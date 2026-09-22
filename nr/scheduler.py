"""Main loop: detect -> packet -> research (async) -> entry at T_D ->
liquidity tracking -> outcome at T_D + 24h -> post-mortem."""
import json
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from . import db, detector, notify, outcomes, packet, research
from .config import PREREG, PREREG_VERSION, RUNTIME

MISSED_ENTRY_GRACE_S = 180
H_MAX = max(PREREG["horizons_minutes"])


def _rows(sql, *args):
    return [dict(r) for r in db.conn().execute(sql, args)]


def _safe(fn, *a):
    try:
        return fn(*a)
    except Exception as e:  # keep the loop alive; the error is logged
        # The logging call must itself be guarded. On 2026-09-22 a token
        # whose symbol was outside cp1252 raised inside take_entry, and
        # db.log then raised the SAME error again while formatting the
        # report - inside this handler, where nothing catches it. It escaped
        # _safe, escaped _entry_loop's while-loop and killed the entries
        # thread silently. The main loop kept detecting, so everything
        # looked healthy while 31 candidates passed T_D with no paper entry
        # and became permanent exclusions. Failing to log must never be able
        # to end a loop.
        try:
            db.log("error", f"{fn.__name__}: {e!r}" + chr(10)
                   + traceback.format_exc(limit=3))
        except Exception:
            try:
                db.log("error", f"{fn.__name__}: unloggable error "
                                f"({type(e).__name__})")
            except Exception:
                pass


def _research_job(c):
    try:
        research.research_candidate(c)
    except Exception as e:
        db.log("error", f"research #{c['id']}: {e!r}\n{traceback.format_exc(limit=4)}")


def step_detect(pool: ThreadPoolExecutor):
    for cid in detector.detect_once(
            lambda: research.budget_ok() and research.session_ok()) or []:
        c = _rows("SELECT * FROM candidates WHERE id=?", cid)[0]
        if _safe(packet.collect, c) is None:
            continue
        if c["research_status"] == "selected":
            pool.submit(_research_job, c)


def step_entries():
    now = db.now_utc()
    due = _rows("SELECT c.* FROM candidates c LEFT JOIN entries e ON e.candidate_id=c.id "
                "WHERE e.candidate_id IS NULL AND c.t_decision<=?", db.iso(now))
    for c in due:
        late_s = (now - db.parse_iso(c["t_decision"])).total_seconds()
        if late_s > MISSED_ENTRY_GRACE_S:
            # System was offline at T_D. A late entry would be a different
            # experiment, so record the miss and exclude the candidate.
            db.conn().execute("INSERT INTO entries VALUES (?,?,?,?,?,?,?,?)",
                              (c["id"], db.iso(now), None, None, None, None, 0,
                               "MISSED: system offline at T_D"))
            db.log("warn", f"entry #{c['id']} missed by {late_s:.0f}s: excluded")
        else:
            _safe(outcomes.take_entry, c)


_last_snap = 0.0


def step_liquidity():
    global _last_snap
    if time.monotonic() - _last_snap < RUNTIME["liquidity_snapshot_minutes"] * 60:
        return
    _last_snap = time.monotonic()
    now = db.now_utc()
    horizon_start = db.iso(now - timedelta(minutes=H_MAX + 30))
    open_ = _rows("SELECT c.* FROM candidates c JOIN entries e ON e.candidate_id=c.id "
                  "WHERE e.ok=1 AND c.t_decision>=?", horizon_start)
    if open_:
        _safe(outcomes.snapshot_liquidity, open_)


def step_outcomes():
    cutoff = db.iso(db.now_utc() - timedelta(minutes=H_MAX + 15))
    due = _rows("SELECT c.* FROM candidates c JOIN entries e ON e.candidate_id=c.id "
                "LEFT JOIN outcomes o ON o.candidate_id=c.id "
                "WHERE o.candidate_id IS NULL AND c.t_decision<=? "
                "AND (e.ok=1 OR e.note NOT LIKE 'MISSED%')", cutoff)
    for c in due[:10]:   # GeckoTerminal rate limit: spread the work
        out = _safe(outcomes.evaluate, c)
        if out is None:
            continue
        outcomes.store(c["id"], out)
        db.log("info", f"outcome #{c['id']} {c.get('symbol')} net24h={out['returns'].get(str(H_MAX))}")
        rep = db.conn().execute("SELECT report_json FROM reports WHERE candidate_id=? "
                                "AND arm='C' AND ok=1", (c["id"],)).fetchone()
        if rep:   # controls are summarised in the daily digest instead
            notify.outcome(c, out, json.loads(rep[0])["continuation_view"])


PRIMARY = PREREG["primary_horizon_minutes"]


def step_interim():
    """Post the primary-horizon (6h) result for researched candidates as soon
    as it has fully elapsed. Display only: nothing is stored or cached."""
    db.conn().execute("CREATE TABLE IF NOT EXISTS interim_sent (candidate_id INTEGER PRIMARY KEY)")
    lo = db.iso(db.now_utc() - timedelta(minutes=H_MAX))
    hi = db.iso(db.now_utc() - timedelta(minutes=PRIMARY + 10))
    due = _rows("SELECT c.*, r.report_json FROM candidates c "
                "JOIN entries e ON e.candidate_id=c.id AND e.ok=1 "
                "JOIN reports r ON r.candidate_id=c.id AND r.arm='C' AND r.ok=1 "
                "LEFT JOIN interim_sent s ON s.candidate_id=c.id "
                "WHERE s.candidate_id IS NULL AND c.t_decision BETWEEN ? AND ?", lo, hi)
    for c in due[:5]:
        out = outcomes.evaluate(c, max_minutes=PRIMARY)
        db.conn().execute("INSERT OR IGNORE INTO interim_sent VALUES (?)", (c["id"],))
        notify.interim(c, out, json.loads(c["report_json"])["continuation_view"])
        db.log("info", f"interim #{c['id']} {c.get('symbol')} net6h={out['returns'].get(str(PRIMARY))}")


def step_quiet():
    """During a dry spell, say so. Silence used to mean either 'the rule did
    not fire' or 'the process is dead', with no way to tell them apart."""
    last = db.conn().execute("SELECT MAX(t1_detected) FROM candidates").fetchone()[0]
    if not last:
        return
    hours = (db.now_utc() - db.parse_iso(last)).total_seconds() / 3600
    if hours >= RUNTIME.get("quiet_heartbeat_hours", 3):
        notify.quiet(hours, detector.funnel_snapshot())


_pm_inflight: set[int] = set()


def _pm_job(cid, outcome):
    try:
        research.postmortem(cid, outcome)
    except Exception as e:
        db.log("error", f"postmortem #{cid}: {e!r}")
    finally:
        _pm_inflight.discard(cid)


def step_postmortems(pool: ThreadPoolExecutor):
    # Post-mortems only use leftover budget: keep one research run in reserve.
    if _pm_inflight or not RUNTIME["postmortem_enabled"]:
        return
    if (not research.budget_ok(1.0 + RUNTIME["reserve_per_candidate_usd"])
            or not research.session_ok()):
        return
    due = _rows("SELECT o.candidate_id, o.outcome_json FROM outcomes o "
                "JOIN reports r ON r.candidate_id=o.candidate_id AND r.arm='C' AND r.ok=1 "
                "LEFT JOIN postmortems p ON p.candidate_id=o.candidate_id "
                "WHERE p.candidate_id IS NULL LIMIT 1")
    for r in due:
        _pm_inflight.add(r["candidate_id"])
        pool.submit(_pm_job, r["candidate_id"], json.loads(r["outcome_json"]))


DIGEST_HOUR_UTC = 13   # ~9am US Eastern
_last_digest_day = None


def step_digest():
    global _last_digest_day
    now = db.now_utc()
    if now.hour != DIGEST_HOUR_UTC or _last_digest_day == now.date():
        return
    _last_digest_day = now.date()
    import statistics as st
    since = db.iso(now - timedelta(hours=24))
    q = lambda sql, *a: db.conn().execute(sql, a).fetchone()[0]
    rets = {"selected": [], "control": []}
    for r in _rows("SELECT c.research_status, o.outcome_json FROM outcomes o "
                   "JOIN candidates c ON c.id=o.candidate_id WHERE c.prereg_version=?",
                   PREREG_VERSION):
        v = json.loads(r["outcome_json"]).get("returns", {}).get(str(PRIMARY))
        if v is not None:
            rets["selected" if r["research_status"] == "selected" else "control"].append(v)
    med = lambda x: f"{st.median(x):+.1%} (n={len(x)})" if x else "n/a"
    notify.digest({
        "New candidates (24h)": q("SELECT COUNT(*) FROM candidates WHERE t1_detected>=?", since),
        "Researched (24h)": q("SELECT COUNT(*) FROM reports WHERE arm='C' AND t4_frozen>=?", since),
        "Claude verdicts (24h)": ", ".join(
            f"{r[0].replace('_', ' ')} {r[1]}" for r in db.conn().execute(
                "SELECT json_extract(report_json,'$.continuation_view'), COUNT(*) FROM reports "
                "WHERE arm='C' AND ok=1 AND t4_frozen>=? GROUP BY 1 ORDER BY 2 DESC", (since,))) or "none",
        "Late/failed (24h)": q("SELECT COUNT(*) FROM reports WHERE (late=1 OR ok=0) AND t4_frozen>=?", since),
        "Researched since last yes": q(
            "SELECT COUNT(*) FROM reports WHERE arm='C' AND ok=1 AND id > COALESCE((SELECT MAX(id) "
            "FROM reports WHERE arm='C' AND ok=1 AND json_extract(report_json,'$.continuation_view') "
            "IN ('continue','strong_continue')), 0)"),
        "Outcomes total": q("SELECT COUNT(*) FROM outcomes"),
        "Median 6h, researched": med(rets["selected"]),
        "Median 6h, control": med(rets["control"]),
        "Budget used (24h)": f"${research.spent(24):.2f} / ${RUNTIME['daily_budget_usd']:.0f}",
        "Session used by bot (5h)": f"{research.session_share():.0%} / "
                                    f"{RUNTIME['session_share_cap']:.0%}",
        "Errors (24h)": q("SELECT COUNT(*) FROM events WHERE level='error' AND t>=?", since),
        "Funnel (this process)": _funnel_line(),
        "Uptime coverage (24h)": f"{db.coverage(24):.0%} of the day observed",
        "Version": PREREG_VERSION,
    })


def _funnel_line() -> str:
    f = detector.funnel_snapshot()
    if not f.get("cycles"):
        return "no cycles yet"
    top = sorted(((k.split(":", 1)[1], v) for k, v in f.items()
                  if k.startswith("sole_blocker:")), key=lambda x: -x[1])[:3]
    return (f"{f['cycles']} cycles, {f.get('nominated', 0)} nominated, "
            f"{f.get('passed', 0)} passed, {f.get('passed_but_deduped', 0)} deduped · "
            f"closest misses: " + (", ".join(f"{k} ({v})" for k, v in top) or "none"))


def _entry_loop():
    """Entries run on their own 15s clock so a slow discovery cycle can
    never push a paper entry past T_D."""
    while True:
        try:
            _safe(step_entries)
        except BaseException:
            # Belt and braces: this thread is the only thing between a
            # candidate and a permanent MISSED exclusion. It does not get to
            # die, whatever happens underneath it.
            pass
        time.sleep(15)


def run_forever():
    import signal
    # Never die from a stray Ctrl+C / Ctrl+Break aimed at another console.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, signal.SIG_IGN)
    db.init()
    from .sources import tls_selfcheck
    tls_err = tls_selfcheck()
    if tls_err:
        db.log("error", f"TLS self-check failed: {tls_err}. Refusing to run.")
        raise SystemExit(2)
    db.log("info", f"starting: prereg={PREREG_VERSION} budget=${RUNTIME['daily_budget_usd']}/day")
    notify.send("▶️ Experiment loop started", f"prereg `{PREREG_VERSION}` · budget "
                f"${RUNTIME['daily_budget_usd']:.0f}/day. If you see this unexpectedly, "
                "the loop restarted after a crash or reboot.", notify.GREY)
    pool = ThreadPoolExecutor(max_workers=RUNTIME["max_concurrent_research"])
    pm_pool = ThreadPoolExecutor(max_workers=1)
    threading.Thread(target=_entry_loop, daemon=True, name="entries").start()
    # Resume research interrupted by a restart, while it can still finish
    # before T_D (research_candidate itself refuses if too little time is left).
    for c in _rows("SELECT c.* FROM candidates c JOIN packets p ON p.candidate_id=c.id "
                   "LEFT JOIN reports r ON r.candidate_id=c.id AND r.arm='C' "
                   "WHERE c.research_status='selected' AND r.id IS NULL AND c.t_decision>?",
                   db.iso(db.now_utc() + timedelta(minutes=3))):
        db.log("info", f"resuming interrupted research for #{c['id']}")
        pool.submit(_research_job, c)
    while True:
        t0 = time.monotonic()
        _safe(db.beat)
        _safe(step_detect, pool)
        _safe(step_liquidity)
        _safe(step_outcomes)
        _safe(step_interim)
        _safe(step_postmortems, pm_pool)
        _safe(step_quiet)
        _safe(lambda: notify.research_paused(research.pause_reason()))
        _safe(step_digest)
        time.sleep(max(5, RUNTIME["poll_seconds"] - (time.monotonic() - t0)))
