"""Evidence packet: a structured snapshot collected by code at T2, before any
Claude involvement. Collectors provide evidence; Claude provides interpretation."""
import json

from . import db, gmgn, sources, xdata
from .config import PREREG


def _gt_window(attrs: dict | None) -> dict:
    if not attrs:
        return {"available": False}
    tx = attrs.get("transactions") or {}
    vol = attrs.get("volume_usd") or {}
    return {
        "available": True,
        "unique_buyers": {w: (tx.get(w) or {}).get("buyers") for w in ("m5", "m15", "m30", "h1", "h6", "h24")},
        "unique_sellers": {w: (tx.get(w) or {}).get("sellers") for w in ("m5", "m15", "m30", "h1", "h6", "h24")},
        "buys": {w: (tx.get(w) or {}).get("buys") for w in ("m5", "m15", "m30", "h1", "h6", "h24")},
        "sells": {w: (tx.get(w) or {}).get("sells") for w in ("m5", "m15", "m30", "h1", "h6", "h24")},
        "volume_usd": {w: sources.f(vol.get(w)) for w in ("m5", "m15", "m30", "h1", "h6", "h24")},
        "price_change_pct": attrs.get("price_change_percentage"),
        "pool_created_at": attrs.get("pool_created_at"),
    }


def _competitors(symbol: str | None, name: str | None, mint: str) -> list[dict]:
    """Other Solana tokens sharing the ticker or name, for the
    'why this token?' question. Deepest pair per token."""
    seen: dict[str, dict] = {}
    for q in {x for x in (symbol, name) if x}:
        d = sources.get("https://api.dexscreener.com/latest/dex/search", {"q": q})
        for p in (d or {}).get("pairs") or []:
            b = p.get("baseToken") or {}
            if p.get("chainId") != "solana" or b.get("address") == mint:
                continue
            if (b.get("symbol") or "").lower() != (symbol or "").lower() and \
               (b.get("name") or "").lower() != (name or "").lower():
                continue
            liq = (p.get("liquidity") or {}).get("usd") or 0
            cur = seen.get(b["address"])
            if not cur or liq > cur["liquidity_usd"]:
                seen[b["address"]] = {
                    "token": b["address"], "symbol": b.get("symbol"), "name": b.get("name"),
                    "liquidity_usd": liq, "mcap_usd": p.get("marketCap") or p.get("fdv"),
                    "volume_h1": (p.get("volume") or {}).get("h1"),
                    "pair_created_at_ms": p.get("pairCreatedAt")}
    return sorted(seen.values(), key=lambda x: -(x["mcap_usd"] or 0))[:10]


def build(candidate: dict) -> dict:
    mint, pool = candidate["token"], candidate["pair_address"]
    limitations = []
    pair = sources.pair_now(pool)
    if not pair:
        limitations.append("DexScreener pair lookup failed at T2")
    gt = sources.gt_pool(pool)
    if not gt:
        limitations.append("GeckoTerminal pool stats (unique buyers/sellers) unavailable")
    rc = sources.rugcheck(mint)
    structure = sources.structure_summary(rc)
    gti = (sources.get(f"https://api.geckoterminal.com/api/v2/networks/solana/tokens/{mint}/info")
           or {}).get("data", {}).get("attributes") or {}
    gth = gti.get("holders") or {}
    structure["geckoterminal_holders"] = {
        "count": gth.get("count"),
        "distribution_pct": gth.get("distribution_percentage"),
        "last_updated": gth.get("last_updated"),
        "note": "GeckoTerminal figures; may include pool/locker accounts",
    } if gth else None
    if not structure["available"]:
        limitations.append("RugCheck structural report unavailable")
    if not structure.get("top_holders"):
        limitations.append("RugCheck returned no top-holder list"
                           + ("; GeckoTerminal holder distribution used instead" if gth else ""))
        if gth:
            structure["total_holders"] = structure.get("total_holders") or gth.get("count")
            top10 = (gth.get("distribution_percentage") or {}).get("top_10")
            if top10 is not None and structure.get("top10_holder_pct") is None:
                structure["top10_holder_pct"] = round(float(top10), 2)
                structure["top10_holder_pct_note"] = "from GeckoTerminal (may include pools)"
    comps = _competitors(candidate.get("symbol"), candidate.get("name"), mint)
    info = (pair or {}).get("info") or {}
    if PREREG.get("x_social_enabled"):
        x_social = xdata.collect(mint, candidate.get("symbol"))
        if not x_social.get("available"):
            limitations.append(f"X/Twitter data unavailable: {x_social.get('error')}")
        else:
            limitations.append("X data = first page (~20 most recent posts) per search, last 24h; "
                               "not a full census of the conversation")
    else:
        x_social = {"available": False, "error": "X data not collected in this version"}
        limitations.append("No X/Twitter post data (no paid X API). X-derived signals come only "
                           "from GMGN (project X account rename history, tokens launched by that "
                           "account); the researcher must use web search for posts")
    wallets = gmgn.collect(mint)
    if not wallets.get("available"):
        limitations.append(f"GMGN wallet/dev data unavailable: {wallets.get('error')}")
    elif wallets.get("using_public_demo_key"):
        limitations.append("GMGN data fetched with the shared public demo key")
    limitations.append("Private Telegram/Discord content is invisible")
    packet = {
        "token": {
            "contract": mint, "chain": "solana",
            "symbol": candidate.get("symbol"), "name": candidate.get("name"),
            "pair_address": pool, "dex": candidate.get("dex_id"),
            "pair_created_at_ms": (pair or {}).get("pairCreatedAt"),
            "market_cap_usd": (pair or {}).get("marketCap"),
            "fdv_usd": (pair or {}).get("fdv"),
            "liquidity_usd": ((pair or {}).get("liquidity") or {}).get("usd"),
            "price_usd": sources.f((pair or {}).get("priceUsd")),
        },
        "trigger_at_detection": json.loads(candidate["trigger_json"]),
        "market": {
            "dexscreener_txns": (pair or {}).get("txns"),
            "dexscreener_volume": (pair or {}).get("volume"),
            "dexscreener_price_change_pct": (pair or {}).get("priceChange"),
            "geckoterminal": _gt_window(gt),
        },
        "structure": structure,
        "social": {
            "project_websites": [w.get("url") for w in info.get("websites") or []],
            "project_socials": [{"type": s.get("type"), "url": s.get("url")}
                                for s in info.get("socials") or []],
            "dexscreener_paid_boosts_active": ((pair or {}).get("boosts") or {}).get("active"),
            "dexscreener_profile_present": bool(info),
            "geckoterminal_twitter_handle": gti.get("twitter_handle"),
            "geckoterminal_telegram_handle": gti.get("telegram_handle"),
            "project_description": gti.get("description") or None,
            "note": "Boosts are paid promotion on DexScreener, not organic attention.",
        },
        "same_ticker_or_name_tokens": comps,
        "x_social": x_social,
        "gmgn_wallets_and_dev": wallets,
        "data_limitations": limitations,
        "timestamps": {"t1_detected": candidate["t1_detected"],
                       "t2_collected": db.iso(db.now_utc()),
                       "t_decision": candidate["t_decision"]},
        "prereg_version": candidate["prereg_version"],
    }
    return packet


def collect(candidate: dict) -> dict:
    p = build(candidate)
    h = db.sha(p)
    db.conn().execute("INSERT INTO packets VALUES (?,?,?,?)",
                      (candidate["id"], p["timestamps"]["t2_collected"], json.dumps(p), h))
    db.ledger("packet", {"candidate_id": candidate["id"], "sha256": h})
    return p


def load(candidate_id: int) -> dict | None:
    r = db.conn().execute("SELECT packet_json FROM packets WHERE candidate_id=?",
                          (candidate_id,)).fetchone()
    return json.loads(r[0]) if r else None
