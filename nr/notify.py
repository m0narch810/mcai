"""Discord updates via webhook. Put the webhook URL in data/discord_webhook.txt
(or the NR_DISCORD_WEBHOOK env var). Optional side channels get a copy of
closed-trade cards with an @everyone ping: data/discord_webhook_tp.txt (trades
that made money) and data/discord_webhook_stops.txt (trades that lost), or
NR_DISCORD_WEBHOOK_TP / NR_DISCORD_WEBHOOK_STOPS. data/discord_webhook_entries.txt
(NR_DISCORD_WEBHOOK_ENTRIES) gets a copy of each ENTRY card, without a ping. Sending is fire-and-forget on a background
thread: a Discord outage can never stall or crash the experiment."""
import json
import os
import queue
import threading
import time

import requests

from .config import DATA_DIR, PREREG, RUNTIME

_q: "queue.Queue[tuple[str | None, dict]]" = queue.Queue(maxsize=200)
_started = False
_last_error_sent = 0.0

GREEN, RED, GREY, BLUE, AMBER = 0x2ECC71, 0xE74C3C, 0x95A5A6, 0x3498DB, 0xF1C40F
PING_CHANNELS = ("tp", "stops")
BULLISH = ("continue", "strong_continue")
# v3/v4 fixed gate (1/3 break-even plus a margin). v5 reports carry a frozen
# rank gate instead; this only interprets reports written under v3/v4.
LEGACY_P_RUNNER_MIN = 35


def is_trade(rep: dict | None) -> bool:
    """Would this report open the position? v5: the rank gate frozen with the
    report. v3/v4: p_runner at or above the fixed threshold. Older: a
    continue call."""
    if not rep:
        return False
    if rep.get("_gate") is not None:
        return bool(rep["_gate"]["take"])
    if rep.get("p_runner") is not None:
        return rep["p_runner"] >= LEGACY_P_RUNNER_MIN
    return rep.get("continuation_view") in BULLISH


def _label(rep: dict) -> str:
    v = rep["continuation_view"].replace("_", " ")
    if rep.get("p_runner") is None:
        return v
    s = f"{rep['p_runner']}% runner / {rep.get('p_rug', '?')}% rug"
    if rep.get("tail_class"):
        s += f" · tail {rep['tail_class']}"
    g = rep.get("_gate") or {}
    if g.get("threshold") is not None:
        s += f" · bar {g['threshold']}"
    # The trade is decided by p_runner's rank, not by the word view; printing
    # "(fade)" on an ENTRY card read as "this trade lost".
    return s if g.get("take") else f"{s} ({v})"


def _all() -> bool:
    return RUNTIME.get("discord_mode") == "all"


VIEW_COLOR = {"strong_continue": GREEN, "continue": GREEN, "neutral": GREY,
              "fade": AMBER, "strong_fade": RED}


def _url(channel: str | None = None) -> str | None:
    """Webhook for the main channel, or for a side channel ("tp", "stops")."""
    sfx = f"_{channel}" if channel else ""
    u = os.environ.get("NR_DISCORD_WEBHOOK" + sfx.upper())
    f = DATA_DIR / f"discord_webhook{sfx}.txt"
    if not u and f.exists():
        u = f.read_text(encoding="utf-8").strip()
    return u if u and u.startswith("https://") else None


def _worker():
    while True:
        channel, payload = _q.get()
        last_exc = None
        url = _url(channel)
        if not url:
            continue
        for attempt in range(4):
            try:
                r = requests.post(url, json=payload, timeout=15)
                if r.status_code == 429:
                    time.sleep(float(r.json().get("retry_after", 2)) + 0.5)
                    continue
                if r.status_code >= 400:
                    with open(DATA_DIR / "discord_errors.log", "a", encoding="utf-8") as f:
                        f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} HTTP {r.status_code} "
                                f"{r.text[:300]} :: {json.dumps(payload)[:300]}\n")
                break
            except requests.RequestException as e:
                last_exc = e
                time.sleep(3 * (attempt + 1))
        else:
            # Every attempt raised. Without this the message vanishes with no
            # trace anywhere, which is indistinguishable from "nothing was
            # worth sending" - the exact failure this file is meant to report.
            try:
                with open(DATA_DIR / "discord_errors.log", "a", encoding="utf-8") as f:
                    f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} gave up after "
                            f"4 attempts: {last_exc!r} :: {json.dumps(payload)[:300]}" + chr(10))
            except OSError:
                pass
        time.sleep(1)   # stay well under Discord's webhook rate limit


def enabled() -> bool:
    return _url() is not None


def send(title: str, desc: str = "", color: int = BLUE, fields: list | None = None,
         url: str | None = None, also: str | None = None):
    """Post an embed to the main channel, plus a copy to side channel `also`
    ("tp" / "stops" with an @everyone ping, "entries" without) when that
    webhook is configured."""
    global _started
    targets = [ch for ch in [None] + ([also] if also else []) if _url(ch)]
    if not targets:
        return
    if not _started:
        threading.Thread(target=_worker, daemon=True, name="discord").start()
        _started = True
    embed = {"title": title[:256], "description": desc[:4000], "color": color}
    if fields:
        # Discord rejects empty field names/values; use a zero-width space.
        embed["fields"] = [{"name": (n or "​")[:256],
                            "value": (str(v) if v not in (None, "") else "-")[:1024],
                            "inline": i}
                           for n, v, i in fields][:25]
    if url:
        embed["url"] = url
    for ch in targets:
        payload = {"username": "Narrative Researcher", "embeds": [embed]}
        if ch in PING_CHANNELS:
            payload |= {"content": "@everyone", "allowed_mentions": {"parse": ["everyone"]}}
        try:
            _q.put_nowait((ch, payload))
        except queue.Full:
            pass


def send_sync(title: str, desc: str) -> tuple[bool, str]:
    """Blocking send for `run.py discord-test`."""
    url = _url()
    if not url:
        return False, "no webhook URL in data/discord_webhook.txt"
    r = requests.post(url, json={"username": "Narrative Researcher",
                                 "embeds": [{"title": title, "description": desc,
                                             "color": GREEN}]}, timeout=15)
    return r.status_code in (200, 204), f"HTTP {r.status_code} {r.text[:200]}"


# ------------------------------------------------------------------ messages
def _dex(token):
    return f"https://dexscreener.com/solana/{token}"


def _links(c: dict) -> tuple:
    t, pair = c["token"], c["pair_address"]
    return ("Chart", f"[GMGN](https://gmgn.ai/sol/token/{t}) · "
                     f"[Axiom](https://axiom.trade/meme/{pair}?chain=sol) · "
                     f"[Padre](https://trade.padre.gg/trade/solana/{t}) · "
                     f"[DexScreener]({_dex(t)})\n`{t}`", False)


def report_frozen(c: dict, arm: str, rep: dict, late: bool, cost: float):
    if not _all():
        if arm == "C" and RUNTIME.get("discord_verdict_lines"):
            _verdict_line(c, rep, late, cost)
        return
    if arm == "C":
        send(f"🔎 #{c['id']} {c.get('symbol')}: {rep['continuation_view'].replace('_', ' ')}",
             rep["thesis"], VIEW_COLOR.get(rep["continuation_view"], GREY), [
                 ("Narrative", rep["narrative"]["potential"], True),
                 ("Token link", rep["token_connection"]["assessment"], True),
                 ("Feasibility", rep["market_feasibility"]["assessment"], True),
                 ("Authenticity", rep["authenticity"]["assessment"], True),
                 ("Confidence", rep["research_confidence"], True),
                 ("X coverage", rep["source_coverage"]["x_twitter"], True),
                 ("Counterargument", rep["counterargument"], False),
                 _links(c),
                 ("", f"cost ${cost:.2f}{' · ⚠️ LATE (excluded)' if late else ''}", False),
             ], url=_dex(c["token"]))
    elif arm == "S":
        send(f"😈 #{c['id']} {c.get('symbol')}: skeptic says {rep['bear_case_strength']} bear case",
             rep["strongest_bear_case"], RED if rep["bear_case_strength"] == "strong" else AMBER,
             [("Skeptic view", rep["continuation_view"], True), _links(c)],
             url=_dex(c["token"]))


def _verdict_line(c: dict, rep: dict, late: bool, cost: float):
    """One compact line per researched candidate, fades included. A night of
    nothing but fades must not look the same as a night of nothing at all."""
    view = rep["continuation_view"]
    if is_trade(rep):
        return          # the full ENTRY card covers these
    t = json.loads(c["trigger_json"]) if isinstance(c.get("trigger_json"), str) else {}
    send(f"· #{c['id']} {c.get('symbol')}: {_label(rep)}",
         rep["thesis"][:300], VIEW_COLOR.get(view, GREY),
         [("Setup", f"mcap ${t.get('mcap_usd', 0):,.0f} · liq ${t.get('liquidity_usd', 0):,.0f} · "
                    f"age {t.get('pair_age_min', 0):.0f}m · turnover {t.get('h1_turnover', 0)}x/h", False),
          _links(c),
          ("", f"cost ${cost:.2f}{' · LATE (excluded)' if late else ''}", False)],
         url=_dex(c["token"]))


_last_quiet = 0.0


def quiet(hours: float, funnel: dict):
    """Periodic 'alive, nothing qualified' note during a dry spell."""
    global _last_quiet
    gap = RUNTIME.get("quiet_heartbeat_hours", 3) * 3600
    if time.time() - _last_quiet < gap:
        return
    _last_quiet = time.time()
    top = sorted(((k.split(":", 1)[1], v) for k, v in funnel.items()
                  if k.startswith("sole_blocker:")), key=lambda x: -x[1])[:3]
    send(f"😴 Nothing qualified in {hours:.1f}h", "The loop is alive; the rule "
         "just has not fired.", GREY,
         [("Cycles", str(funnel.get("cycles", 0)), True),
          ("Nominated", str(funnel.get("nominated", 0)), True),
          ("Rejected", str(funnel.get("rejected", 0)), True),
          ("Closest misses", ", ".join(f"{k} ({v})" for k, v in top) or "none", False)])


_paused: str | None = None


def research_paused(reason: str | None):
    """Post once when research stops for budget/session reasons and once when it
    resumes. Candidates still get logged as `no_budget` meanwhile, so without
    this a paused bot is silent on Discord and looks dead."""
    global _paused
    was, _paused = _paused, reason
    if (was is None) == (reason is None):
        return
    if reason:
        send("⏸️ Research paused", f"Capped by {reason}. Detection keeps running; "
             "new candidates are logged as `no_budget` until spend rolls off.", AMBER)
    else:
        send("▶️ Research resumed", f"Was capped by {was}.", GREEN)


def _mc(x: float | None) -> str:
    """Market cap the way traders quote it: $67.6k, $1.24M."""
    if not x:
        return "n/a"
    return f"${x / 1e6:.2f}M" if x >= 1e6 else f"${x / 1e3:.1f}k"


def entry(c: dict, rep: dict, skeptic: dict | None, price: float | None,
          liq: float | None, mcap: float | None):
    """One message per token Claude would take: verdict + paper entry.
    Levels are quoted as market caps (supply is fixed, so mcap scales with
    price); the fill itself is priced off the next bar, so they are ~."""
    fields = [
        ("Entry mcap", _mc(mcap), True),
        ("Liquidity", f"${liq:,.0f}" if liq else "n/a", True),
        ("Narrative", rep["narrative"]["potential"], True),
        ("Token link", rep["token_connection"]["assessment"], True),
        ("Confidence", rep["research_confidence"], True),
        ("Counterargument", rep["counterargument"], False),
    ]
    if skeptic:
        fields.append(("😈 Skeptic", f"{skeptic['bear_case_strength']} bear case, says "
                       f"{skeptic['continuation_view'].replace('_', ' ')}: "
                       f"{skeptic['strongest_bear_case']}", False))
    fields.append(_links(c))
    tp, sl = PREREG.get("bracket_target"), PREREG.get("bracket_stop")
    mm = PREREG.get("bracket_max_minutes") or 360
    hold = f"{mm} min" if mm < 60 else f"{mm // 60}h"
    plan = (f"$250 paper position: take profit {tp:+.0%} (~{_mc(mcap and mcap * (1 + tp))} mcap), "
            f"stop {sl:+.0%} (~{_mc(mcap and mcap * (1 + sl))}), else sell at market after {hold}."
            if tp is not None else "$250 paper position, main measure = 6h result.")
    send(f"🟢 ENTRY #{c['id']} {c.get('symbol')}: Claude says {_label(rep)}",
         rep["thesis"] + "\n\n" + plan,
         GREEN, fields, url=_dex(c["token"]), also="entries" if is_trade(rep) else None)


def outcome(c: dict, out: dict, rep: dict | None):
    if not _all() and not is_trade(rep):
        return
    view = _label(rep) if rep else None
    r = out.get("returns", {})
    fmt = lambda k: "n/a" if r.get(k) is None else f"{r[k]:+.1%}"
    net = r.get("1440")
    color = GREY if net is None else GREEN if net > 0 else RED
    who = f"Claude said **{view}**" if view else "control (not researched)"
    send(f"📈 #{c['id']} {c.get('symbol')}: final 24h result {fmt('1440')} (6h {fmt('360')})",
         f"{who}. Net of fees, impact and slippage on a $250 paper position.", color, [
             ("1h", fmt("60"), True), ("6h", fmt("360"), True), ("24h", fmt("1440"), True),
             ("Max up 6h", f"{out['mfe_6h']:+.0%}" if out.get("mfe_6h") is not None else "n/a", True),
             ("Max down 6h", f"{out['mae_6h']:+.0%}" if out.get("mae_6h") is not None else "n/a", True),
             ("Liquidity collapse", "yes" if out.get("liquidity_collapse") else "no", True),
             _links(c),
         ], url=_dex(c["token"]))


def bracket_hit(c: dict, b: dict, rep: dict, mfe: float | None, out: dict | None = None):
    """Posted the moment a taken trade closes: take profit, stop, or the
    time limit. Winners are copied to the TP channel, losers and flat exits
    to the stops channel. Prices are shown as market caps: mcap per unit of
    price is fixed by the entry quote (supply does not change)."""
    tp, sl = PREREG["bracket_target"], PREREG["bracket_stop"]
    out = out or {}
    k = (out["entry_mcap"] / out["entry_quote"]
         if out.get("entry_mcap") and out.get("entry_quote") else None)
    mc = lambda px: _mc(k * px) if k and px else "n/a"
    entry_ref = out.get("entry_ref")
    net, mins = b["net"], b.get("exit_min") or 0
    if b["result"] == "target":
        title = f"🎯 TAKE PROFIT #{c['id']} {c.get('symbol')}: sold at {tp:+.0%} after {mins:.0f} min · net {net:+.1%}"
        color = GREEN
    elif b["result"] == "stop":
        title = f"🛑 STOPPED OUT #{c['id']} {c.get('symbol')}: sold after {mins:.0f} min · net {net:+.1%}"
        color = RED
    else:
        title = (f"⏱️ TIME LIMIT #{c['id']} {c.get('symbol')}: sold at market after "
                 f"{PREREG['bracket_max_minutes']} min · net {net:+.1%}")
        color = GREEN if net > 0 else RED if net < 0 else GREY
    desc = (f"Claude said **{_label(rep)}**. Net of fees, impact and slippage on the "
            f"${PREREG['position_usd']:.0f} paper position.")
    if b["result"] == "stop" and net <= -0.99:
        desc += " Liquidity was pulled or the price gapped through the stop: position worth ~0."
    elif b["result"] == "stop" and net < sl - 0.1:
        desc += f" Price gapped past the {sl:+.0%} stop, so the sell filled lower."
    send(title, desc, color, [
        ("Entry mcap", mc(entry_ref), True),
        ("Exit mcap", mc(b.get("exit_ref")), True),
        ("Peak so far", "n/a" if mfe is None else
         f"{mc(entry_ref and entry_ref * (1 + mfe))} ({mfe:+.0%})", True),
        _links(c),
    ], url=_dex(c["token"]), also="tp" if net > 0 else "stops")


def interim(c: dict, out: dict, rep: dict):
    """Primary-horizon (6h) result, posted as soon as it is known."""
    r = out.get("returns", {}).get("360")
    if r is None or (not _all() and not is_trade(rep)):
        return
    pct = lambda k: "n/a" if out.get(k) is None else f"{out[k]:+.0%}"
    r1 = out["returns"].get("60")
    view = rep["continuation_view"]
    b = out.get("bracket")
    if b and rep.get("p_runner") is not None:
        names = {"target": "hit +100% first", "stop": "hit the stop first",
                 "time": "neither level, sold at 6h", "unfilled": "could not be bought"}
        took = is_trade(rep)
        good = None if b["result"] == "unfilled" else (b["net"] > 0) == took
        send(f"⏱️ #{c['id']} {c.get('symbol')}: trade {names.get(b['result'], b['result'])}, "
             f"net {b['net']:+.1%}",
             f"Claude said **{_label(rep)}**, so the position was "
             f"{'TAKEN' if took else 'skipped'}"
             f"{'' if good is None else (' ✅' if good else ' ❌')}.",
             GREEN if b["net"] > 0 else RED if b["net"] < 0 else GREY, [
                 ("Exit after", f"{b.get('exit_min', 0):.0f} min", True),
                 ("Hold-to-6h", f"{r:+.1%}", True),
                 ("Max up 6h", pct("mfe_6h"), True),
                 _links(c),
             ], url=_dex(c["token"]))
        return
    if view == "neutral":
        verdict = "➖ Claude was neutral"
    elif (r > 0) == (view in ("continue", "strong_continue")):
        verdict = "✅ call looks right"
    else:
        verdict = "❌ call looks wrong"
    send(f"⏱️ #{c['id']} {c.get('symbol')}: 6h result {r:+.1%}",
         f"Claude said **{view.replace('_', ' ')}**, so {verdict}. Net of costs, $250 paper "
         f"position entered {PREREG['decision_delay_minutes']} min after detection.", GREEN if r > 0 else RED, [
             ("1h", "n/a" if r1 is None else f"{r1:+.1%}", True),
             ("6h", f"{r:+.1%}", True),
             ("Max up 6h", pct("mfe_6h"), True),
             ("Max down 6h", pct("mae_6h"), True),
             _links(c),
         ], url=_dex(c["token"]))


def error(msg: str):
    """At most one error ping per 15 minutes."""
    global _last_error_sent
    if time.monotonic() - _last_error_sent < 900:
        return
    _last_error_sent = time.monotonic()
    send("⚠️ Error in experiment loop", f"```{msg[:1500]}```", RED)


def digest(stats: dict):
    send("🗓️ Daily digest", "", BLUE, [(k, v, True) for k, v in stats.items()])
