"""Live trading scoreboard for the current PREREG_VERSION: the C-arm rank-gate
trades, scored with the same bracket engine as the experiment.

Runs in a separate process (stats_bot.py) and never writes to the experiment
tables. Closed trades use the stored outcome when the loop has written one
(24h after entry); before that they are evaluated here on closed 1-min bars
(outcomes.evaluate with max_minutes, which reads and writes no bar cache) and
the final result is kept in data/stats_cache.json so each trade costs its
GeckoTerminal calls once."""
import json
import statistics as st
import threading
from datetime import datetime, timezone

from . import books, db, notify, outcomes, sources
from .config import DATA_DIR, PREREG, PREREG_VERSION

CACHE_PATH = DATA_DIR / "stats_cache.json"
STARTING_BANKROLL = 1_000.0
POSITION = PREREG["position_usd"]
BMAX = PREREG["bracket_max_minutes"]
# New evaluations per /stats call. The rest show as "scoring" and are picked
# up on the next call, so one command can't flood GeckoTerminal.
MAX_NEW_EVALS = 12
# Shadow books are scored once per trade, after its 6h window (the longest
# book) plus a liquidity snapshot after that point, and cached.
BOOK_AFTER_MIN = 362
MAX_NEW_BOOKS = 6
PRIMARY_BOOK = f"primary ({BMAX}m hold)"
_lock = threading.Lock()

# This process has its own rate limiter; the experiment loop shares the same
# GeckoTerminal quota, so be twice as polite as the loop is.
sources._MIN_GAP[sources.GT_HOST] = 4.5


def _load_cache() -> dict:
    try:
        d = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        return d if d.get("version") == PREREG_VERSION else {"version": PREREG_VERSION, "trades": {}}
    except (OSError, ValueError):
        return {"version": PREREG_VERSION, "trades": {}}


def _save_cache(d: dict):
    tmp = CACHE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(d), encoding="utf-8")
    tmp.replace(CACHE_PATH)


def _taken(version: str) -> list[dict]:
    rows = db.conn().execute(
        "SELECT c.*, r.report_json, r.t4_frozen FROM candidates c "
        "JOIN reports r ON r.candidate_id=c.id AND r.arm='C' AND r.ok=1 AND r.late=0 "
        "WHERE c.prereg_version=? ORDER BY c.t_decision", (version,)).fetchall()
    return [dict(r) for r in rows if notify.is_trade(json.loads(r["report_json"]))]


def _score(c: dict, cache: dict, budget: list) -> dict:
    """{'state': closed|open|unfilled|scoring, ...} for one taken trade."""
    key = str(c["id"])
    # The loop's stored outcome is authoritative and replaces an early score.
    o = db.conn().execute("SELECT outcome_json FROM outcomes WHERE candidate_id=?",
                          (c["id"],)).fetchone()
    ob = (json.loads(o[0]).get("bracket") or {}) if o else {}
    if ob.get("result") in ("target", "stop", "time"):
        cache["trades"][key] = dict(_closed(c, ob), liq_ok=True)
        return cache["trades"][key]
    if key in cache["trades"] and (cache["trades"][key]["state"] != "closed"
                                   or cache["trades"][key].get("liq_ok")):
        return cache["trades"][key]
    e = db.conn().execute("SELECT ok, liquidity_usd FROM entries WHERE candidate_id=?",
                          (c["id"],)).fetchone()
    if e is None:
        return {"state": "open", "note": "waiting for entry"}
    if not e["ok"] or not e["liquidity_usd"]:
        res = {"state": "unfilled"}
        cache["trades"][key] = res
        return res
    if ob.get("result") == "unfilled":
        cache["trades"][key] = {"state": "unfilled"}
        return cache["trades"][key]
    mins = int((db.now_utc() - db.parse_iso(c["t_decision"])).total_seconds() // 60) - 1
    if mins < 1:
        return {"state": "open", "minutes": 0}
    if budget[0] <= 0:
        return {"state": "scoring"}
    budget[0] -= 1
    out = outcomes.evaluate(c, max_minutes=min(mins, BMAX))
    b = out.get("bracket") or {}
    if out.get("unfilled"):
        cache["trades"][key] = {"state": "unfilled"}
        return cache["trades"][key]
    if b.get("result") in ("target", "stop") or (b.get("result") == "time" and mins >= BMAX):
        res = _closed(c, b)
        # The exit is valued at the pool liquidity observed around it. Until a
        # snapshot exists at or after the exit, that value is a placeholder
        # (a missing snapshot reads as an empty pool, i.e. -100%), so the
        # result is shown but not kept; the next call re-scores it.
        if _liq_seen_after(c["id"], res["exit_ts"]):
            cache["trades"][key] = dict(res, liq_ok=True)
        else:
            cache["trades"].pop(key, None)
        return res
    return {"state": "open", "minutes": mins, "mfe": out.get("mfe_6h")}


def _books(c: dict, cache: dict, budget: list) -> dict | None:
    key = str(c["id"])
    got = cache.setdefault("books", {}).get(key)
    if got is not None:
        return got
    td = db.parse_iso(c["t_decision"]).timestamp()
    if (db.now_utc().timestamp() - td) / 60 < BOOK_AFTER_MIN or not _liq_seen_after(c["id"], td + 360 * 60):
        return None
    if budget[0] <= 0:
        return None
    budget[0] -= 1
    t = books.trade_from_db(c, outcomes.fetch_bars(c, cache=False))
    if t is None:
        cache["books"][key] = {}
        return {}
    res = {PRIMARY_BOOK: round(books.sim(t, **{**books.primary_params(),
                                                "levels": [tuple(x) for x in books.primary_params()["levels"]]}), 4)}
    res.update(books.run_books(c, json.loads(c["report_json"]), c["t4_frozen"], t))
    cache["books"][key] = res
    return res


def _liq_seen_after(cid: int, ts: float) -> bool:
    r = db.conn().execute("SELECT 1 FROM liquidity_obs WHERE candidate_id=? AND t>=? LIMIT 1",
                          (cid, db.iso(datetime.fromtimestamp(ts, timezone.utc)))).fetchone()
    return r is not None


def _closed(c: dict, b: dict) -> dict:
    exit_ts = db.parse_iso(c["t_decision"]).timestamp() + (b.get("exit_min") or BMAX) * 60
    return {"state": "closed", "result": b["result"], "net": b["net"],
            "exit_min": b.get("exit_min"), "exit_ts": exit_ts, "symbol": c.get("symbol"),
            "id": c["id"]}


def compute(version: str | None = None) -> dict:
    version = version or PREREG_VERSION
    with _lock:
        db.init()
        cache = _load_cache()
        taken = _taken(version)
        budget = [MAX_NEW_EVALS]
        scored = [(c, _score(c, cache, budget)) for c in taken]
        bbudget = [MAX_NEW_BOOKS]
        book_rows = [b for b in (_books(c, cache, bbudget) for c in taken) if b]
        _save_cache(cache)
    names = [PRIMARY_BOOK] + list(PREREG.get("shadow_books", {}))
    book_nets = {n: [r[n] for r in book_rows if r.get(n) is not None] for n in names}
    q = lambda sql, *a: db.conn().execute(sql, a).fetchone()[0]
    start = q("SELECT MIN(t1_detected) FROM candidates WHERE prereg_version=?", version)
    closed = sorted((s for _, s in scored if s["state"] == "closed"), key=lambda s: s["exit_ts"])
    return {
        "version": version,
        "start": start,
        "hours": ((db.now_utc() - db.parse_iso(start)).total_seconds() / 3600) if start else 0,
        "candidates": q("SELECT COUNT(*) FROM candidates WHERE prereg_version=?", version),
        "researched": q("SELECT COUNT(*) FROM candidates WHERE prereg_version=? "
                        "AND research_status='selected'", version),
        "no_budget": q("SELECT COUNT(*) FROM candidates WHERE prereg_version=? "
                       "AND research_status='no_budget'", version),
        "taken": len(taken),
        "closed": closed,
        "open": [dict(s, symbol=c.get("symbol"), id=c["id"]) for c, s in scored
                 if s["state"] in ("open", "scoring")],
        "unfilled": sum(s["state"] == "unfilled" for _, s in scored),
        "excluded": q("SELECT COUNT(*) FROM exclusions x JOIN candidates c ON c.id=x.candidate_id "
                      "WHERE c.prereg_version=?", version),
        "books": book_nets,
        "books_n": len(book_rows),
    }


def summarize(d: dict) -> dict:
    """Numbers only; formatting lives in the bot."""
    nets = [s["net"] for s in d["closed"]]
    pnl = [n * POSITION for n in nets]
    wins = [x for x in pnl if x > 0]
    losses = [x for x in pnl if x <= 0]
    eq, peak, dd = 0.0, 0.0, 0.0
    for x in pnl:
        eq += x
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    streak, kind = 0, None
    for x in reversed(pnl):
        k = "W" if x > 0 else "L"
        if kind in (None, k):
            kind, streak = k, streak + 1
        else:
            break
    n = len(nets)
    total = sum(pnl)
    return {
        "n": n,
        "wins": len(wins), "losses": len(losses),
        "tp": sum(s["result"] == "target" for s in d["closed"]),
        "sl": sum(s["result"] == "stop" for s in d["closed"]),
        "time": sum(s["result"] == "time" for s in d["closed"]),
        "win_rate": len(wins) / n if n else None,
        "tp_rate": sum(s["result"] == "target" for s in d["closed"]) / n if n else None,
        "ev_pct": st.mean(nets) if n else None,
        "ev_usd": st.mean(pnl) if n else None,
        "median_pct": st.median(nets) if n else None,
        "total_usd": total,
        "bankroll_end": STARTING_BANKROLL + total,
        "bankroll_pct": total / STARTING_BANKROLL,
        "deployed_pct": total / (n * POSITION) if n else None,
        "avg_win": st.mean(wins) if wins else None,
        "avg_loss": st.mean(losses) if losses else None,
        "profit_factor": (sum(wins) / -sum(losses)) if losses and sum(losses) < 0 else None,
        "best": max(d["closed"], key=lambda s: s["net"]) if n else None,
        "worst": min(d["closed"], key=lambda s: s["net"]) if n else None,
        "max_dd": dd,
        "streak": f"{streak}{kind}" if kind else "-",
    }
