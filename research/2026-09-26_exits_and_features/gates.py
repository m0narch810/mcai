"""Gate variants on every Claude-researched v5.1 coin with a complete window.
Few, motivated variants; A/B = time halves of the researched coins."""
import json
import statistics as st
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import exits2 as E  # noqa: E402
import features as F  # noqa: E402
import rr_search as rs  # noqa: E402
import rr_sim  # noqa: E402
from nr import db  # noqa: E402

v51 = [rs.prep(t) for t in rr_sim.load("19ce3af93839", HERE / "bars_v51.json")]
res = []
for t in sorted(v51, key=lambda t: t["t_fill"]):
    r = db.conn().execute("SELECT report_json FROM reports WHERE candidate_id=? AND arm='C' AND ok=1 AND late=0",
                          (t["id"],)).fetchone()
    if not r:
        continue
    t["rep"] = json.loads(r[0])
    t["x"] = F.feats(t)
    res.append(t)
h = len(res) // 2
sets = {"A": res[:h], "B": res[h:], "ALL": res}
top = lambda t: bool((t["rep"].get("_gate") or {}).get("take"))
lpok = lambda t: t["x"]["lp_removable"] is False
G = {
    "G0 all researched coins (no gate)": lambda t: True,
    "G1 current gate (top quarter p_runner)": top,
    "G2 G1 + LP not removable": lambda t: top(t) and lpok(t),
    "G3 G2 + survival >= 30": lambda t: top(t) and lpok(t) and (t["rep"].get("survival_score") or 0) >= 30,
    "G4 G2 + p_rug < 50": lambda t: top(t) and lpok(t) and (t["rep"].get("p_rug") or 100) < 50,
    "G5 LP not removable + p_rug < 50 (no rank)": lambda t: lpok(t) and (t["rep"].get("p_rug") or 100) < 50,
    "G6 G2 + top-half p_runner instead of top quarter": None,   # filled below
    "G7 G2 + tail class C/D": lambda t: top(t) and lpok(t) and t["rep"].get("tail_class") in ("C_2x_to_5x", "D_5x_plus"),
    "G8 G2 + manipulation < 50": lambda t: top(t) and lpok(t) and (t["rep"].get("manipulation_risk") or 100) < 50,
}
med = st.median(t["rep"]["p_runner"] for t in res)
G["G6 G2 + top-half p_runner instead of top quarter"] = lambda t: lpok(t) and t["rep"]["p_runner"] >= med
EX = {"6h (current)": dict(stop=-0.5, levels=[(1.0, 1.0)]),
      "30m hold": dict(stop=-0.5, levels=[(1.0, 1.0)], max_min=30)}
print(f"researched coins with complete windows: {len(res)} (A={h}, B={len(res) - h}); median p_runner {med}")
for gn, g in G.items():
    print(gn)
    for en, kw in EX.items():
        cells = []
        for k, ts in sets.items():
            sel = [t for t in ts if g(t)]
            if not sel:
                cells.append(f"{k}: n=0")
                continue
            n = [E.sim2(t, **kw) for t in sel]
            rug = sum(x <= -0.95 for x in n)
            cells.append(f"{k}: n={len(n):3d} EV {st.mean(n):+.3f} win {sum(x > 0 for x in n) / len(n):3.0%} rug {rug}")
        print(f"   {en:<13}" + "  |  ".join(cells))
