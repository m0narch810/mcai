"""Posts the live trading scoreboard to Discord every POST_EVERY_HOURS
through the existing webhook, and (optionally) answers /stats.

Separate from the experiment loop: it only reads the database, so starting,
stopping or crashing it can't affect the experiment. The periodic post needs
nothing beyond data/discord_webhook.txt. /stats additionally needs a bot token
in data/discord_bot_token.txt (or NR_DISCORD_BOT_TOKEN); until that exists
the bot part waits and checks once a minute. Single instance via
data/stats_bot.lock."""
import asyncio
import msvcrt
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import discord
import requests
from discord import app_commands

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from nr import stats  # noqa: E402
from nr import notify  # noqa: E402
from nr.config import DATA_DIR, PREREG  # noqa: E402

TOKEN_FILE = DATA_DIR / "discord_bot_token.txt"
LOG = DATA_DIR / "stats_bot.log"
GREEN, RED, GREY = 0x2ECC71, 0xE74C3C, 0x95A5A6
POST_EVERY_HOURS = 10 / 60


def log(msg: str):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"[{datetime.now(timezone.utc):%Y-%m-%dT%H:%M:%S}] {msg}\n")


def token() -> str | None:
    t = os.environ.get("NR_DISCORD_BOT_TOKEN")
    if not t and TOKEN_FILE.exists():
        t = TOKEN_FILE.read_text(encoding="utf-8").strip()
    return t or None


def _usd(x):
    return "n/a" if x is None else f"{'-' if x < 0 else '+'}${abs(x):,.2f}"


def _pct(x, signed=True):
    return "n/a" if x is None else (f"{x:+.1%}" if signed else f"{x:.0%}")


def build_embed() -> discord.Embed:
    d = stats.compute()
    s = stats.summarize(d)
    color = GREY if not s["n"] else GREEN if s["total_usd"] > 0 else RED
    e = discord.Embed(
        title=f"📊 Trading stats: {d['hours']:.1f}h since this version started",
        description=(f"Rank-gate trades (Claude's top quarter, rug-guarded), bought "
                     f"{PREREG['decision_delay_minutes']} min after detection: take profit "
                     f"{PREREG['bracket_target']:+.0%}, stop {PREREG['bracket_stop']:+.0%}, else sell at market after "
                     f"{PREREG['bracket_max_minutes']} min."),
        color=color)
    scoring = sum(o["state"] == "scoring" for o in d["open"])
    add = lambda n, v, inline=True: e.add_field(name=n, value=v, inline=inline)
    add("Trades", f"{d['taken']} taken · {s['n']} closed · {len(d['open'])} open"
                  + (f" · {d['unfilled']} unfillable" if d["unfilled"] else ""), False)
    if s["n"]:
        add("Record", f"**{s['wins']}W / {s['losses']}L**\nTP {s['tp']} · SL {s['sl']} · time limit {s['time']}")
        add("Win rate", f"**{_pct(s['win_rate'], False)}**\nTP hit {_pct(s['tp_rate'], False)} "
                        f"(needs ~35% to break even)")
        add("EV per trade", f"**{_pct(s['ev_pct'])}** ({_usd(s['ev_usd'])})\nmedian {_pct(s['median_pct'])}")
        add("Total P&L", f"**{_usd(s['total_usd'])}**\non {s['n']} × ${stats.POSITION:,.0f}")
        add("Bankroll", f"${stats.STARTING_BANKROLL:,.0f} → **${s['bankroll_end']:,.2f}**\n"
                        f"{_pct(s['bankroll_pct'])}")
        add("Return on capital used", _pct(s["deployed_pct"]))
        add("Avg win / avg loss", f"{_usd(s['avg_win'])} / {_usd(s['avg_loss'])}")
        add("Profit factor", "n/a" if s["profit_factor"] is None else f"{s['profit_factor']:.2f}")
        add("Max drawdown", _usd(s["max_dd"]))
        b, w = s["best"], s["worst"]
        add("Best / worst", f"{b['symbol']} {_pct(b['net'])} ({b['result']})\n"
                            f"{w['symbol']} {_pct(w['net'])} ({w['result']})")
        add("Streak", s["streak"])
    else:
        add("Record", "No closed trades yet.", False)
    if d["open"]:
        lines = []
        for o in d["open"][:10]:
            if o["state"] == "scoring":
                lines.append(f"{o['symbol']}: scoring…")
            else:
                mfe = f", max up {o['mfe']:+.0%}" if o.get("mfe") is not None else ""
                m = o.get("minutes", 0)
                lines.append(f"{o['symbol']}: {f'{m}m in' if m else 'just entered'}{mfe}")
        more = len(d["open"]) - 10
        add("Open", "\n".join(lines) + (f"\n…and {more} more" if more > 0 else ""), False)
    if d.get("books_n"):
        lines = []
        for name, nets in d["books"].items():
            if not nets:
                lines.append(f"**{name}**: no trades")
                continue
            w = sum(x > 0 for x in nets)
            lines.append(f"**{name}**: {len(nets)} · {w}W/{len(nets) - w}L · EV {sum(nets) / len(nets):+.1%} · "
                         f"{_usd(sum(nets) * stats.POSITION)}")
        add(f"Strategy comparison (same {d['books_n']} coins, scored after 6h)", "\n".join(lines)[:1024], False)
    add("Funnel", f"{d['candidates']} coins flagged · {d.get('excluded', 0)} blocked by rug guard · "
                  f"{d['researched']} researched · {d['no_budget']} skipped for budget", False)
    note = f" · {scoring} trades still scoring, run /stats again" if scoring else ""
    e.set_footer(text=f"${stats.POSITION:,.0f} per trade, ${stats.STARTING_BANKROLL:,.0f} starting bankroll · "
                      f"net of fees, impact and slippage · version {d['version']}{note}")
    return e


def _poster():
    """Scoreboard to the webhook now, then every POST_EVERY_HOURS."""
    while True:
        try:
            url = notify._url()
            if url:
                r = requests.post(url, json={"embeds": [build_embed().to_dict()]}, timeout=20)
                log(f"posted stats to webhook: HTTP {r.status_code}")
        except Exception as ex:
            log(f"webhook stats post failed: {ex!r}")
        time.sleep(POST_EVERY_HOURS * 3600)


def main():
    lock = open(DATA_DIR / "stats_bot.lock", "w")
    try:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        return   # already running
    threading.Thread(target=_poster, daemon=True, name="poster").start()
    tok = token()
    if not tok:
        log(f"no bot token yet; waiting for {TOKEN_FILE.name}")
        while not (tok := token()):
            time.sleep(60)

    while True:
        try:
            make_client().run(tok, log_handler=None)
            return
        except discord.LoginFailure:
            log("login failed: bad token; waiting for a new one")
            old = tok
            while (tok := token()) == old:
                time.sleep(60)
        except Exception as ex:
            log(f"bot crashed: {ex!r}; restarting in 30s")
            time.sleep(30)


def make_client() -> discord.Client:
    client = discord.Client(intents=discord.Intents.default())
    tree = app_commands.CommandTree(client)

    @tree.command(name="stats", description="Win rate, EV, P&L and bankroll for this version's trades")
    async def stats_cmd(inter: discord.Interaction):
        await inter.response.defer(thinking=True)
        try:
            emb = await asyncio.to_thread(build_embed)
            await inter.followup.send(embed=emb)
        except Exception as ex:
            log(f"/stats failed: {ex!r}")
            await inter.followup.send(f"Stats failed: `{ex!r}`"[:1900])

    @client.event
    async def on_ready():
        # Guild sync makes the command appear immediately (global sync can
        # take up to an hour).
        for g in client.guilds:
            tree.copy_global_to(guild=g)
            await tree.sync(guild=g)
        log(f"ready as {client.user}; /stats synced to {len(client.guilds)} server(s)")

    return client

if __name__ == "__main__":
    main()
