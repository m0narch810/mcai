"""v6 shadow books: alternative exit/gate rules paper-traded on the SAME
gate-taken coins as the primary trade, so they can be compared on coins none
of them were tuned on. No extra Claude cost; scored after the coin's 6h
window from 1-min bars and the recorded liquidity snapshots.

Engine conventions are the primary bracket's (outcomes.bracket /
_round_trip): sequential 1-min bars; stop checked before targets, so a bar
spanning both is a stop; no target on the fill bar; a target is a resting
limit that fills at its level only if price trades THROUGH it and the bar's
volume could have carried price there; a stop fills at the worse of level,
open and close; fees, CPMM impact, slippage and tx cost on the buy and on
every sell (each trim is its own sell); exits below the liquidity floor are
worth 0, and a stale exit-liquidity reading (> 45 min) takes the worse of the
readings around it. With the primary's parameters this reproduces the stored
bracket nets exactly (tests/test_books.py and a 91/91 check on v4).

Extra rules, all decided on closed information only:
  stale_min - if the first take-profit hasn't filled by then, sell all at
              that minute's close (the coin isn't the runner);
  be        - after the first trim the stop moves up to the entry price;
  trail     - after the first trim, stop = (1 - trail) x highest CLOSE."""
import bisect
import json

from . import db, outcomes
from .config import PREREG

P = PREREG
S = P["position_usd"]


def trade_from_db(c: dict, bars: list) -> dict | None:
    """The sim's view of one candidate: fill time, entry reference (worse of
    the T_D quote and the next bar's open, as the primary), liquidity."""
    e = db.conn().execute("SELECT ok, price_usd, liquidity_usd FROM entries WHERE candidate_id=?",
                          (c["id"],)).fetchone()
    if not e or not e["ok"] or not e["liquidity_usd"] or not bars:
        return None
    t_fill = int(db.parse_iso(c["t_decision"]).timestamp())
    nxt = next((b[1] for b in bars if t_fill <= b[0] < t_fill + 300), None)
    obs = [(db.parse_iso(o["t"]).timestamp(), o["liquidity_usd"] if o["present"] else 0.0)
           for o in db.conn().execute("SELECT t, liquidity_usd, present FROM liquidity_obs "
                                      "WHERE candidate_id=? ORDER BY t", (c["id"],))]
    return prep({"bars": [tuple(b) for b in bars], "t_fill": t_fill,
                 "entry": max(x for x in (e["price_usd"], nxt) if x),
                 "liq_in": e["liquidity_usd"], "obs": obs, "fee": outcomes._fee(c.get("dex_id"))})


def prep(t: dict) -> dict:
    t["obs_t"] = [o[0] for o in t["obs"]]
    t["obs_l"] = [o[1] for o in t["obs"]]
    end = t["t_fill"] + 1440 * 60
    t["win"] = [b for b in t["bars"] if b[0] + 60 > t["t_fill"] and b[0] < end]
    return t


def _lq_before(t, ts):
    i = bisect.bisect_right(t["obs_t"], ts) - 1
    return t["liq_in"] if i < 0 else t["obs_l"][i]


def _lq_exit(t, ts):
    i = bisect.bisect_right(t["obs_t"], ts) - 1
    if i < 0:
        return t["obs_l"][0] if t["obs_l"] else t["liq_in"]
    if ts - t["obs_t"][i] <= 45 * 60 or i + 1 >= len(t["obs_t"]):
        return t["obs_l"][i]
    return min(t["obs_l"][i], t["obs_l"][i + 1])


def sim(t, stop, levels, stale_min=None, be=False, trail=None, max_min=360,
        size_frac_of_liq=None) -> float:
    """Net return on the position. size_frac_of_liq caps the position at that
    fraction of entry liquidity (CPMM impact per side = size / (liq/2))."""
    fee, entry, t_fill = t["fee"], t["entry"], t["t_fill"]
    Sx = S if size_frac_of_liq is None else min(S, size_frac_of_liq * t["liq_in"])
    p_entry = entry * (1 + fee + Sx / (t["liq_in"] / 2) + P["slippage_buffer"])
    tokens = (Sx - P["tx_cost_usd"]) / p_entry
    remaining, proceeds = 1.0, 0.0
    stop_lvl = None if stop is None else entry * (1 + stop)
    pending = sorted((entry * (1 + r), f) for r, f in levels)
    end = t_fill + max_min * 60
    stale_end = None if stale_min is None else t_fill + stale_min * 60
    prev_c, hi_close, trimmed = entry, entry, False

    def sell(frac, px, ts):
        nonlocal proceeds
        lq = _lq_exit(t, ts)
        if not px or not lq or lq < P["exit_liquidity_min_usd"]:
            return
        V = tokens * frac * px
        proceeds += max(V * (1 - fee - V / (lq / 2 + V) - P["slippage_buffer"]) - P["tx_cost_usd"], 0.0)

    for ts, o, h, l, c, v in t["win"]:
        if ts >= end:
            break
        if stale_end is not None and not trimmed and ts >= stale_end:
            sell(remaining, outcomes._last_close_at(t["bars"], stale_end), stale_end)
            return proceeds / Sx - 1
        eff = stop_lvl
        if trimmed and trail is not None:
            tl = hi_close * (1 - trail)
            eff = tl if eff is None else max(eff, tl)
        if eff is not None and l <= eff:
            sell(remaining, min(eff, o, c), ts + 60)
            return proceeds / Sx - 1
        base = min(o, prev_c)
        lq = _lq_before(t, ts)
        if not (ts <= t_fill < ts + 60):
            while pending and h > pending[0][0] and outcomes._reachable(base, v, lq, pending[0][0]):
                px, f = pending.pop(0)
                f = min(f, remaining)
                sell(f, px, ts + 60)
                remaining -= f
                if not trimmed:
                    trimmed = True
                    if be:
                        stop_lvl = entry if stop_lvl is None else max(stop_lvl, entry)
            if remaining <= 1e-9:
                return proceeds / Sx - 1
        ok_close = c <= base or outcomes._reachable(base, v, lq, c)
        prev_c = c if ok_close else base
        if ok_close and not (ts <= t_fill < ts + 60):
            hi_close = max(hi_close, c)
    last = min(stale_end, end) if (stale_end is not None and not trimmed) else end
    sell(remaining, outcomes._last_close_at(t["bars"], last), last)
    return proceeds / Sx - 1


def rank_threshold(arm: str, t4: str, q: float, n: int) -> float | None:
    """Nearest-rank q-quantile of this arm's last n on-time p_runner values
    frozen before t4 (the same causal construction as the primary gate)."""
    from .config import PREREG_VERSION
    prior = sorted(r[0] for r in db.conn().execute(
        "SELECT json_extract(r.report_json,'$.p_runner') FROM reports r "
        "JOIN candidates c ON c.id=r.candidate_id AND c.prereg_version=? "
        "WHERE r.arm=? AND r.ok=1 AND r.late=0 AND r.t4_frozen<? "
        "AND json_extract(r.report_json,'$.p_runner') IS NOT NULL "
        "ORDER BY r.t4_frozen DESC LIMIT ?", (PREREG_VERSION, arm, t4, n)))
    if len(prior) < P.get("trade_rank_min_prior", 1):
        return None
    k = min(len(prior) - 1, max(0, int(-(-q * len(prior) // 1)) - 1))
    return prior[k]


def run_books(c: dict, rep: dict, t4: str, t: dict) -> dict:
    """Net return per shadow book for one gate-taken coin (None = the book's
    gate skipped it)."""
    out = {}
    for name, b in P["shadow_books"].items():
        if b.get("gate_quantile"):
            thr = rank_threshold("C", t4, b["gate_quantile"], P["trade_rank_window"])
            if thr is None or rep.get("p_runner", -1) < thr:
                out[name] = None
                continue
        out[name] = round(sim(t, b.get("stop"), [tuple(x) for x in b["levels"]],
                              b.get("stale_min"), b.get("be", False), b.get("trail"),
                              b.get("max_min", 360), b.get("size_frac_of_liq")), 4)
    return out


def primary_params() -> dict:
    return {"stop": P["bracket_stop"], "levels": [[P["bracket_target"], 1.0]],
            "max_min": P["bracket_max_minutes"]}


def dumps(x) -> str:
    return json.dumps(x, sort_keys=True)
