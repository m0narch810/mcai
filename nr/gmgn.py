"""GMGN OpenAPI (read-only): wallet composition, dev history and promotion
status for a token. Key in data/gmgn_key.txt; falls back to GMGN's public
demo key (shared and rate-limited) until then."""
import threading
import time
import uuid

import requests

from .config import DATA_DIR

HOST = "https://openapi.gmgn.ai"
DEMO_KEY = "gmgn_solbscbaseethmonadtron"
MIN_GAP_S = 1.2          # free tier leaky bucket is 5 requests / 5 capacity
_lock = threading.Lock()
_last = 0.0


def _key() -> tuple[str, bool]:
    f = DATA_DIR / "gmgn_key.txt"
    k = f.read_text(encoding="utf-8").strip() if f.exists() else ""
    return (k, False) if k else (DEMO_KEY, True)


def _server_now() -> int:
    """GMGN rejects timestamps more than 5s off its clock."""
    from . import clock
    return int(clock.now())


def _get(path: str, mint: str) -> tuple[dict | None, str | None]:
    global _last
    key, _ = _key()
    for attempt in range(3):
        with _lock:
            wait = MIN_GAP_S - (time.monotonic() - _last)
            if wait > 0:
                time.sleep(wait)
            _last = time.monotonic()
        try:
            r = requests.get(HOST + path, headers={"X-APIKEY": key, "User-Agent": "narrative-research"},
                             params={"chain": "sol", "address": mint,
                                     "timestamp": _server_now(), "client_id": str(uuid.uuid4())},
                             timeout=20)
        except requests.RequestException as e:
            err = f"network: {e.__class__.__name__}"
            time.sleep(2)
            continue
        if r.status_code == 429:
            err = "rate limited"
            time.sleep(5)
            continue
        if r.status_code != 200:
            return None, f"HTTP {r.status_code}: {r.text[:120]}"
        body = r.json()
        if body.get("code") != 0:
            return None, f"api code {body.get('code')}: {body.get('message')}"
        return body.get("data"), None
    return None, err


def _f(x):
    try:
        return round(float(x), 4)
    except (TypeError, ValueError):
        return None


def collect(mint: str) -> dict:
    """Never raises. Every field is as GMGN reports it at T2."""
    _, demo = _key()
    info, err = _get("/v1/token/info", mint)
    out = {"source": "GMGN OpenAPI", "available": info is not None,
           "using_public_demo_key": demo}
    if info is None:
        out["error"] = err
        return out
    sec, sec_err = _get("/v1/token/security", mint)
    stat, tags, dev, link = (info.get(k) or {} for k in ("stat", "wallet_tags_stat", "dev", "link"))
    out.update({
        "holder_count": info.get("holder_count"),
        "wallet_tags": {k: tags.get(k) for k in (
            "smart_wallets", "renowned_wallets", "fresh_wallets", "sniper_wallets",
            "bundler_wallets", "rat_trader_wallets", "whale_wallets", "top_wallets")},
        "wallet_tags_note": ("GMGN wallet classifications among traders. renowned = known "
                             "KOL wallets; rat_trader = insider/sneak wallets."),
        "rates": {
            "bot_degen_rate": _f(stat.get("bot_degen_rate")),
            "fresh_wallet_rate": _f(stat.get("fresh_wallet_rate")),
            "top_bundler_trader_pct": _f(stat.get("top_bundler_trader_percentage")),
            "top_rat_trader_pct": _f(stat.get("top_rat_trader_percentage")),
            "top_entrapment_trader_pct": _f(stat.get("top_entrapment_trader_percentage")),
            "top10_holder_rate": _f(stat.get("top_10_holder_rate")),
            "dev_team_hold_rate": _f(stat.get("dev_team_hold_rate")),
            "creator_hold_rate": _f(stat.get("creator_hold_rate")),
            "top70_sniper_hold_rate": _f(stat.get("top70_sniper_hold_rate")),
        },
        "smart_money_signal_count": stat.get("signal_count"),
        "degen_call_count": stat.get("degen_call_count"),
        "dev": {
            "creator_address": dev.get("creator_address"),
            "creator_token_status": dev.get("creator_token_status"),
            "creator_launched_token_count": stat.get("creator_created_count"),
            "dev_x_account_launched_token_count": dev.get("twitter_create_token_count"),
            "dev_x_account_deleted_token_post_count": dev.get("twitter_del_post_token_count"),
            "project_x_rename_history": [
                {"username": h.get("twitter_username"), "renamed_at_unix": h.get("rename_timestamp")}
                for h in (dev.get("twitter_name_change_history") or [])[:6]],
            "rename_note": ("Older usernames of the project's X account. A long history means "
                            "a recycled account, a common red flag."),
            "funded_from": dev.get("fund_from") or None,
            "creator_best_token_ath_mcap": (dev.get("ath_token_info") or {}).get("ath_mc"),
        },
        "promotion": {
            "dexscreener_paid_ad": bool(dev.get("dexscr_ad")),
            "dexscreener_boost_paid": bool(dev.get("dexscr_boost_fee")),
            "dexscreener_trending_bar_paid": bool(dev.get("dexscr_trending_bar")),
            "dexscreener_profile_updated": bool(dev.get("dexscr_update_link")),
            "community_takeover": bool(dev.get("cto_flag")),
        },
        "project_links": {k: link.get(k) for k in ("twitter_username", "website", "telegram")
                          if link.get(k)},
        "launchpad": info.get("launchpad_platform"),
        "created_unix": info.get("creation_timestamp"),
        "migrated_unix": info.get("migrated_timestamp"),
        "ath_price": info.get("ath_price"),
        "image_duplicate_count": info.get("image_dup_count"),
        "gmgn_page_visits": info.get("visiting_count"),
    })
    if sec:
        out["security"] = {
            "mint_renounced": sec.get("renounced_mint"),
            "freeze_renounced": sec.get("renounced_freeze_account"),
            "honeypot": sec.get("honeypot"), "buy_tax": sec.get("buy_tax"),
            "sell_tax": sec.get("sell_tax"), "flags": sec.get("flags"),
            "lp_burn_ratio": _f(sec.get("burn_ratio")),
        }
    elif sec_err:
        out["security_error"] = sec_err
    return out
