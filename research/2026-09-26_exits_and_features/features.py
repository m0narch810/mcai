"""Pre-trade feature study. Hypotheses were written BEFORE running this, each
with a market-structure reason; a feature counts only if its direction holds
in v5.1 half A, v5.1 half B AND v4 (H).

H1 removable LP (not a pump.fun curve, LP neither locked nor burned) -> more
   -100% outcomes: an LP pull is the mechanism that zeroes a position.
H2 wash ratio (buys per unique buyer, last hour) high -> manufactured volume,
   worse outcomes. Organic flow is a few buys per wallet; bots churn.
H3 already pumped (h1 price change > +300%) -> late, worse; already dumping
   at detection (m5 change < 0) -> worse.
H4 concentration (top-10 holders > 30%, dev/creator holding, bundlers) ->
   supply that can be dumped -> worse.
H5 smart / renowned (KOL) wallets present -> better (informed buyers).
H6 thin pool (< $15k liquidity) -> worse: costs and impact eat the move.
H7 breadth: more unique buyers in the last hour -> better.
H8 Claude p_rug high / survival low -> more rugs.
H9 RugCheck score_normalised high -> worse.
H10 bonding-curve coin vs graduated pool.
"""
import json
import statistics as st
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import rr_sim  # noqa: E402
import rr_search as rs  # noqa: E402
from nr import db  # noqa: E402


def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def feats(t):
    p = db.conn().execute("SELECT packet_json FROM packets WHERE candidate_id=?", (t["id"],)).fetchone()
    p = json.loads(p[0]) if p else {}
    trig = p.get("trigger_at_detection") or {}
    s = p.get("structure") or {}
    g = p.get("gmgn_wallets_and_dev") or {}
    gt = (p.get("market") or {}).get("geckoterminal") or {}
    rates = g.get("rates") or {}
    tags = g.get("wallet_tags") or {}
    sec = g.get("security") or {}
    lp_locked = (s.get("lp_locked_pct_top_markets") or [None])[0]
    burn = f(sec.get("lp_burn_ratio"))
    curve = bool(trig.get("bonding_curve"))
    buys = (gt.get("buys") or {}).get("h1")
    ub = (gt.get("unique_buyers") or {}).get("h1")
    rep = db.conn().execute("SELECT report_json FROM reports WHERE candidate_id=? AND arm='C' AND ok=1",
                            (t["id"],)).fetchone()
    rep = json.loads(rep[0]) if rep else {}
    return {
        "lp_removable": None if (lp_locked is None and burn is None and not curve) else
        (not curve and (lp_locked or 0) < 90 and (burn or 0) < 0.9),
        "wash_ratio": (buys / ub) if buys and ub else None,
        "pc_h1": f(trig.get("price_change_h1")),
        "pc_m5": f(trig.get("price_change_m5")),
        "top10": f(s.get("top10_holder_pct")),
        "dev_hold": max(f(rates.get("dev_team_hold_rate")) or 0, f(rates.get("creator_hold_rate")) or 0) if rates else None,
        "bundler": f(rates.get("top_bundler_trader_pct")),
        "sniper": f(rates.get("top70_sniper_hold_rate")),
        "smart": ((tags.get("smart_wallets") or 0) + (tags.get("renowned_wallets") or 0)) if tags else None,
        "liq": f(trig.get("liquidity_usd")),
        "ub_h1": ub,
        "age": f(trig.get("pair_age_min")),
        "mcap": f(trig.get("mcap_usd")),
        "p_rug": rep.get("p_rug"), "survival": rep.get("survival_score"),
        "p_runner": rep.get("p_runner"),
        "rc_score": f(s.get("rugcheck_score_normalised")),
        "curve": curve,
        "bot_rate": f(rates.get("bot_degen_rate")),
    }


def outcome(t):
    cur = rs.sim(t, -0.5, [(1.0, 1.0)], max_min=360)
    m30 = rs.sim(t, -0.5, [(1.0, 1.0)], max_min=30)
    return {"cur": cur, "m30": m30, "rug": cur <= -0.95,
            "target": rr_sim.simulate(t, -0.5, [(1.0, 1.0)]) > 0.5}


TESTS = [
    ("H1 LP removable", lambda x: x["lp_removable"], [(True, "removable"), (False, "locked/burned/curve")]),
    ("H2 wash ratio", lambda x: x["wash_ratio"], [((0, 4), "<4 buys/buyer"), ((4, 8), "4-8"), ((8, 1e9), ">8")]),
    ("H3 h1 pump", lambda x: x["pc_h1"], [((-1e9, 100), "<+100%"), ((100, 300), "+100..300%"), ((300, 1e9), ">+300%")]),
    ("H3 m5 at detection", lambda x: x["pc_m5"], [((-1e9, 0), "dumping (m5<0)"), ((0, 1e9), "rising (m5>=0)")]),
    ("H4 top10 holders %", lambda x: x["top10"], [((0, 20), "<20%"), ((20, 30), "20-30%"), ((30, 101), ">30%")]),
    ("H4 dev/creator hold", lambda x: x["dev_hold"], [((-1, 0.001), "0"), ((0.001, 2), ">0")]),
    ("H4 bundler %", lambda x: x["bundler"], [((-1, 0.05), "<5%"), ((0.05, 2), ">=5%")]),
    ("H4 sniper hold", lambda x: x["sniper"], [((-1, 0.02), "<2%"), ((0.02, 2), ">=2%")]),
    ("H5 smart/KOL wallets", lambda x: x["smart"], [((-1, 0.5), "none"), ((0.5, 1e9), ">=1")]),
    ("H6 liquidity", lambda x: x["liq"], [((0, 15000), "<$15k"), ((15000, 40000), "$15-40k"), ((40000, 1e12), ">$40k")]),
    ("H7 unique buyers 1h", lambda x: x["ub_h1"], [((0, 200), "<200"), ((200, 500), "200-500"), ((500, 1e9), ">500")]),
    ("H8 Claude p_rug", lambda x: x["p_rug"], [((-1, 25), "<25"), ((25, 50), "25-49"), ((50, 101), ">=50")]),
    ("H8 Claude survival", lambda x: x["survival"], [((-1, 30), "<30"), ((30, 60), "30-59"), ((60, 101), ">=60")]),
    ("H9 RugCheck score", lambda x: x["rc_score"], [((-1, 10), "<10"), ((10, 50), "10-49"), ((50, 1e9), ">=50")]),
    ("H10 curve", lambda x: x["curve"], [(True, "on curve"), (False, "graduated/other")]),
    ("age at detection", lambda x: x["age"], [((0, 20), "10-20m"), ((20, 60), "20-60m"), ((60, 1e9), ">1h")]),
    ("GMGN bot/degen rate", lambda x: x["bot_rate"], [((-1, 0.3), "<30%"), ((0.3, 0.5), "30-50%"), ((0.5, 2), ">50%")]),
]


def inb(v, b):
    if isinstance(b, tuple):
        return v is not None and b[0] <= v < b[1]
    return v is b or v == b


def main():
    v51 = [rs.prep(t) for t in rr_sim.load("19ce3af93839", HERE / "bars_v51.json")]
    v4 = [rs.prep(t) for t in rr_sim.load("e16de5aac8ee")]
    v51.sort(key=lambda t: t["t_fill"])
    for t in v51 + v4:
        t["x"], t["y"] = feats(t), outcome(t)
    h = len(v51) // 2
    sets = {"A": v51[:h], "B": v51[h:], "H": v4}
    L = ["cell = n | EV 30m-hold | EV current | TP-first% | rug%   (sets A,B = v5.1 halves, H = v4)"]
    for name, get, buckets in TESTS:
        L.append(f"\n{name}")
        for b, lab in buckets:
            cells = []
            for k, ts in sets.items():
                g = [t for t in ts if inb(get(t["x"]), b)]
                if len(g) < 5:
                    cells.append(f"{k}: n={len(g):<3} {'':>30}")
                    continue
                cells.append(f"{k}: n={len(g):<3} {st.mean(t['y']['m30'] for t in g):+.2f} "
                             f"{st.mean(t['y']['cur'] for t in g):+.2f} "
                             f"{sum(t['y']['target'] for t in g) / len(g):3.0%} "
                             f"{sum(t['y']['rug'] for t in g) / len(g):3.0%}")
            L.append(f"  {lab:<22}" + "  |  ".join(cells))
    out = "\n".join(L)
    (HERE / "features_results.txt").write_text(out, encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
