"""Dip entry: from T_D, track the highest CLOSE; place a resting limit buy at
(1 - d) x that high. It fills only if a later bar trades THROUGH it
(low < level), at the level (never better). If it hasn't filled within
wait_min, the coin is skipped (no trade). Entry liquidity = last snapshot
before the fill. Exit rules are then applied from the fill, via sim2 on a
shifted copy of the trade. Written before running; d in {0.2, 0.3}."""
import statistics as st
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import exits2 as E  # noqa: E402
import features as F  # noqa: E402
import rr_search as rs  # noqa: E402
import rr_sim  # noqa: E402
from rr_sim import P  # noqa: E402


def dip_trade(t, d, wait_min=30):
    hi = None
    end = t["t_fill"] + wait_min * 60
    for ts, o, h, l, c, v in t["bars"]:
        if ts < t["t_fill"]:
            continue
        if ts >= end:
            return None
        if hi is not None and l < hi * (1 - d):
            lvl = hi * (1 - d)
            liq = rs.lq_before(t, ts)
            if not liq or liq < P["exit_liquidity_min_usd"] * 2:
                return None
            u = dict(t)
            # Fill during bar ts; the fill bar's favourable side is unknowable,
            # so the shifted trade starts at this bar and sim2 treats it as the
            # fill bar (no target on it; stop checked on its low).
            u["t_fill"], u["entry"], u["liq_in"] = ts + 30, lvl, liq
            return rs.prep(u)
        hi = c if hi is None else max(hi, c)
    return None


def main():
    v51 = [rs.prep(t) for t in rr_sim.load("19ce3af93839", HERE / "bars_v51.json")]
    v4 = [rs.prep(t) for t in rr_sim.load("e16de5aac8ee")]
    v51.sort(key=lambda t: t["t_fill"])
    for t in v51 + v4:
        t["x"] = F.feats(t)
    ok = lambda ts: [t for t in ts if t["x"]["lp_removable"] is False]
    h = len(v51) // 2
    sets = {"A": ok(v51[:h]), "B": ok(v51[h:]), "H": ok(v4), "T": ok([t for t in v51 if t["taken"]])}
    EX = {"TP+100 SL-50 30m": dict(stop=-0.5, levels=[(1.0, 1.0)], max_min=30),
          "TP+100 SL-50 6h": dict(stop=-0.5, levels=[(1.0, 1.0)]),
          "TP+50 SL-30 60m": dict(stop=-0.3, levels=[(0.5, 1.0)], max_min=60)}
    print("LP-filtered coins. Immediate entry vs dip entry. cell = n traded | EV | win%")
    for en, kw in EX.items():
        print(en)
        for label, mk in (("buy at T_D (now)", lambda t: t),
                          ("dip -20% within 30m", lambda t: dip_trade(t, 0.2)),
                          ("dip -30% within 30m", lambda t: dip_trade(t, 0.3))):
            cells = []
            for k, ts in sets.items():
                tr = [u for u in (mk(t) for t in ts) if u]
                if not tr:
                    cells.append(f"{k}: n=0")
                    continue
                n = [E.sim2(u, **kw) for u in tr]
                cells.append(f"{k}: {len(n):3d}/{len(ts):<3d} {st.mean(n):+.3f} {sum(x > 0 for x in n) / len(n):3.0%}")
            print(f"   {label:<22}" + "  |  ".join(cells))


if __name__ == "__main__":
    main()
