"""Objective candidate detection. A token enters the pool if and only if its
deepest SOL/USDC pair meets every condition in PREREG. No judgment involved."""
import json
import random
from collections import Counter
from datetime import timedelta

from . import db, sources
from .config import PREREG, PREREG_VERSION

P = PREREG


def evaluate_rule(pair: dict, now_ms: int) -> tuple[bool, dict]:
    """Return (passes, trigger_numbers). Uses only fields DexScreener computes
    from trades up to the moment of the call.

    Universe rule v2 (see PREREG): both volume tests are normalised by the
    market - pool depth, and the token's own last hour - never by its average
    volume since launch. That v1 denominator scored a token 40 minutes into a
    vertical launch below 1.0 and a dormant year-old token at 30x. The v1
    ratios are still recorded as descriptive numbers; they gate nothing."""
    vol = pair.get("volume") or {}
    tx = pair.get("txns") or {}
    pc = pair.get("priceChange") or {}
    liq = (pair.get("liquidity") or {}).get("usd") or 0
    mcap = pair.get("marketCap") or pair.get("fdv") or 0
    age_min = (now_ms - (pair.get("pairCreatedAt") or now_ms)) / 60000
    h1 = vol.get("h1") or 0
    m5_rate = (vol.get("m5") or 0) * 12          # last 5 min as an hourly rate
    turnover = h1 / liq if liq else 0.0          # pool depths traded per hour
    m5_vs_h1 = m5_rate / h1 if h1 else 0.0       # is the move still live?
    # Descriptive only: v1's since-launch hourly baseline, capped at 6h.
    window_h = max(age_min, 1) / 60
    h6_hourly = (vol.get("h6") or 0) / min(6.0, window_h)
    t = {
        "mcap_usd": mcap, "liquidity_usd": liq, "pair_age_min": round(age_min, 1),
        "bonding_curve": bool((pair.get("liquidity") or {}).get("curve")),
        "vol_m5": vol.get("m5"), "vol_h1": h1, "vol_h6": vol.get("h6"),
        "h1_turnover": round(turnover, 3),
        "m5_rate_vs_h1_rate": round(m5_vs_h1, 3),
        "vol_accel_m5_vs_h6": round(m5_rate / h6_hourly, 3) if h6_hourly else None,
        "vol_accel_h1_vs_h6": round(h1 / h6_hourly, 3) if h6_hourly else None,
        "h1_buys": (tx.get("h1") or {}).get("buys"),
        "h1_sells": (tx.get("h1") or {}).get("sells"),
        "price_change_m5": pc.get("m5"), "price_change_h1": pc.get("h1"),
        "price_change_h6": pc.get("h6"), "price_usd": sources.f(pair.get("priceUsd")),
    }
    checks = {
        "mcap_min": mcap >= P["mcap_min_usd"],
        "mcap_max": mcap <= P["mcap_max_usd"],
        "liquidity_min": liq >= P["liquidity_min_usd"],
        "pair_age_min": age_min >= P["pair_age_min_minutes"],
        "turnover_min": turnover >= P["h1_turnover_min"],
        "still_live": m5_vs_h1 >= P["m5_rate_vs_h1_rate_min"],
        "buys_min": (t["h1_buys"] or 0) >= P["h1_buys_min"],
        "price_up_h1": (pc.get("h1") or 0) > P["h1_price_change_min_pct"],
    }
    t["failed_checks"] = [k for k, v in checks.items() if not v]
    return all(checks.values()), t


def recently_seen(token: str, since_iso: str) -> bool:
    r = db.conn().execute(
        "SELECT 1 FROM candidates WHERE token=? AND t1_detected>=? LIMIT 1",
        (token, since_iso)).fetchone()
    return r is not None


def detect_once(budget_ok, full: bool = True) -> list[int]:
    """One discovery cycle. Returns ids of newly created candidates.
    budget_ok() -> bool says whether research capacity exists right now."""
    mints = sources.discover_tokens(full)
    pairs_by_mint = sources.dex_pairs(sorted(mints), P["quote_tokens"])
    # T1 is when the numbers that met the rule were read, not when the cycle
    # began: through v5 it was stamped before discovery, ~2 min too early.
    now = db.now_utc()
    now_ms = int(now.timestamp() * 1000)
    since = db.iso(now - timedelta(hours=P["dedup_hours"]))
    created = []
    funnel = Counter({"nominated": len(mints), "with_pairs": len(pairs_by_mint)})
    for mint, pairs in pairs_by_mint.items():
        pair = sources.best_pair(pairs, P["quote_tokens"])
        if not pair:
            funnel["no_sol_usdc_pair"] += 1
            continue
        ok, trig = evaluate_rule(pair, now_ms)
        if not ok:
            funnel["rejected"] += 1
            # Which condition did the work? Only meaningful when it was the
            # sole blocker; that is the number worth watching over time.
            if len(trig["failed_checks"]) == 1:
                funnel["sole_blocker:" + trig["failed_checks"][0]] += 1
            for k in trig["failed_checks"]:
                funnel["failed:" + k] += 1
            continue
        if recently_seen(mint, since):
            funnel["passed_but_deduped"] += 1
            continue
        funnel["passed"] += 1
        draw, rerun = random.random(), random.random()
        if draw >= P["research_sample_prob"]:
            status = "not_sampled"
        elif not budget_ok():
            status = "no_budget"
        else:
            status = "selected"
        t1 = db.iso(now)
        td = db.iso(now + timedelta(minutes=P["decision_delay_minutes"]))
        trig["discovered_pairs"] = len(pairs)
        cur = db.conn().execute(
            "INSERT INTO candidates (prereg_version, token, symbol, name, pair_address,"
            " dex_id, t1_detected, t_decision, trigger_json, research_draw,"
            " research_status, rerun_draw) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (PREREG_VERSION, mint, pair["baseToken"].get("symbol"),
             pair["baseToken"].get("name"), pair["pairAddress"], pair.get("dexId"),
             t1, td, json.dumps(trig), draw, status, rerun))
        cid = cur.lastrowid
        db.ledger("candidate", {"id": cid, "token": mint, "t1": t1, "t_decision": td,
                                "trigger": trig, "research_status": status})
        db.log("info", f"candidate #{cid} {pair['baseToken'].get('symbol')} {mint[:8]}"
                       f" mcap=${trig['mcap_usd']:,.0f} age={trig['pair_age_min']:.0f}m"
                       f" turn={trig['h1_turnover']} live={trig['m5_rate_vs_h1_rate']}"
                       f" -> {status}")
        created.append(cid)
    _record_funnel(funnel)
    return created


# ---------------------------------------------------------------- telemetry
_funnel_total: Counter = Counter()
_funnel_cycles = 0
FUNNEL_LOG_EVERY = 30          # cycles (~30 min at poll_seconds=60)


def _record_funnel(funnel: Counter) -> None:
    """Accumulate per-cycle rejection reasons and log a summary periodically.
    Without this, a run that selects nothing looks identical to a dead run."""
    global _funnel_cycles
    _funnel_total.update(funnel)
    _funnel_cycles += 1
    if _funnel_cycles % FUNNEL_LOG_EVERY:
        return
    parts = [f"funnel/{_funnel_cycles}cyc: nominated={_funnel_total['nominated']}"
             f" rejected={_funnel_total['rejected']} passed={_funnel_total['passed']}"
             f" deduped={_funnel_total['passed_but_deduped']}"]
    for label, prefix in (("sole blocker", "sole_blocker:"), ("any fail", "failed:")):
        counts = {k.split(":", 1)[1]: v for k, v in _funnel_total.items()
                  if k.startswith(prefix)}
        parts.append(f"{label}: " + (", ".join(
            f"{k}={v}" for k, v in sorted(counts.items(), key=lambda x: -x[1])) or "none"))
    db.log("info", " | ".join(parts))


def funnel_snapshot() -> dict:
    """Cumulative funnel counts for this process, for the daily digest."""
    return {"cycles": _funnel_cycles, **dict(_funnel_total)}
