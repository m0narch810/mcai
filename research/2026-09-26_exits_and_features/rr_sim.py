"""Exit-rule comparison on already-recorded trades.

Same conventions as nr/outcomes.bracket:
- sequential 1-min bars from the fill; stop checked BEFORE targets, so a bar
  that spans both is a stop;
- no target/trim can fill on the fill bar (only the adverse side is knowable);
- a target is a resting limit: fills at its level only if high > level AND the
  bar's volume could have carried price there (outcomes._reachable);
- a stop fills at the worse of level, bar open and bar close;
- fees, CPMM impact, slippage buffer and tx cost on every buy and every sell
  (each trim is its own sell); exit below the liquidity floor is worth 0.
Reads the experiment DB; writes nothing to it."""
import json
import statistics as st
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from nr import db, notify, outcomes  # noqa: E402
from nr.config import PREREG  # noqa: E402

P = PREREG
S = P["position_usd"]
HERE = Path(__file__).parent
MAX_MIN = 360


def load(version, bars_json=None):
    db.init()
    bars_ext = json.loads(Path(bars_json).read_text()) if bars_json else None
    cut = db.iso(db.now_utc() - timedelta(hours=6, minutes=5))
    rows = [dict(r) for r in db.conn().execute(
        "SELECT c.*, e.price_usd q, e.liquidity_usd liq_in FROM candidates c "
        "JOIN entries e ON e.candidate_id=c.id WHERE c.prereg_version=? AND e.ok=1 "
        "AND e.liquidity_usd>0 AND c.t_decision<? ORDER BY c.id", (version, cut))]
    out = []
    for c in rows:
        if bars_ext is not None:
            bars = [tuple(b) for b in bars_ext.get(str(c["id"]), [])]
        else:
            bars = [tuple(r) for r in db.conn().execute(
                "SELECT ts,o,h,l,c,v FROM ohlcv WHERE candidate_id=? ORDER BY ts", (c["id"],))]
        if not bars:
            continue
        t_fill = int(db.parse_iso(c["t_decision"]).timestamp())
        nxt = next((b[1] for b in bars if t_fill <= b[0] < t_fill + 300), None)
        entry = max(x for x in (c["q"], nxt) if x)
        obs = [(db.parse_iso(o["t"]).timestamp(), o["liquidity_usd"] if o["present"] else 0.0)
               for o in db.conn().execute(
                   "SELECT t, liquidity_usd, present FROM liquidity_obs WHERE candidate_id=? "
                   "ORDER BY t", (c["id"],))]
        rep = db.conn().execute("SELECT report_json FROM reports WHERE candidate_id=? AND arm='C' "
                                "AND ok=1 AND late=0", (c["id"],)).fetchone()
        o = db.conn().execute("SELECT outcome_json FROM outcomes WHERE candidate_id=?",
                              (c["id"],)).fetchone()
        out.append({"id": c["id"], "sym": c["symbol"], "bars": bars, "t_fill": t_fill,
                    "entry": entry, "liq_in": c["liq_in"], "obs": obs,
                    "fee": outcomes._fee(c["dex_id"]),
                    "taken": bool(rep) and notify.is_trade(json.loads(rep[0])),
                    "status": c["research_status"],
                    "stored": (json.loads(o[0]).get("bracket") if o else None)})
    return out


def liq_before(t, ts):
    last = t["liq_in"]
    for ot, lq in t["obs"]:
        if ot > ts:
            break
        last = lq
    return last


def liq_exit(t, ts):
    """outcomes.evaluate's liq_at: the snapshot at/before ts; if it is more than
    45 min old, the WORSE of it and the next one (a rug in the gap must count)."""
    before = after = None
    for ot, lq in t["obs"]:
        if ot <= ts:
            before = (ot, lq)
        elif after is None:
            after = (ot, lq)
    if before is None:
        return after[1] if after else t["liq_in"]
    if ts - before[0] <= 45 * 60 or after is None:
        return before[1]
    return min(before[1], after[1])


def simulate(t, stop, levels, be_after_first=False, max_min=MAX_MIN):
    """levels: [(return_level, fraction_of_original), ...] ascending. Returns net
    return on the $S position."""
    fee, entry, t_fill = t["fee"], t["entry"], t["t_fill"]
    impact_in = S / (t["liq_in"] / 2)
    p_entry = entry * (1 + fee + impact_in + P["slippage_buffer"])
    tokens = (S - P["tx_cost_usd"]) / p_entry
    remaining, proceeds = 1.0, 0.0
    stop_lvl = None if stop is None else entry * (1 + stop)
    pending = [(entry * (1 + r), f) for r, f in levels]
    end = t_fill + max_min * 60
    prev_c = entry

    def sell(frac, px, ts):
        nonlocal proceeds
        lq = liq_exit(t, ts)
        if not px or not lq or lq < P["exit_liquidity_min_usd"]:
            return
        V = tokens * frac * px
        imp = V / (lq / 2 + V)
        proceeds += max(V * (1 - fee - imp - P["slippage_buffer"]) - P["tx_cost_usd"], 0.0)

    for ts, o, h, l, c, v in t["bars"]:
        if ts + 60 <= t_fill or ts >= end:
            continue
        if stop_lvl is not None and l <= stop_lvl:
            sell(remaining, min(stop_lvl, o, c), ts + 60)
            return proceeds / S - 1
        base = min(o, prev_c)
        lq = liq_before(t, ts)
        if not (ts <= t_fill < ts + 60):
            hit = [(px, f) for px, f in pending if h > px and outcomes._reachable(base, v, lq, px)]
            for px, f in hit:
                f = min(f, remaining)
                sell(f, px, ts + 60)
                remaining -= f
                pending.remove((px, next(ff for pp, ff in pending if pp == px)))
            if hit and be_after_first and stop_lvl is not None:
                stop_lvl = max(stop_lvl, entry)
            if remaining <= 1e-9:
                return proceeds / S - 1
        if c <= base or outcomes._reachable(base, v, lq, c):
            prev_c = c
        else:
            prev_c = base
    sell(remaining, outcomes._last_close_at(t["bars"], end), end)
    return proceeds / S - 1


def after_minus30(t):
    """For a trade that trades down to -30% before ever reaching +100%: what
    happened next? -> 'stop50' (reached -50% before recovering to entry),
    'recovered' (back to entry first), 'neither'. None if -30% never printed."""
    e, t_fill, end = t["entry"], t["t_fill"], t["t_fill"] + MAX_MIN * 60
    touched = False
    for ts, o, h, l, c, v in t["bars"]:
        if ts + 60 <= t_fill or ts >= end:
            continue
        fill_bar = ts <= t_fill < ts + 60
        if not touched:
            if not fill_bar and h > e * 2:
                return None
            if l <= e * 0.7:
                touched = True
                if l <= e * 0.5:
                    return "stop50"
            continue
        if l <= e * 0.5:
            return "stop50"
        if h >= e:
            return "recovered"
    return "neither" if touched else None


def policies():
    ps = {"CURRENT  SL-50 TP+100": (-0.5, [(1.0, 1.0)], False)}
    for sl in (-0.2, -0.3, -0.4, -0.5, -0.6, None):
        for tp in (0.5, 0.75, 1.0, 1.5, 2.0, 3.0, None):
            name = f"SL{'none' if sl is None else f'{sl:+.0%}'} TP{'none' if tp is None else f'{tp:+.0%}'}"
            ps[name] = (sl, [(tp, 1.0)] if tp else [], False)
    ps["YOURS  SL-30 trim 150/200/250"] = (-0.3, [(1.5, 1 / 3), (2.0, 1 / 3), (2.5, 1 / 3)], False)
    ps["YOURS+BE  ...stop to entry after 1st trim"] = (-0.3, [(1.5, 1 / 3), (2.0, 1 / 3), (2.5, 1 / 3)], True)
    ps["SL-50 trim 150/200/250"] = (-0.5, [(1.5, 1 / 3), (2.0, 1 / 3), (2.5, 1 / 3)], False)
    ps["SL-30 half@+100 rest@+250"] = (-0.3, [(1.0, 0.5), (2.5, 0.5)], False)
    return ps


def table(trades, label):
    ps = policies()
    res = {}
    for name, (sl, lv, be) in ps.items():
        nets = [simulate(t, sl, lv, be) for t in trades]
        res[name] = nets
    return res


if __name__ == "__main__":
    sets = {}
    v51 = load("19ce3af93839", HERE / "bars_v51.json")
    v4 = load("e16de5aac8ee")
    sets["v5.1 TAKEN (Claude top quarter)"] = [t for t in v51 if t["taken"]]
    sets["v5.1 ALL filled coins"] = v51
    sets["v4 ALL filled coins (holdout)"] = v4

    # Validation: CURRENT must reproduce the stored v4 bracket nets.
    diffs = [abs(simulate(t, -0.5, [(1.0, 1.0)]) - t["stored"]["net"]) for t in v4
             if t["stored"] and t["stored"].get("result") in ("target", "stop", "time")]
    print(f"VALIDATION vs stored v4 brackets: n={len(diffs)}  median |diff| "
          f"{st.median(diffs):.4f}  max {max(diffs):.4f}  within 2pp: "
          f"{sum(d < 0.02 for d in diffs)}/{len(diffs)}\n")

    results = {k: table(v, k) for k, v in sets.items()}
    names = list(policies())
    keys = list(sets)
    print("mean net per trade (EV) | win% | total $ at $250/trade")
    print(f"{'rule':<42}" + "".join(f"{k[:30]:>34}" for k in keys))
    print(f"{'n':<42}" + "".join(f"{len(sets[k]):>34}" for k in keys))
    rows = []
    for nm in names:
        cells = []
        for k in keys:
            n = results[k][nm]
            cells.append(f"{st.mean(n):+.3f} {sum(x > 0 for x in n) / len(n):4.0%} {sum(n) * S:+8.0f}")
        rows.append((st.mean(results[keys[0]][nm]), nm, cells))
    for nm in names[:1] + [r[1] for r in sorted(rows, key=lambda r: -r[0]) if r[1] != names[0]]:
        cells = next(r[2] for r in rows if r[1] == nm)
        print(f"{nm:<42}" + "".join(f"{c:>34}" for c in cells))

    print("\nRank of each rule by EV in each set (1 = best):")
    rank = {k: {nm: i + 1 for i, nm in enumerate(sorted(names, key=lambda nm: -st.mean(results[k][nm])))}
            for k in keys}
    for nm in ["CURRENT  SL-50 TP+100", "YOURS  SL-30 trim 150/200/250",
               "YOURS+BE  ...stop to entry after 1st trim"] + \
              sorted(names, key=lambda nm: rank[keys[0]][nm])[:5]:
        print(f"  {nm:<42}" + "  ".join(f"{rank[k][nm]:>3}/{len(names)}" for k in keys))

    print("\nOnce a trade is down 30% (before ever reaching +100%), what happens next within 6h?")
    for k in keys:
        r = [after_minus30(t) for t in sets[k]]
        r = [x for x in r if x]
        if r:
            print(f"  {k:<36} n={len(r):3d}  went on to -50%: {r.count('stop50') / len(r):.0%}  "
                  f"recovered to entry: {r.count('recovered') / len(r):.0%}  neither: "
                  f"{r.count('neither') / len(r):.0%}")
