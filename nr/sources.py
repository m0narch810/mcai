"""Free public data sources: DexScreener, GeckoTerminal, RugCheck.
Every call is rate-limited per host and retried on 429/5xx."""
import threading
import time
import warnings

import certifi
import requests
from urllib3.exceptions import InsecureRequestWarning

# An unverified HTTPS request must never pass silently: turn urllib3's
# warning into an exception so the call fails and gets logged instead.
warnings.filterwarnings("error", category=InsecureRequestWarning)

_UA = {"accept": "application/json", "user-agent": "narrative-research/0.1"}
# Minimum seconds between calls per host (GeckoTerminal free tier ~30/min).
_MIN_GAP = {"api.geckoterminal.com": 2.2, "api.dexscreener.com": 0.25,
            "api.rugcheck.xyz": 1.0}
_last: dict[str, float] = {}
_locks: dict[str, threading.Lock] = {h: threading.Lock() for h in _MIN_GAP}
_session = requests.Session()
_session.headers.update(_UA)
_session.verify = certifi.where()


def tls_selfcheck() -> str | None:
    """Returns an error string if certificate verification is not working."""
    try:
        requests.get("https://self-signed.badssl.com/", timeout=15, verify=certifi.where())
        return "self-signed certificate was ACCEPTED: TLS verification is off"
    except requests.exceptions.SSLError:
        return None                      # correctly rejected
    except InsecureRequestWarning as e:
        return f"unverified request attempted: {e}"
    except requests.RequestException:
        return None                      # network issue, not a TLS problem


def get(url: str, params=None, tries: int = 4):
    host = url.split("/")[2]
    lock = _locks.setdefault(host, threading.Lock())
    for attempt in range(tries):
        with lock:
            gap = _MIN_GAP.get(host, 0.5) - (time.monotonic() - _last.get(host, 0))
            if gap > 0:
                time.sleep(gap)
            _last[host] = time.monotonic()
        try:
            r = _session.get(url, params=params, timeout=25)
        except InsecureRequestWarning as e:
            from . import db
            db.log("error", f"refused unverified HTTPS to {host}: {e}")
            return None
        except requests.RequestException:
            time.sleep(2 * (attempt + 1))
            continue
        if r.status_code == 200:
            return r.json()
        if r.status_code == 404:
            return None
        if r.status_code in (429, 500, 502, 503, 504):
            time.sleep(5 * (attempt + 1))
            continue
        return None
    return None


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- discovery
def discover_tokens() -> set[str]:
    """Token mints worth checking against the candidate rule this cycle.
    Discovery sources only nominate; the rule itself decides."""
    mints: set[str] = set()
    # Trending over several windows so older tokens that are re-accelerating
    # are nominated, not just fresh launches.
    for duration, pages in (("5m", (1, 2, 3)), ("1h", (1, 2)), ("6h", (1, 2))):
        for page in pages:
            d = get("https://api.geckoterminal.com/api/v2/networks/solana/trending_pools",
                    {"page": page, "duration": duration})
            for p in (d or {}).get("data", []):
                mints.add(p["relationships"]["base_token"]["data"]["id"].split("_", 1)[1])
    for page in (1, 2):
        d = get("https://api.geckoterminal.com/api/v2/networks/solana/pools",
                {"page": page, "sort": "h24_volume_usd_desc"})
        for p in (d or {}).get("data", []):
            mints.add(p["relationships"]["base_token"]["data"]["id"].split("_", 1)[1])
    # Pools created in the last few hours. The trending lists lag a fresh
    # launch by design, and since the age floor dropped to 35 minutes the
    # 35m-2h band is in scope and needs its own feed.
    for page in (1, 2):
        d = get("https://api.geckoterminal.com/api/v2/networks/solana/new_pools",
                {"page": page})
        for p in (d or {}).get("data", []):
            mints.add(p["relationships"]["base_token"]["data"]["id"].split("_", 1)[1])
    for url in ("https://api.dexscreener.com/token-boosts/latest/v1",
                "https://api.dexscreener.com/token-profiles/latest/v1",
                "https://api.dexscreener.com/token-boosts/top/v1"):
        for t in get(url) or []:
            if t.get("chainId") == "solana":
                mints.add(t["tokenAddress"])
    return mints


# ---------------------------------------------------------------- market data
def dex_pairs(mints: list[str], quote_tokens: list[str] | None = None) -> dict[str, list[dict]]:
    """Solana pairs per mint. The batch endpoint returns only each token's
    main pair; when that isn't a liquid SOL/USDC pool (e.g. a bonding curve
    or an exotic quote) the token's full pair list is fetched."""
    out: dict[str, list[dict]] = {}
    for i in range(0, len(mints), 30):
        chunk = mints[i:i + 30]
        for p in get("https://api.dexscreener.com/tokens/v1/solana/" + ",".join(chunk)) or []:
            out.setdefault(p["baseToken"]["address"], []).append(p)
    if quote_tokens:
        for mint in mints:
            if best_pair(out.get(mint, []), quote_tokens) is None:
                full = get(f"https://api.dexscreener.com/token-pairs/v1/solana/{mint}")
                if full:
                    out[mint] = full
    return out


def best_pair(pairs: list[dict], quote_tokens: list[str]) -> dict | None:
    """Deepest pair quoted in SOL/USDC."""
    ok = [p for p in pairs if p.get("quoteToken", {}).get("address") in quote_tokens
          and (p.get("liquidity") or {}).get("usd")]
    return max(ok, key=lambda p: p["liquidity"]["usd"]) if ok else None


def pair_now(pair_address: str) -> dict | None:
    d = get(f"https://api.dexscreener.com/latest/dex/pairs/solana/{pair_address}")
    pairs = (d or {}).get("pairs") or []
    return pairs[0] if pairs else None


def gt_pool(pool: str) -> dict | None:
    d = get(f"https://api.geckoterminal.com/api/v2/networks/solana/pools/{pool}")
    return (d or {}).get("data", {}).get("attributes")


def ohlcv_minutes(pool: str, before_ts: int, limit: int = 1000) -> list[list]:
    """1-minute bars [ts, o, h, l, c, v] ending before before_ts, oldest first."""
    d = get(f"https://api.geckoterminal.com/api/v2/networks/solana/pools/{pool}/ohlcv/minute",
            {"aggregate": 1, "limit": limit, "before_timestamp": before_ts,
             "currency": "usd", "token": "base"})
    bars = (d or {}).get("data", {}).get("attributes", {}).get("ohlcv_list") or []
    return sorted(bars, key=lambda b: b[0])


# ---------------------------------------------------------------- structure
def rugcheck(mint: str) -> dict | None:
    return get(f"https://api.rugcheck.xyz/v1/tokens/{mint}/report")


def structure_summary(rc: dict | None) -> dict:
    """Reduce the RugCheck report to the structural fields in the packet."""
    if not rc:
        return {"available": False}
    known = rc.get("knownAccounts") or {}
    raw_holders = rc.get("topHolders") or []
    # Pool vaults and lockers hold supply but are not holders; label and drop.
    holders = []
    for h in raw_holders:
        k = known.get(h.get("owner")) or known.get(h.get("address")) or {}
        h = dict(h, _known=k.get("name"), _ktype=k.get("type"))
        if k.get("type") not in ("AMM", "LOCKER"):
            holders.append(h)
    top10 = sum((h.get("pct") or 0) for h in holders[:10])
    insiders = sum(1 for h in holders if h.get("insider"))
    markets = rc.get("markets") or []
    lp_locked = [round(m.get("lp", {}).get("lpLockedPct") or 0, 2) for m in markets[:3]]
    return {
        "available": True,
        "mint_authority_renounced": rc.get("mintAuthority") is None,
        "freeze_authority_renounced": rc.get("freezeAuthority") is None,
        "creator": rc.get("creator"),
        "creator_balance": rc.get("creatorBalance"),
        "creator_other_tokens": len(rc.get("creatorTokens") or []),
        "total_holders": rc.get("totalHolders") or None,
        "top10_holder_pct": round(top10, 2) if holders else None,
        "top10_holder_pct_note": "excludes accounts RugCheck labels AMM/LOCKER",
        "excluded_pool_or_locker_accounts": [
            {"name": h.get("_known"), "pct": round(h.get("pct") or 0, 2)}
            for h in (dict(x, _known=(known.get(x.get("owner")) or {}).get("name"),
                           _ktype=(known.get(x.get("owner")) or {}).get("type"))
                      for x in raw_holders) if h["_ktype"] in ("AMM", "LOCKER")],
        "top_holders": [{"address": h.get("owner") or h.get("address"),
                         "pct": round(h.get("pct") or 0, 2),
                         "insider": bool(h.get("insider")),
                         "label": h.get("_known")} for h in holders[:10]],
        "insider_flagged_in_top_holders": insiders,
        "insider_networks": [{"size": n.get("size"), "type": n.get("type"),
                              "token_pct": n.get("tokenAmount")}
                             for n in (rc.get("insiderNetworks") or [])[:5]],
        "graph_insiders_detected": rc.get("graphInsidersDetected"),
        "lp_locked_pct_top_markets": lp_locked,
        "total_market_liquidity_usd": rc.get("totalMarketLiquidity"),
        "launchpad": (rc.get("launchpad") or {}).get("name"),
        "deploy_platform": rc.get("deployPlatform"),
        "rugged_flag": rc.get("rugged"),
        "risks": [{"name": r.get("name"), "level": r.get("level"),
                   "description": r.get("description")} for r in rc.get("risks") or []],
        "transfer_fee_pct": (rc.get("transferFee") or {}).get("pct"),
    }


f = _f
