# v5 pre-registration: reading rules

Written 2026-09-25 (UTC), BEFORE any v5 candidate existed.
PREREG_VERSION 19ce3af93839 (v5.1). Supersedes v4 (e16de5aac8ee); not pooled with v4.
v5.0 (4dd7b5e449de) ran ~40 min on 2026-09-25 with the same design and a 10-min
entry; no outcome existed when it was replaced. It is archived, not pooled.

## Why v5 exists
The user's direction: the bot vetoes almost everything and alerts far less
often than runners actually happen. The v4 readout (127 outcomes) showed why,
and none of the causes is "Claude can't tell coins apart":
- The gate was a fixed level (p_runner >= 35). With a ~12% base rate Claude
  honestly cleared it once in 39 coins, while its RANKING was informative
  (AUC(p_runner, target) 0.775 C / 0.750 P, n=39, wide CIs).
- Every coin sat ~20 min between frozen research (T1+~4.5m) and the paper buy
  (T1+25m), and coins were only eligible at 35 min old, by which time the
  flagged ones were typically already up ~+150% on the hour.
- The $40/day metered budget, not the Claude quota (5h session at 7%),
  excluded 41 of 116 drawn coins in 24h as `no_budget` - a non-random
  exclusion whose coins did far worse (-99.6% median 6h vs -34% control).

## What changed (and why each is not fitted to v4 outcomes)
1. **Rank gate.** Take the trade iff p_runner >= the 75th percentile of the
   same arm's previous 100 on-time scores (scores only, strictly earlier,
   frozen into the report as `_gate`). 0.75 was chosen for alert frequency
   (~1-2/hour at v4 volumes), not from P&L. The 1/3 break-even is unchanged:
   v5 tests whether the top quarter clears it.
2. **Faster:** decision delay 25 -> 4 min, research timeout 20 -> 3 min.
   Profiling showed the "3 min packet" was mostly the discovery cycle
   (~120s of GeckoTerminal calls queued behind outcome scoring) with T1
   stamped BEFORE it ran. v5.1 stamps T1 when the rule's numbers are read,
   runs detection on its own thread with GeckoTerminal priority, and runs
   only the early-coin feeds every 30s (~21s per cycle; full feeds every
   5 min). Research froze a median ~66s after the packet in v4.
3. **Earlier:** pair_age_min 35 -> 10 min (the 35 existed only to give the
   descriptive sigma null 60 min of bars); sigma window 60 -> 20 min.
4. **Decomposed scores** (ChatGPT review): survival_score, attention_score,
   manipulation_risk, tail_class (A-D). Descriptive in v5; the gate still
   ranks p_runner so the primary question stays the same as v3/v4.
5. **Right-tail ladder:** +50% / +100% / +300% before -50% within 24h, same
   sequential engine as the bracket. Descriptive.
6. **RugCheck methodology in the packet:** its weighted risk score
   (`rugcheck_score`, `rugcheck_score_normalised`) and per-risk weights.
   Also a baseline: AUC(rugcheck score_normalised, liquidity collapse) vs
   Claude's p_rug.
7. Operational (no version change): budget $60/day, $22/5h, reserve per coin
   $0.80 (measured cost ~$0.47), stop research above 85% weekly utilization.

## Primary questions
- Mean net of the bracket trade (+100% / -50% / 6h) for C-gate TAKEN coins
  vs the not_sampled buy-everything control. Report P&L, n, target-hit rate.
- AUC(p_runner, target hit), C and P.

## Additional checks before reading anything
- Null: control-pool target-first rate at or below ~0.5; the sigma null.
- Split by `trigger.bonding_curve` and by pair age (<30m, 30m-6h, >6h):
  the age floor moved, so the new youngest coins must be visible on their own.
- Gate sanity: share of C reports with `_gate.take` should settle near 25%
  once v5 scores fill the window. Far from that means ties or a bug.
- `no_budget` share: should drop well below v4's ~35% of drawn coins.
- Late share: C reports frozen after T_D are excluded. If more than ~10% are
  late at a 4-min entry, the delay is too tight - that is a new version, not
  a silent change.

## When to read
n >= 60 filled C-gate TAKEN trades (about 240 researched coins), and >= 10
target hits among them. Before that, pipeline checks only.

## Changing things
Same rule as before: each change is a new version with its reason written
here first. Do not move the quantile or tighten a score filter because early
v5 results look better somewhere.
