# AI Crypto Narrative Researcher: Phase 1

A prospective experiment that asks one question:

> When a Solana token starts showing unusual activity, does Claude's actual
> qualitative research (reading and searching the web) tell us anything about
> what happens next that simple quantitative data doesn't?

This is **not a trading bot**. Nothing is ever bought. Every trade is a paper
trade with pessimistic costs.

## Run it

```powershell
python run.py run        # leave running; the PC must stay awake
python run.py status     # what's happening, budget used
python run.py analyze    # the readout (meaningful after ~50+ outcomes)
python run.py show 12    # everything recorded for candidate #12
python run.py verify     # prove no frozen record was altered
```

Or double-click `start.ps1`, which runs the loop and restarts it on crash,
logging to `data/run.log`.

Requirements: Python 3.11+, `requests`, and the Claude Code CLI logged in to
your Claude subscription. There's no API key: research runs through
`claude -p` on your Max plan.

## How it works

```
every 60s   discover tokens (GeckoTerminal trending + DexScreener boosts/profiles)
            -> apply the PRE-REGISTERED candidate rule          T1
               (v2: pair >=35m old, h1 volume >= pool liquidity,
                last 5m still running at >= the hour's pace,
                >=100 h1 buys, h1 price up, $30k-$30M mcap, >=$8k liq)
            -> evidence packet (market, holders, structure)     T2
            -> random draw: research (60%) or control (40%)
research    C  : Claude + web search/fetch                      T3 -> frozen T4
            P  : Claude, packet only, no internet
            C2 : independent rerun of C on 10% (self-consistency)
            S  : skeptic (off in v3: it never changed a call; its budget
                 researches more coins instead)
T1 + 25m    paper entry for EVERY candidate (all arms share it)  T_D
+20m...     liquidity snapshots for 24h
T_D + 6h    v3 bracket trade resolved: +100% target / -50% stop / 6h exit
T_D + 24h   outcome from 1-min bars -> post-mortem (leftover budget)
```

### Arms compared (same candidates, same entry time)
| Arm | Information |
|-----|-------------|
| A | Fixed quant formula: volume/buyer acceleration, buy pressure, liquidity, holders, authorities |
| B | A + structured social: GMGN wallet composition (bundlers, bots, snipers, smart money, KOL wallets), dev history incl. recycled X accounts, paid promotion, project links |
| P | Claude reading only the packet: isolates what *browsing* adds |
| C | Claude doing real research |

**v3 primary metric**: AUC of Claude's `p_runner` against "the bracket trade
hit +100% before -50%", and the net P&L of the trades it would take
(`p_runner >= 35`) against buying every control coin. See "v3" below. The v2
metric (AUC against net 6h return > 0) is still reported as secondary.

## Integrity guarantees
- **Pre-registration**: every threshold is in `nr/config.py` `PREREG`, hashed
  into `PREREG_VERSION`. Changing any of them starts a new version, and
  analysis never pools versions.
- **Frozen records**: SQLite triggers abort any UPDATE or DELETE on candidates,
  packets, reports, entries and liquidity observations. Every frozen record is
  also written to a hash-chained `data/ledger.jsonl` (`run.py verify`).
- **Every candidate is logged**, including controls, failures, late reports
  and missed entries.
- **Same entry for all arms** at T_D. Reports frozen after T_D are flagged
  `late` and excluded.
- **Realistic fills**: the entry reference is the worse of the live quote and
  the next bar's open. Swap fee, CPMM price impact at the observed liquidity,
  a 0.5% MEV buffer each side and tx costs are all charged. Exit liquidity
  below $2k, or a vanished pair, means the position is worth 0.
- **Sequential outcomes**: first touch on 1-min bars. On the fill bar only the
  adverse side counts. If target and stop share a bar, it's a loss.
  Barriers are vol-scaled to pre-entry realized volatility.
- **Null check first**: the readout starts with the symmetric-barrier null
  and the random-allocation check. The unit tests confirm a random walk
  returns about 0.49.
- **No hindsight edits**: post-mortems are a separate table and never touch
  the report. Hypotheses they propose are listed only as proposals.

## Universe rule: why v2 replaced v1

v1 normalised both volume tests by `V6 / min(6h, pair_age)` - the pair's
average hourly volume *since it was created*. That denominator is the launch
itself for a young pair, so a token 40 minutes into a vertical move scored
**below 1.0** against a 3.0 threshold and could never qualify; for a year-old
token trading $3k a day the denominator is ~0, so a single $1k print scored
**30x** and always qualified. Combined with a 120-minute age floor, the rule
selected dormant tokens twitching and rejected every token that was moving.

Measured on one live discovery snapshot (130 mints, 2026-09-22): of 37 tokens
up >=40% over 6h, exactly **1** passed v1 - a 459-hour-old pair up 0.7%.
Median 6h change by age bucket was +97.8% for pairs under 2h (all excluded by
the age floor) versus +0.7% for pairs over 7 days (the only bucket that could
pass). 16 researched candidates produced 0 `continue` calls, which was the
correct read of what the detector was handing over, not excessive caution.

v2 normalises by the market instead, so both tests mean the same thing at any
pair age and any scale: hourly **turnover** (`vol_h1 / liquidity_usd >= 1.0`)
for "unusual activity", and `m5_rate / h1_rate >= 1.0` for "the move is still
live at T1". Neither references the token's own history before the last hour.
Thresholds come from that reasoning, not from any observed return; no outcome
data was consulted in choosing them. This is `PREREG_VERSION` `ebf0ce5fd7e7`
and it does **not** pool with earlier versions in analysis.

## v3: forecast the trade, not the vibe

v2 (`ebf0ce5fd7e7`) asked "will attention persist over 6h?" and scored a 6h
hold. Across the whole v2 pool (201 coins with a 6h result, researched or
not): **61%** had their LP pulled within 6h (verified on trade data: volume
stops dead the minute liquidity reads $0), **7%** were up at 6h, but **~27%**
touched +50% and **~14%** doubled first. Claude faded every coin, which was
right on the median and wrong on the tail: of 33 faded coins, Anthropic
(+1225% at 6h), CT (+184%, +128%) and three coins that doubled then rugged
were all `strong_fade` with no gradation. A 5-level label with two levels in
use cannot rank, and a hold-to-6h outcome cannot tell a runner from a rug.

v3 (`5e07836521a5`) changes the question, not the coins (same universe rule):
- Claude outputs `p_runner` (0-100): the chance a bracket trade bought at T_D
  hits **+100% before -50%** within 6h, and `p_rug` (0-100). The prompt states
  the measured v2 base rates instead of "most tokens are noise".
- The trade is scored exactly as described: strictly sequential on 1-min bars,
  stop checked first (same bar = loss), target fills only if price traded
  through it, stop fills at the worse of level/open/close, pulled LP = -100%.
- A position is taken iff `p_runner >= 35`: the break-even hit rate of a
  +100%/-50% payoff is 1/3, plus a margin for costs. Nothing was fitted.
- +100%/-50% is log-symmetric, so a driftless coin hits the target first half
  the time (unit-tested); the readout checks the control pool against that.
- A pool with no pair or $0 liquidity at T_D is recorded as **unfilled** (no
  trade, net 0) instead of -100%: you cannot buy into an empty pool.
- Reading rules were written before any v3 outcome: `data/v3_prereg.md`.

## v4: early coins only

v4 (`e16de5aac8ee`) keeps the v3 question and trade and changes the coins:
- **$10k-$300k market cap** (was $30k-$30M). The trade is a 2x, and the net
  buying a 2x needs scales with market cap: a $50k coin doubles on ~$50k of
  flow, a $5M coin needs ~$5M. Young, small coins are where a burst of
  attention can plausibly double the price. Reasoning only: v1-v3 outcomes
  were not sliced by mcap to choose the band.
- **pump.fun bonding-curve coins are in scope.** DexScreener reports no
  liquidity for a curve, and `best_pair` dropped every pair without it, so
  through v3 the earliest coins were invisible. The curve is a constant-product
  pool on virtual reserves (30 SOL x 1.073B tokens at launch, complete at ~115
  virtual SOL), so its depth follows from its price alone:
  `vSOL = sqrt(K * priceNative)`, liquidity = `2 * vSOL * SOL_USD`. The
  existing CPMM fill model then matches the exact curve fill to within 1%
  (unit-tested). Fee 1.25% per side (0.95% protocol + 0.30% creator), charged
  on the whole round trip even when the exit is on PumpSwap.
- **Graduation is followed.** A curve coin is quoted, snapshotted and charted
  at the token level: price bars are the curve's until the PumpSwap pool's
  first bar, then the pool's. Without this every runner would look like it
  stopped trading at the moment it made it.
- Discovery adds GeckoTerminal's pump.fun pool list; the general feeds rank
  across all of Solana, where a $30k curve coin almost never appears.
- Meteora DBC and other launchpad curves stay out: per-launch curve parameters,
  no public depth, so no honest fill model.
- Reading rules: `data/v4_prereg.md`.

## v5: rank gate, faster entry, decomposed scores

v4 almost never alerted: its gate was a fixed level (p_runner >= 35) that an
honest forecaster rarely reaches when ~1 in 8 coins hits the target, even
though its ranking was informative. v5 changes how coins are picked and how
fast, not the trade being scored:
- **Rank gate:** trade when p_runner is in the top quarter of the arm's last
  100 on-time scores (strictly earlier, frozen with the report as `_gate`).
- **Faster and earlier:** entry at T1+4m (was 25), research timeout 3m,
  coins eligible from 10 min old (was 35). T1 is stamped when the rule's
  numbers are read; detection runs on its own thread every 30s with priority
  on GeckoTerminal (early-coin feeds every cycle, all feeds every 5 min).
- **More scores:** survival, attention, manipulation risk, tail class A-D
  (descriptive), and a +50/+100/+300% before -50% ladder over 24h.
- **RugCheck's weighted risk score** is now in the packet and is reported as
  a baseline against Claude's p_rug.
- Budget raised to $60/day with a weekly-usage cap of 85%.
- Reading rules: `data/v5_prereg.md`.

## Known limitations (Phase 1)
- **No X post data** (no paid X API). Claude reaches X only through web
  search, which indexes it poorly; GMGN supplies the project X account's
  rename history. `nr/xdata.py` (TwitterAPI.io) is built but switched off via
  `x_social_enabled` in PREREG.
- **GMGN** wallet/dev data (key in `data/gmgn_key.txt`; falls back to GMGN's
  shared public demo key). GMGN's wallet labels are its own classification.
- **Pre-graduation bonding-curve tokens are excluded** (pump.fun, Meteora
  DBC): they have no pool liquidity to model fills against.
- CPMM impact is an approximation for concentrated-liquidity pools (CLMM/DLMM).
- Wallet-funding graphs are limited to RugCheck insider flags and GMGN labels.
- Uses the 1-min bar resolution from GeckoTerminal. Anything finer is unresolvable, so same-bar ties count as losses.
- **The experiment has a duty cycle, not 24h coverage.** The PC is off during
  school hours (roughly 11:00-20:00 UTC), so detection stops and any candidate
  whose T_D falls in the gap is recorded as a missed entry and excluded. Paper
  entries cannot be backfilled - a late entry would be a different experiment -
  though 24h outcomes can, since they are computed from historical 1-min bars
  once the loop is back. The readout prints observed coverage and an
  hour-of-day map so a US-evening/overnight sample is never mistaken for a
  round-the-clock one.

## Layout
```
run.py              CLI
nr/config.py        pre-registered parameters + runtime settings
nr/detector.py      objective candidate rule
nr/packet.py        evidence packet
nr/research.py      headless Claude runner, freezing, budget
nr/prompts/         research / packet-only / skeptic / post-mortem prompts
nr/schemas.py       structured report schemas
nr/outcomes.py      paper entry, costs, sequential evaluation
nr/baselines.py     arms A and B
nr/analysis.py      readout
nr/scheduler.py     main loop
tests/              outcome-logic and integration tests
data/               research.db, ledger.jsonl, raw transcripts, analysis.md
```

## Setup for collaborators

Secrets are gitignored. To run the loop yourself, create your own:

| File | Needed for |
|---|---|
| `data/discord_webhook.txt` | Discord updates (optional; or `NR_DISCORD_WEBHOOK` env var) |
| `data/gmgn_key.txt`, `data/gmgn_private_key.pem`, `data/gmgn_public_key.pem` | GMGN wallet/dev data (optional; falls back to public data) |
| `data/twitterapi_key.txt` | X/Twitter data for research packets |

Research runs through `claude -p` (Claude Code CLI on a Max plan). Existing
results are in `snapshot/research.db` (see `snapshot/README.md`), the raw
per-arm model outputs in `data/raw/`, and the hash-chained ledger in
`data/ledger.jsonl`. `prereg_version` is a hash of `PREREG` (in `nr/config.py`), every
prompt in `nr/prompts/`, and the Claude model. Changing any of them starts
a new experiment version; results are never pooled across versions.

`python -m pytest -q tests` should pass before any change is pushed.
