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
            S  : skeptic, only when the pre-registered trigger fires
T1 + 25m    paper entry for EVERY candidate (all arms share it)  T_D
+20m...     liquidity snapshots for 24h
T_D + 24h   outcome from 1-min bars -> post-mortem (leftover budget)
```

### Arms compared (same candidates, same entry time)
| Arm | Information |
|-----|-------------|
| A | Fixed quant formula: volume/buyer acceleration, buy pressure, liquidity, holders, authorities |
| B | A + structured social: GMGN wallet composition (bundlers, bots, snipers, smart money, KOL wallets), dev history incl. recycled X accounts, paid promotion, project links |
| P | Claude reading only the packet: isolates what *browsing* adds |
| C | Claude doing real research |

The primary metric is AUC of each arm's ranking against "net 6h return > 0"
(pre-registered primary horizon; 1h and 24h are secondary),
with bootstrap CIs, plus runner detection and results split by market-cap bucket.

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
