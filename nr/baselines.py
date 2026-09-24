"""Arms A and B: fixed, pre-registered formulas computed from the evidence
packet alone. Equal weights, fixed clipping, no fitting. Each feature is signed
so that "higher = more consistent with continuing attention", for a reason
stated in plain English.

B = A + structured social: X mention breadth and organic share, other
contracts pushed under the same ticker, GMGN wallet composition and paid
promotion, plus the project-link signals.
"""
import math

from .schemas import AUTHENTICITY, CONTINUATION, FEASIBILITY, NARRATIVE_POTENTIAL, \
    TOKEN_CONNECTION


def _clip(x, lo=-3.0, hi=3.0):
    return max(lo, min(hi, x))


def _log_ratio(a, b):
    if not a or not b or a <= 0 or b <= 0:
        return None
    return math.log(a / b)


def arm_a(p: dict) -> float | None:
    trig = p["trigger_at_detection"]
    gt = p["market"]["geckoterminal"]
    s = p["structure"]
    f = []
    # Volume accelerating relative to its own recent baseline.
    if trig.get("vol_accel_m5_vs_h6"):
        f.append(math.log(trig["vol_accel_m5_vs_h6"]))
    # More distinct buyers arriving now than the 6h norm = broadening demand.
    if gt.get("available"):
        ub = gt["unique_buyers"]
        r = _log_ratio((ub.get("m15") or 0) * 4, (ub.get("h6") or 0) / 6)
        if r is not None:
            f.append(r)
    # Net buy pressure over the last hour.
    b, sl = trig.get("h1_buys") or 0, trig.get("h1_sells") or 0
    if b + sl:
        f.append(2 * (b - sl) / (b + sl))
    # Deeper liquidity relative to market cap = less fragile price.
    lr = _log_ratio(trig.get("liquidity_usd"), trig.get("mcap_usd"))
    if lr is not None:
        f.append(lr + 2.3)          # centred on liq/mcap ~ 0.10
    # Structure: broad holder base, low concentration, renounced authorities.
    if s.get("total_holders"):
        f.append(math.log10(s["total_holders"]) - 3)       # 1,000 holders -> 0
    if s.get("top10_holder_pct") is not None:
        f.append(1.5 - s["top10_holder_pct"] / 20)         # 30% -> 0
    if s.get("available"):
        f.append(0.0 if s.get("mint_authority_renounced") and
                 s.get("freeze_authority_renounced") else -2.0)
        f.append(-min(3, s.get("insider_flagged_in_top_holders") or 0))
    f = [_clip(x) for x in f]
    return sum(f) / len(f) if f else None


def arm_b(p: dict) -> float | None:
    a = arm_a(p)
    if a is None:
        return None
    soc = p["social"]
    types = {x["type"] for x in soc["project_socials"] if x.get("type")}
    g = [
        # A real project presence (X account, site, Telegram) = someone building a story.
        1.0 if "twitter" in types else -1.0,
        0.5 if soc["project_websites"] else -0.5,
        0.5 if "telegram" in types else 0.0,
        # Paid boosts are bought attention, not earned attention.
        -1.0 if (soc.get("dexscreener_paid_boosts_active") or 0) > 0 else 0.0,
        # Many same-ticker copies = attention is split or this token is a copy.
        -min(2.0, len(p.get("same_ticker_or_name_tokens") or []) / 3),
    ]
    x = p.get("x_social") or {}
    if x.get("available"):
        cm = x.get("contract_mentions") or {}
        # More distinct people posting this exact contract = broader attention.
        g.append(_clip(math.log1p(cm.get("unique_authors") or 0) - 1.5, -2, 2))
        # Attention from people, not alert bots and call channels.
        if cm.get("promo_or_bot_share") is not None:
            g.append(1.0 - 2 * cm["promo_or_bot_share"])
        # Other contracts pushed under the same ticker = attention is split or stolen.
        others = (x.get("ticker_mentions") or {}).get("other_contracts_pushed_under_same_ticker")
        if others is not None:
            g.append(-min(2.0, len(others) / 2))
    w = p.get("gmgn_wallets_and_dev") or {}
    if w.get("available"):
        r = w.get("rates") or {}
        # Bots, bundles and fresh wallets manufacture volume.
        if r.get("bot_degen_rate") is not None:
            g.append(1.0 - 2 * r["bot_degen_rate"])
        if r.get("top_bundler_trader_pct") is not None:
            g.append(-min(2.0, 4 * r["top_bundler_trader_pct"]))
        # Smart money and known KOL wallets present = informed interest.
        t = w.get("wallet_tags") or {}
        g.append(min(2.0, math.log1p((t.get("smart_wallets") or 0) + 2 * (t.get("renowned_wallets") or 0)) / 2))
        # A recycled project X account is a classic rug setup.
        g.append(-min(2.0, max(0, len((w.get("dev") or {}).get("project_x_rename_history") or []) - 1) / 2))
    return (a * 1.0 + sum(g) / len(g)) / 2


def ordinal(value: str, scale: list[str]) -> float:
    return scale.index(value) / (len(scale) - 1)


def arm_claude(report: dict) -> float:
    """Primary Claude score: p_runner (v3) as 0..1, else the frozen
    continuation_view ordinal (v1/v2 reports have no p_runner)."""
    if report.get("p_runner") is not None:
        return report["p_runner"] / 100
    return ordinal(report["continuation_view"], CONTINUATION)


def claude_components(report: dict) -> dict:
    return {
        "narrative_potential": ordinal(report["narrative"]["potential"], NARRATIVE_POTENTIAL),
        "token_connection": ordinal(report["token_connection"]["assessment"], TOKEN_CONNECTION),
        "market_feasibility": ordinal(report["market_feasibility"]["assessment"], FEASIBILITY),
        "authenticity": ordinal(report["authenticity"]["assessment"], AUTHENTICITY),
    }


def arm_skeptic(s: dict) -> float:
    return ordinal(s["continuation_view"], CONTINUATION)
