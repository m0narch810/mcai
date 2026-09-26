"""Runs Claude as a headless research agent (`claude -p`) on the user's Claude
subscription, and freezes every report the moment it returns.

Arms:
  C  - full research: evidence packet + web search/fetch
  C2 - independent rerun of C started at the same moment (self-consistency)
  P  - packet only, no internet (isolates what browsing adds)
  S  - skeptic, runs when the pre-registered trigger fires on C
"""
import json
import shutil
import subprocess
import time
from datetime import timedelta
from pathlib import Path

from . import db, schemas
from .config import DATA_DIR, PREREG, PROMPT_DIR, RAW_DIR, RUNTIME
from .packet import load as load_packet

AGENT_CWD = DATA_DIR / "agent_cwd"   # empty dir so no project files leak in
WEB_TOOLS = "WebSearch,WebFetch"
SYSTEM_PROMPT = ("You are a careful, skeptical research analyst. You follow the task "
                 "instructions exactly and return the required structured output.")


# ------------------------------------------------------------------ budget
def spent(hours: float) -> float:
    since = db.iso(db.now_utc() - timedelta(hours=hours))
    r = db.conn().execute("SELECT COALESCE(SUM(cost_usd),0) FROM usage WHERE t>=?",
                          (since,)).fetchone()
    return r[0]


def budget_ok(need: float | None = None) -> bool:
    """Daily and 5h caps, plus an hourly pace.

    The pace matters for the experiment, not just the wallet. Which candidates
    get researched is supposed to be decided by the random draw alone, but a
    candidate that passes the draw and then finds the budget empty is recorded
    `no_budget` - a non-random exclusion. Without pacing, the whole day's
    budget burns in the first hours and every later candidate is excluded that
    way, which correlates exclusion with time of day and with how busy the
    market is. Spreading the spend keeps `no_budget` rare and scattered.
    """
    need = RUNTIME["reserve_per_candidate_usd"] if need is None else need
    hourly = (RUNTIME["daily_budget_usd"] / RUNTIME["active_hours_per_day"]
              * RUNTIME["hourly_burst_multiple"])
    return (spent(24) + need <= RUNTIME["daily_budget_usd"]
            and spent(5) + need <= RUNTIME["window5h_budget_usd"]
            and spent(1) + need <= hourly)


def _record_ratelimit(info: dict | None):
    win = ((info or {}).get("unifiedWindows") or {}).get("five_hour") or {}
    if win.get("utilization") is None or not win.get("resetsAt"):
        return
    week = ((info.get("unifiedWindows") or {}).get("seven_day") or {}).get("utilization")
    db.conn().execute("INSERT INTO ratelimit VALUES (?,?,?,?)",
                      (db.iso(db.now_utc()), int(win["resetsAt"]),
                       float(win["utilization"]), week))


def session_share() -> float:
    """How much of the current 5h session limit has been used since the bot's
    first run in this window (0..1). 0 when there is no reading for a live window."""
    last = db.conn().execute(
        "SELECT resets_at, util_5h FROM ratelimit ORDER BY rowid DESC LIMIT 1").fetchone()
    if not last or last[0] <= db.now_utc().timestamp():
        return 0.0
    base = db.conn().execute(
        "SELECT util_5h FROM ratelimit WHERE resets_at=? ORDER BY rowid LIMIT 1",
        (last[0],)).fetchone()[0]
    return max(0.0, last[1] - base)


def weekly_util() -> float | None:
    r = db.conn().execute("SELECT util_7d FROM ratelimit ORDER BY rowid DESC LIMIT 1").fetchone()
    return r[0] if r and r[0] is not None else None


def session_ok() -> bool:
    week = weekly_util()
    return (session_share() < RUNTIME["session_share_cap"]
            and (week is None or week < RUNTIME.get("weekly_util_cap", 1.0)))


def pause_reason() -> str | None:
    """Which cap is stopping new research right now, or None if research can
    start. Mirrors budget_ok() + session_ok(); used only for notifications."""
    need = RUNTIME["reserve_per_candidate_usd"]
    hourly = (RUNTIME["daily_budget_usd"] / RUNTIME["active_hours_per_day"]
              * RUNTIME["hourly_burst_multiple"])
    if spent(24) + need > RUNTIME["daily_budget_usd"]:
        return f"daily budget (${spent(24):.2f} / ${RUNTIME['daily_budget_usd']:.0f} in 24h)"
    if spent(5) + need > RUNTIME["window5h_budget_usd"]:
        return f"5h budget (${spent(5):.2f} / ${RUNTIME['window5h_budget_usd']:.0f} in 5h)"
    if spent(1) + need > hourly:
        return f"hourly pace (${spent(1):.2f} / ${hourly:.2f} in 1h)"
    week = weekly_util()
    if week is not None and week >= RUNTIME.get("weekly_util_cap", 1.0):
        return f"Claude weekly usage ({week:.0%} / {RUNTIME['weekly_util_cap']:.0%} cap)"
    if not session_ok():
        return (f"Claude session share ({session_share():.0%} / "
                f"{RUNTIME['session_share_cap']:.0%} cap)")
    return None


def _meter(purpose: str, cid: int | None, cost: float):
    db.conn().execute("INSERT INTO usage VALUES (?,?,?,?)",
                      (db.iso(db.now_utc()), purpose, cid, cost or 0.0))


# ------------------------------------------------------------------ claude
def _claude_path() -> str:
    p = shutil.which("claude") or str(Path.home() / ".local" / "bin" / "claude.exe")
    return p


def _extract_json(text: str):
    """Fallback: last {...} block in free text."""
    if not text:
        return None
    for start in [i for i, ch in enumerate(text) if ch == "{"][::-1]:
        try:
            return json.loads(text[start:text.rindex("}") + 1])
        except (ValueError, json.JSONDecodeError):
            continue
    return None


def run_claude(prompt: str, schema: dict, tools: str, timeout_s: float,
               budget_usd: float, tag: str) -> dict:
    """One headless run. Retries transient API errors while time remains."""
    AGENT_CWD.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_s
    raw_path = RAW_DIR / f"{tag}_{int(time.time())}.json"
    attempt, last_err, total_cost = 0, None, 0.0
    while time.monotonic() < deadline - 20 and attempt < 3:
        attempt += 1
        cmd = [_claude_path(), "-p",
               "--model", RUNTIME["claude_model"],
               "--system-prompt", SYSTEM_PROMPT,
               "--tools", tools,
               "--setting-sources", "",
               "--strict-mcp-config",
               "--no-session-persistence",
               "--output-format", "stream-json", "--verbose",
               "--max-budget-usd", f"{budget_usd:.2f}",
               "--json-schema", json.dumps(schema)]
        if tools:
            cmd += ["--allowedTools", tools]
        try:
            proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", cwd=AGENT_CWD,
                                  timeout=max(30, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            last_err = "timeout"
            break
        out, rate_info = None, None
        for line in proc.stdout.splitlines():
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("type") == "rate_limit_event":
                rate_info = ev.get("rate_limit_info")
            elif ev.get("type") == "result":
                out = ev
        _record_ratelimit(rate_info)
        if out is None:
            last_err = f"unparseable CLI output rc={proc.returncode}: {proc.stderr[-400:]}"
            time.sleep(10)
            continue
        raw_path.write_text(json.dumps(out, indent=1), encoding="utf-8")
        total_cost += out.get("total_cost_usd") or 0.0
        if out.get("is_error") and out.get("api_error_status") in (429, 500, 502, 503, 529):
            last_err = f"api_error {out.get('api_error_status')}"
            time.sleep(20 * attempt)
            continue
        obj = out.get("structured_output") or _extract_json(out.get("result") or "")
        if obj is None:
            return {"ok": False, "error": f"no structured output: {str(out.get('result'))[:300]}",
                    "cost": total_cost, "raw_path": str(raw_path)}
        errs = schemas.validate(obj, schema)
        if errs:
            return {"ok": False, "error": "schema: " + "; ".join(errs[:8]), "obj": obj,
                    "cost": total_cost, "raw_path": str(raw_path)}
        return {"ok": True, "obj": obj, "cost": total_cost, "raw_path": str(raw_path)}
    return {"ok": False, "error": last_err or "no time left", "cost": total_cost,
            "raw_path": str(raw_path) if raw_path.exists() else None}


def _prompt(name: str, **kw) -> str:
    text = (PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8")
    for k, v in kw.items():
        text = text.replace("{" + k + "}", v)
    return text


# ------------------------------------------------------------------ freeze
def freeze(cid: int, arm: str, t3: str, t_decision: str, res: dict):
    t4 = db.iso(db.now_utc())
    late = int(t4 > t_decision)
    body = {"candidate_id": cid, "arm": arm, "t3": t3, "t4": t4, "late": late,
            "ok": int(res["ok"]), "report": res.get("obj") if res["ok"] else None,
            "error": res.get("error")}
    if (res["ok"] and arm in ("C", "P") and res["obj"].get("p_runner") is not None
            and PREREG.get("trade_rank_quantile") is not None):
        # The rank gate is decided here, from strictly earlier reports, and
        # frozen with the report so nothing downstream can recompute it later.
        res["obj"]["_gate"] = rank_gate(arm, res["obj"].get("p_runner"), t4, bool(late))
        body["report"] = res["obj"]
    h = db.sha(body)
    db.conn().execute(
        "INSERT INTO reports (candidate_id, arm, t3_started, t4_frozen, late, ok,"
        " report_json, error, cost_usd, raw_path, sha256) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (cid, arm, t3, t4, late, int(res["ok"]),
         json.dumps(res["obj"]) if res["ok"] else None, res.get("error"),
         res.get("cost"), res.get("raw_path"), h))
    db.ledger("report", body | {"sha256": h})
    _meter(f"research_{arm}", cid, res.get("cost") or 0.0)
    status = "OK" if res["ok"] else f"FAILED ({res.get('error')})"
    obj = res.get("obj") or {}
    view = obj.get("continuation_view", "")
    if obj.get("p_runner") is not None:
        view += f" p_runner={obj['p_runner']} p_rug={obj.get('p_rug')}"
    db.log("info", f"report #{cid} arm={arm} {status} {view} late={late} "
                   f"cost=${res.get('cost') or 0:.2f}")
    if res["ok"] and arm in ("C", "S"):
        from . import notify
        c = dict(db.conn().execute("SELECT * FROM candidates WHERE id=?", (cid,)).fetchone())
        notify.report_frozen(c, arm, res["obj"], bool(late), res.get("cost") or 0.0)


def rank_gate(arm: str, score, t4: str, late: bool) -> dict:
    """Trade gate: take the position iff score >= the trade_rank_quantile of
    this arm's last trade_rank_window on-time p_runner values frozen before
    t4. Scores only - no outcome can reach the gate. Nearest-rank quantile.

    v6.1: only reports from the SAME version (same model + prompts) count.
    Until then the window spanned versions, and after the Opus->Sonnet
    switch Sonnet's scores (median 14) were ranked against Opus's (median 8,
    p75 12): 3 of the first 5 Sonnet reports cleared the "top quarter" bar.
    Below trade_rank_min_prior same-version reports there is no trade."""
    from .config import PREREG_VERSION
    n = PREREG["trade_rank_window"]
    prior = [r[0] for r in db.conn().execute(
        "SELECT json_extract(r.report_json,'$.p_runner') FROM reports r "
        "JOIN candidates c ON c.id=r.candidate_id AND c.prereg_version=? "
        "WHERE r.arm=? AND r.ok=1 AND r.late=0 AND r.t4_frozen<? "
        "AND json_extract(r.report_json,'$.p_runner') IS NOT NULL "
        "ORDER BY r.t4_frozen DESC LIMIT ?", (PREREG_VERSION, arm, t4, n))]
    if score is None or late or len(prior) < PREREG.get("trade_rank_min_prior", 1):
        return {"take": False, "threshold": None, "n_prior": len(prior)}
    srt = sorted(prior)
    k = min(len(srt) - 1, max(0, int(-(-PREREG["trade_rank_quantile"] * len(srt) // 1)) - 1))
    thr = srt[k]
    return {"take": score >= thr, "threshold": thr, "n_prior": len(prior)}


def skeptic_triggered(report: dict) -> bool:
    """Pre-registered: run the skeptic when evidence conflicts, connection is
    weak, structure is poor, promotion is suspected, confidence is low, or the
    candidate ranks highly."""
    return (report["token_connection"]["assessment"] in ("none", "weak", "unclear")
            or report["authenticity"]["assessment"] in ("strong_promotional", "coordinated")
            or report["market_feasibility"]["assessment"] == "poor"
            or report["research_confidence"] == "low"
            or report["continuation_view"] in ("continue", "strong_continue"))


# ------------------------------------------------------------------ pipeline
def _remaining_s(t_decision: str) -> float:
    return (db.parse_iso(t_decision) - db.now_utc()).total_seconds()


def research_candidate(c: dict):
    """Run all arms for one selected candidate. Called from a worker thread."""
    from concurrent.futures import ThreadPoolExecutor

    cid, td = c["id"], c["t_decision"]
    from . import rugguard
    if rugguard.excluded(cid):
        return
    packet = load_packet(cid)
    if packet is None:
        db.log("warn", f"#{cid} no packet; research skipped")
        return
    pj = json.dumps(packet, indent=1)
    now_s = db.iso(db.now_utc())
    limit = min(PREREG["research_timeout_minutes"] * 60, _remaining_s(td))
    if limit < 90:
        db.log("warn", f"#{cid} under 90s left before decision; research skipped")
        return
    per_run = RUNTIME["per_run_budget_usd"]
    if not budget_ok():
        db.log("warn", f"#{cid} budget exhausted by the time research started; skipped")
        return
    arms = {"C": ("research", WEB_TOOLS, per_run)}
    if PREREG.get("packet_only_arm", True):
        arms["P"] = ("packet_only", "", 0.75)
    rerun_reserve = 2 * RUNTIME["reserve_per_candidate_usd"]
    if c["rerun_draw"] < PREREG["consistency_rerun_prob"] and budget_ok(rerun_reserve):
        arms["C2"] = ("research", WEB_TOOLS, per_run)
    t3 = db.iso(db.now_utc())
    prompts = {a: _prompt(p, packet=pj, now_utc=now_s, contract=c["token"],
                          symbol=c.get("symbol") or "")
               for a, (p, _, _) in arms.items()}
    with ThreadPoolExecutor(max_workers=len(arms)) as ex:
        futs = {a: ex.submit(run_claude, prompts[a], schemas.REPORT, tools, limit, b,
                             f"c{cid}_{a}")
                for a, (_, tools, b) in arms.items()}
        results = {a: f.result() for a, f in futs.items()}
    for a, res in results.items():
        freeze(cid, a, t3, td, res)

    c_res = results["C"]
    if not PREREG.get("skeptic_enabled", True):
        return
    if not (c_res["ok"] and skeptic_triggered(c_res["obj"])):
        return
    left = _remaining_s(td)
    if left < 150 or not budget_ok(1.5):
        db.log("info", f"#{cid} skeptic triggered but skipped (time {left:.0f}s / budget)")
        return
    t3s = db.iso(db.now_utc())
    res = run_claude(_prompt("skeptic", packet=pj, now_utc=db.iso(db.now_utc())),
                     schemas.SKEPTIC, WEB_TOOLS, left - 15, 1.5, f"c{cid}_S")
    freeze(cid, "S", t3s, td, res)


def postmortem(cid: int, outcome: dict):
    r = db.conn().execute("SELECT report_json FROM reports WHERE candidate_id=? AND arm='C'"
                          " AND ok=1", (cid,)).fetchone()
    if not r:
        return
    prompt = _prompt("postmortem", packet=json.dumps(load_packet(cid), indent=1),
                     report=r[0], outcome=json.dumps(outcome, indent=1))
    res = run_claude(prompt, schemas.POSTMORTEM, "", 600, 1.0, f"c{cid}_PM")
    db.conn().execute("INSERT OR REPLACE INTO postmortems VALUES (?,?,?,?,?,?,?)",
                      (cid, db.iso(db.now_utc()), int(res["ok"]),
                       json.dumps(res.get("obj")) if res["ok"] else None,
                       res.get("error"), res.get("cost"), res.get("raw_path")))
    _meter("postmortem", cid, res.get("cost") or 0.0)
    db.log("info", f"postmortem #{cid} {'OK' if res['ok'] else res.get('error')}")
