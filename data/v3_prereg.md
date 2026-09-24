# v3 pre-registration: reading rules

Written 2026-09-23T22:17Z, BEFORE any v3 candidate existed.
PREREG_VERSION 5e07836521a5. Supersedes the v2 prompt rule
(data/prompt_prior_decision_rule.md), which was applied at n=33: faded-coin
win rate 12% -> "keep". That rule measured the median and missed the tail,
which is why the question itself changed (README, "v3").

## Primary questions (arm C, on-time reports, filled trades only)
1. Ranking: AUC(p_runner, bracket result == target), bootstrap 95% CI.
2. Money: mean net of trades with p_runner >= 35, versus the buy-everything
   baseline = mean bracket net of the not_sampled control pool.

## When to read
- Do not read before n >= 100 filled, researched candidates AND >= 10 target
  hits among them. Before that, only pipeline checks (does p_runner have
  spread? does the null check look sane?).
- The null check comes first: control-pool target-first rate must be at or
  below ~0.5. Above 0.6 means a fill leak; fix it before reading anything.

## What counts as a result
- Edge: AUC CI lower bound > 0.5 AND taken-trade mean net > control mean net.
- No edge: AUC CI includes 0.5. Report it as such; do not re-slice by
  mcap/age/etc. to find a subgroup that works (that is a new hypothesis for
  a new version, tested on coins detected after it is written).
- P arm (packet only) is the control for web research: if P ranks as well as
  C, browsing adds nothing.

## Changing things
- Bugs (wrong fills, crashes, bad data) are fixed at any time and noted here.
- Design changes (prompt, levels, threshold, universe) are allowed at any
  time, but each one is a new PREREG_VERSION, the reason is written here
  BEFORE that version's outcomes exist, and versions are never pooled.
- Not a reason to change: a streak of losses, a single big miss, wanting more
  trades on Discord, or a comparison run after seeing which version looks better.
