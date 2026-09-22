"""X/Twitter evidence via TwitterAPI.io (key in data/twitterapi_key.txt).

Collected by code at T2, before Claude starts, and only ever for the past
(until_time = now), so it is information that existed at decision time.
The contract-address search is the clean signal; the ticker search is used
mainly to find OTHER contracts being pushed under the same ticker."""
import re
import statistics as st
import threading
import time
from datetime import datetime, timezone

import requests

from .config import DATA_DIR

API = "https://api.twitterapi.io/twitter/tweet/advanced_search"
MIN_GAP_S = 5.5          # free tier: 1 request / 5 s (paid tiers allow more)
_lock = threading.Lock()
_last = 0.0
BASE58 = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")
# Accounts/posts that are automated alerts or paid-call channels, not people.
PROMO = re.compile(r"alert|signal|radar|detect|scanner|watcher|ping|sniper|bot\b|"
                   r"calls?\b|gem|degen|pump_|smart ?wallet|x profit|\d+x\b|entry shared",
                   re.IGNORECASE)


def _key() -> str | None:
    f = DATA_DIR / "twitterapi_key.txt"
    return f.read_text(encoding="utf-8").strip() if f.exists() else None


def _search(query: str) -> tuple[list | None, str | None]:
    global _last
    key = _key()
    if not key:
        return None, "no TwitterAPI.io key"
    for attempt in range(3):
        with _lock:
            wait = MIN_GAP_S - (time.monotonic() - _last)
            if wait > 0:
                time.sleep(wait)
            _last = time.monotonic()
        try:
            r = requests.get(API, headers={"X-API-Key": key},
                             params={"query": query, "queryType": "Latest"}, timeout=30)
        except requests.RequestException as e:
            err = f"network: {e.__class__.__name__}"
            continue
        if r.status_code == 200:
            return r.json().get("tweets") or [], None
        err = f"HTTP {r.status_code}: {r.text[:120]}"
        if r.status_code == 429:
            time.sleep(6)
            continue
        break   # 401/402 (bad key / out of credit) etc.: don't retry
    return None, err


def _age_days(created: str | None, ref: datetime) -> int | None:
    try:
        return (ref - datetime.strptime(created, "%a %b %d %H:%M:%S %z %Y")).days
    except (TypeError, ValueError):
        return None


def _is_promo(t: dict) -> bool:
    a = t.get("author") or {}
    return bool(PROMO.search(a.get("userName") or "") or PROMO.search(a.get("name") or "")
                or PROMO.search(t.get("text") or ""))


def summarise(tweets: list, ref: datetime) -> dict:
    authors = {t["author"]["userName"]: t["author"] for t in tweets if t.get("author")}
    fol = [a.get("followers") or 0 for a in authors.values()]
    ages = [x for x in (_age_days(a.get("createdAt"), ref) for a in authors.values())
            if x is not None]
    promo = [t for t in tweets if _is_promo(t)]
    return {
        "posts": len(tweets),
        "unique_authors": len(authors),
        "median_author_followers": st.median(fol) if fol else None,
        "max_author_followers": max(fol) if fol else None,
        "authors_over_10k_followers": sum(f >= 10_000 for f in fol),
        "authors_account_under_30d": sum(a < 30 for a in ages),
        "median_views": st.median([t.get("viewCount") or 0 for t in tweets]) if tweets else None,
        "promo_or_bot_share": round(len(promo) / len(tweets), 2) if tweets else None,
        "promo_note": "heuristic: alert/signal/scanner/call-channel wording in name or text",
        "top_posts": [{
            "author": t["author"].get("userName"),
            "followers": t["author"].get("followers"),
            "account_age_days": _age_days(t["author"].get("createdAt"), ref),
            "blue": t["author"].get("isBlueVerified"),
            "created_at": t.get("createdAt"),
            "views": t.get("viewCount"), "likes": t.get("likeCount"),
            "retweets": t.get("retweetCount"),
            "looks_promo": _is_promo(t),
            "text": " ".join((t.get("text") or "").split())[:280],
            "url": t.get("url"),
        } for t in sorted(tweets, key=lambda t: -((t.get("author") or {}).get("followers") or 0))[:8]],
    }


def collect(mint: str, symbol: str | None) -> dict:
    """X evidence for the 24h before now. Never raises."""
    now = datetime.now(timezone.utc)
    until, since = int(now.timestamp()), int(now.timestamp()) - 86400
    out = {"source": "TwitterAPI.io advanced search", "window": "24h before T2",
           "available": False}
    ca_tweets, err = _search(f'"{mint}" since_time:{since} until_time:{until}')
    if ca_tweets is None:
        out["error"] = err
        return out
    out["available"] = True
    out["contract_mentions"] = summarise(ca_tweets, now)
    out["contract_mentions"]["note"] = ("Posts containing this exact contract address. "
                                        "First page only (up to ~20 most recent).")
    if symbol:
        tk, err = _search(f'${symbol} (solana OR sol OR pump OR CA) '
                          f'since_time:{since} until_time:{until}')
        if tk is None:
            out["ticker_search_error"] = err
        else:
            others: dict[str, int] = {}
            for t in tk:
                for ca in BASE58.findall(t.get("text") or ""):
                    if ca != mint and len(ca) >= 32:
                        others[ca] = others.get(ca, 0) + 1
            out["ticker_mentions"] = {
                "posts": len(tk),
                "unique_authors": len({(t.get("author") or {}).get("userName") for t in tk}),
                "posts_citing_this_contract": sum(mint in (t.get("text") or "") for t in tk),
                "other_contracts_pushed_under_same_ticker": sorted(
                    ({"contract": k, "posts": v} for k, v in others.items()),
                    key=lambda x: -x["posts"])[:8],
                "note": (f"'${symbol}' plus crypto terms. Generic tickers pull unrelated "
                         "chatter; the useful part is which contracts are being pushed."),
            }
    return out
