"""Trencher-style exits on coins that pass the removable-LP filter.

New ingredient, "stale exit": if the first take-profit has not filled by
stale_min, sell everything at the close of that minute (a coin that has not
moved by then is not the runner). Once the first trim fills, the remainder
rides to max_min under an optional trailing stop (on closes) and an optional
break-even stop. Everything else as rr_search.sim (validated 91/91).
The policies were written down before running; no grid."""
import statistics as st
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import features as F  # noqa: E402
import rr_search as rs  # noqa: E402
import rr_sim  # noqa: E402
from rr_sim import P, S, outcomes  # noqa: E402


def sim2(t, stop, levels, stale_min=None, be=False, trail=None, max_min=360):
    fee, entry, t_fill = t["fee"], t["entry"], t["t_fill"]
    p_entry = entry * (1 + fee + S / (t["liq_in"] / 2) + P["slippage_buffer"])
    tokens = (S - P["tx_cost_usd"]) / p_entry
    remaining, proceeds = 1.0, 0.0
    stop_lvl = None if stop is None else entry * (1 + stop)
    pending = sorted((entry * (1 + r), f) for r, f in levels)
    end = t_fill + max_min * 60
    stale_end = None if stale_min is None else t_fill + stale_min * 60
    prev_c, hi_close, trimmed = entry, entry, False

    def sell(frac, px, ts):
        nonlocal proceeds
        lq = rs.lq_exit(t, ts)
        if not px or not lq or lq < P["exit_liquidity_min_usd"]:
            return
        V = tokens * frac * px
        proceeds += max(V * (1 - fee - V / (lq / 2 + V) - P["slippage_buffer"]) - P["tx_cost_usd"], 0.0)

    for ts, o, h, l, c, v in t["win"]:
        if ts >= end:
            break
        if stale_end is not None and not trimmed and ts >= stale_end:
            sell(remaining, outcomes._last_close_at(t["bars"], stale_end), stale_end)
            return proceeds / S - 1
        eff = stop_lvl
        if trimmed and trail is not None:
            tl = hi_close * (1 - trail)
            eff = tl if eff is None else max(eff, tl)
        if eff is not None and l <= eff:
            sell(remaining, min(eff, o, c), ts + 60)
            return proceeds / S - 1
        base = min(o, prev_c)
        lq = rs.lq_before(t, ts)
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
                return proceeds / S - 1
        ok_close = c <= base or outcomes._reachable(base, v, lq, c)
        prev_c = c if ok_close else base
        if ok_close and not (ts <= t_fill < ts + 60):
            hi_close = max(hi_close, c)
    if stale_end is not None and not trimmed:
        sell(remaining, outcomes._last_close_at(t["bars"], min(stale_end, end)), min(stale_end, end))
    else:
        sell(remaining, outcomes._last_close_at(t["bars"], end), end)
    return proceeds / S - 1


HALF_2X = [(1.0, 0.5)]
YOUR_LADDER = [(1.0, 1 / 3), (1.75, 1 / 3), (2.5, 1 / 3)]
POL = {
    "E0 current: SL-50 TP+100, 6h":                        dict(stop=-0.5, levels=[(1.0, 1.0)]),
    "E1 SL-50 TP+100, sell at 30m":                        dict(stop=-0.5, levels=[(1.0, 1.0)], max_min=30),
    "E2 half at 2x, ride rest trail40%, stale 30m":       dict(stop=-0.5, levels=HALF_2X, stale_min=30, trail=0.4),
    "E3 half at 2x, ride rest trail40%+BE, stale 30m":    dict(stop=-0.5, levels=HALF_2X, stale_min=30, trail=0.4, be=True),
    "E4 your ladder 100/175/250 +BE, stale 30m":          dict(stop=-0.5, levels=YOUR_LADDER, stale_min=30, be=True),
    "E5 your ladder 100/175/250 +BE trail40%, stale 30m": dict(stop=-0.5, levels=YOUR_LADDER, stale_min=30, be=True, trail=0.4),
    "E6 E3 with stale 60m":                                dict(stop=-0.5, levels=HALF_2X, stale_min=60, trail=0.4, be=True),
    "E7 E3 with no hard stop":                             dict(stop=None, levels=HALF_2X, stale_min=30, trail=0.4, be=True),
    "E8 E3 with stop -35%":                                dict(stop=-0.35, levels=HALF_2X, stale_min=30, trail=0.4, be=True),
}


def main():
    v51 = [rs.prep(t) for t in rr_sim.load("19ce3af93839", HERE / "bars_v51.json")]
    v4 = [rs.prep(t) for t in rr_sim.load("e16de5aac8ee")]
    v51.sort(key=lambda t: t["t_fill"])
    for t in v51 + v4:
        t["x"] = F.feats(t)
    keep = lambda ts: [t for t in ts if t["x"]["lp_removable"] is False]
    h = len(v51) // 2
    sets = {"A": keep(v51[:h]), "B": keep(v51[h:]), "H": keep(v4),
            "T": keep([t for t in v51 if t["taken"]]), "T_all": [t for t in v51 if t["taken"]]}
    L = ["Coins passing the LP filter (locked/burned/curve). cell = EV | win% | avg win | avg loss | best",
         "sets: A/B v5.1 halves, H v4, T = Claude-taken trades passing the filter, T_all = all taken (no filter)",
         "n: " + "  ".join(f"{k}={len(v)}" for k, v in sets.items())]
    for nm, kw in POL.items():
        L.append(nm)
        for k, ts in sets.items():
            n = [sim2(t, **kw) for t in ts]
            w = [x for x in n if x > 0]
            lo = [x for x in n if x <= 0]
            L.append(f"   {k:<6} EV {st.mean(n):+.3f}  win {len(w) / len(n):3.0%}  avg win "
                     f"{st.mean(w) if w else 0:+.2f}  avg loss {st.mean(lo) if lo else 0:+.2f}  best {max(n):+.2f}"
                     f"  total ${sum(n) * S:+,.0f}")
    out = "\n".join(L)
    (HERE / "exits2_results.txt").write_text(out, encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
