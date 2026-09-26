"""Wide exit-rule search with out-of-sample checks.

Engine conventions are rr_sim's (validated 91/91 against the experiment's
stored v4 brackets): sequential 1-min bars, stop checked before targets, no
target on the fill bar, targets need price THROUGH the level plus reachable
volume, stop fills at the worse of level/open/close, costs on every sell.
Extra rule types, all decided on CLOSED information only:
- trailing stop: armed after the first trim; level = (1 - trail) x highest
  CLOSE so far (closes, not highs, so the level never uses an intrabar
  extreme), checked against the next bars' lows like the fixed stop;
- break-even: after the first trim the stop moves up to the entry price;
- max hold: sell the remainder at the last close before the limit.

Selection protocol (to avoid crowning a lucky rule):
  A = v5.1 coins, first half by time; B = v5.1 coins, second half;
  H = v4 coins (older version, later entry) - never used to choose.
  Rules are ranked on A and on B separately; the pick is the rule with the
  best WORST rank across A and B, then reported on H and on the taken trades.
"""
import bisect
import itertools
import json
import statistics as st
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import rr_sim  # noqa: E402
from rr_sim import P, S, outcomes  # noqa: E402


def prep(t):
    t["obs_t"] = [o[0] for o in t["obs"]]
    t["obs_l"] = [o[1] for o in t["obs"]]
    end = t["t_fill"] + 360 * 60
    t["win"] = [b for b in t["bars"] if b[0] + 60 > t["t_fill"] and b[0] < end]
    return t


def lq_before(t, ts):
    i = bisect.bisect_right(t["obs_t"], ts) - 1
    return t["liq_in"] if i < 0 else t["obs_l"][i]


def lq_exit(t, ts):
    i = bisect.bisect_right(t["obs_t"], ts) - 1
    if i < 0:
        return t["obs_l"][0] if t["obs_l"] else t["liq_in"]
    if ts - t["obs_t"][i] <= 45 * 60 or i + 1 >= len(t["obs_t"]):
        return t["obs_l"][i]
    return min(t["obs_l"][i], t["obs_l"][i + 1])


def sim(t, stop, levels, be=False, trail=None, max_min=360):
    fee, entry, t_fill = t["fee"], t["entry"], t["t_fill"]
    p_entry = entry * (1 + fee + S / (t["liq_in"] / 2) + P["slippage_buffer"])
    tokens = (S - P["tx_cost_usd"]) / p_entry
    remaining, proceeds = 1.0, 0.0
    stop_lvl = None if stop is None else entry * (1 + stop)
    pending = sorted((entry * (1 + r), f) for r, f in levels)
    end = t_fill + max_min * 60
    prev_c, hi_close, trimmed = entry, entry, False

    def sell(frac, px, ts):
        nonlocal proceeds
        lq = lq_exit(t, ts)
        if not px or not lq or lq < P["exit_liquidity_min_usd"]:
            return
        V = tokens * frac * px
        proceeds += max(V * (1 - fee - V / (lq / 2 + V) - P["slippage_buffer"])
                        - P["tx_cost_usd"], 0.0)

    for ts, o, h, l, c, v in t["win"]:
        if ts >= end:
            break
        eff = stop_lvl
        if trimmed and trail is not None:
            tl = hi_close * (1 - trail)
            eff = tl if eff is None else max(eff, tl)
        if eff is not None and l <= eff:
            sell(remaining, min(eff, o, c), ts + 60)
            return proceeds / S - 1
        base = min(o, prev_c)
        lq = lq_before(t, ts)
        if not (ts <= t_fill < ts + 60):
            while pending and h > pending[0][0] and outcomes._reachable(base, v, lq, pending[0][0]):
                px, f = pending.pop(0)
                f = min(f, remaining)
                sell(f, px, ts + 60)
                remaining -= f
                if not trimmed:
                    trimmed = True
                    if be and stop_lvl is not None:
                        stop_lvl = max(stop_lvl, entry)
                    elif be:
                        stop_lvl = entry
            if remaining <= 1e-9:
                return proceeds / S - 1
        ok_close = c <= base or outcomes._reachable(base, v, lq, c)
        prev_c = c if ok_close else base
        if ok_close and not (ts <= t_fill < ts + 60):
            hi_close = max(hi_close, c)
    sell(remaining, outcomes._last_close_at(t["bars"], end), end)
    return proceeds / S - 1


def ladder(a, b, n):
    if n == 1:
        return [(a, 1.0)]
    return [(a + (b - a) * i / (n - 1), 1.0 / n) for i in range(n)]


HOLDS = (15, 30, 60, 90, 180, 360)


def rules():
    R = {}
    stops = [-0.15, -0.2, -0.25, -0.3, -0.35, -0.4, -0.5, None]
    exits = {f"TP{x:+.0%}": ladder(x, x, 1) for x in (0.3, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0)}
    for a, b in ((0.5, 1.5), (0.5, 2.5), (1.0, 2.0), (1.0, 3.0), (1.5, 2.5), (1.5, 4.0), (2.0, 4.0)):
        exits[f"trim{a:+.0%}..{b:+.0%}x3"] = ladder(a, b, 3)
    exits["half@+50 rest@+200"] = [(0.5, 0.5), (2.0, 0.5)]
    exits["half@+100 rest@+300"] = [(1.0, 0.5), (3.0, 0.5)]
    exits["third@+50 trail"] = [(0.5, 1 / 3)]
    exits["half@+100 trail"] = [(1.0, 0.5)]
    for (en, lv), sl, be, trail, mh in itertools.product(
            exits.items(), stops, (False, True), (None, 0.3, 0.5), HOLDS):
        if trail is not None and (lv[-1][1] >= 0.999 and len(lv) == 1):
            continue    # a single full exit leaves nothing to trail
        name = (f"SL{'none' if sl is None else f'{sl:+.0%}'} {en}"
                f"{' +BE' if be else ''}{f' trail{trail:.0%}' if trail else ''} hold{mh}m")
        R[name] = (sl, lv, be, trail, mh)
    return R


CURRENT = "SL-50% TP+100% hold360m"
YOURS = "SL-30% trim+150%..+250%x3 hold360m"
YOURS_BE = "SL-30% trim+150%..+250%x3 +BE hold360m"


def main(out_path):
    v51 = [prep(t) for t in rr_sim.load("19ce3af93839", HERE / "bars_v51.json")]
    v4 = [prep(t) for t in rr_sim.load("e16de5aac8ee")]
    v51.sort(key=lambda t: t["t_fill"])
    half = len(v51) // 2
    sets = {"A": v51[:half], "B": v51[half:], "H": v4, "T": [t for t in v51 if t["taken"]]}
    R = rules()
    ev = {k: {} for k in sets}
    for name, (sl, lv, be, trail, mh) in R.items():
        for k, ts in sets.items():
            n = [sim(t, sl, lv, be, trail, mh) for t in ts]
            ev[k][name] = (st.mean(n) if n else float("nan"), sum(x > 0 for x in n) / len(n) if n else 0)
    rank = {k: {nm: i + 1 for i, nm in enumerate(sorted(R, key=lambda nm: -ev[k][nm][0]))} for k in "ABH"}
    worst_ab = sorted(R, key=lambda nm: (max(rank["A"][nm], rank["B"][nm]), rank["A"][nm] + rank["B"][nm]))

    def spearman(k1, k2):
        a = [rank[k1][nm] for nm in R]
        b = [rank[k2][nm] for nm in R]
        n = len(a)
        return 1 - 6 * sum((x - y) ** 2 for x, y in zip(a, b)) / (n * (n * n - 1))

    L = [f"rules tested: {len(R)}",
         f"n: A={len(sets['A'])} B={len(sets['B'])} (v5.1 halves), H={len(sets['H'])} (v4 holdout), "
         f"T={len(sets['T'])} (v5.1 taken)",
         f"rank agreement (Spearman): A vs B {spearman('A', 'B'):+.2f}   A vs H {spearman('A', 'H'):+.2f}   "
         f"B vs H {spearman('B', 'H'):+.2f}",
         "", "EV per trade (win%) by set; rank of 1..N in A/B/H",
         f"{'rule':<52}{'A':>14}{'B':>14}{'H':>14}{'T':>14}   ranks A/B/H"]

    def row(nm):
        cells = "".join(f"{ev[k][nm][0]:+.3f}({ev[k][nm][1]:3.0%})" if ev[k][nm][0] == ev[k][nm][0]
                        else f"{'n/a':>14}" for k in "ABHT")
        return f"{nm:<52}{cells}   {rank['A'][nm]}/{rank['B'][nm]}/{rank['H'][nm]}"

    L.append("-- reference --")
    for nm in (CURRENT, YOURS, YOURS_BE):
        L.append(row(nm))
    L.append("-- top 25 by worst rank across the two v5.1 halves (chosen without v4) --")
    for nm in worst_ab[:25]:
        L.append(row(nm))
    L.append("-- top 10 on A alone, and how they did on B and H (overfit check) --")
    for nm in sorted(R, key=lambda nm: rank["A"][nm])[:10]:
        L.append(row(nm))

    # Which single ingredients help, averaged over everything else?
    L.append("")
    L.append("-- ingredient effects: mean EV over all rules sharing the ingredient (A+B pooled / H) --")
    AB = {nm: (ev["A"][nm][0] * len(sets["A"]) + ev["B"][nm][0] * len(sets["B"])) / (len(sets["A"]) + len(sets["B"]))
          for nm in R}
    def eff(label, pred):
        sel = [nm for nm in R if pred(R[nm])]
        L.append(f"  {label:<28} v5.1 {st.mean(AB[n] for n in sel):+.3f}   v4 {st.mean(ev['H'][n][0] for n in sel):+.3f}"
                 f"   ({len(sel)} rules)")
    for sl in (-0.15, -0.2, -0.25, -0.3, -0.35, -0.4, -0.5, None):
        eff(f"stop {sl}", lambda r, sl=sl: r[0] == sl)
    for mh in HOLDS:
        eff(f"max hold {mh}m", lambda r, mh=mh: r[4] == mh)
    eff("break-even after 1st trim", lambda r: r[2])
    eff("no break-even", lambda r: not r[2])
    for tr in (None, 0.3, 0.5):
        eff(f"trail {tr}", lambda r, tr=tr: r[3] == tr)
    eff("single full take-profit", lambda r: len(r[1]) == 1 and r[1][0][1] > 0.99)
    eff("scaled exits (trims)", lambda r: len(r[1]) > 1 or r[1][0][1] < 0.99)

    L.append("")
    L.append("-- once down 30% before +100%: next within 6h --")
    for k in "ABHT":
        r = [x for x in (rr_sim.after_minus30(t) for t in sets[k]) if x]
        if r:
            L.append(f"  {k}: n={len(r)}  to -50% {r.count('stop50') / len(r):.0%}  back to entry "
                     f"{r.count('recovered') / len(r):.0%}")
    Path(out_path).write_text("\n".join(L), encoding="utf-8")
    json.dump({k: {nm: ev[k][nm] for nm in R} for k in ev}, open(Path(out_path).with_suffix(".json"), "w"))
    print("\n".join(L[:40]))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else HERE / "search_results.txt")
