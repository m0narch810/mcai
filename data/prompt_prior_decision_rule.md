# Pre-committed decision rule: the research prompt's base-rate prior

Written 2026-09-22 ~20:15 UTC, under PREREG ebf0ce5fd7e7, BEFORE any v2
outcome existed. Zero outcomes rows at time of writing; first v2 interim
6h results due ~02:00 UTC.

## The question
`nr/prompts/research.md` opens with "Most tokens that meet the rule are noise,
promotion or manipulation." That sentence was calibrated to the v1 universe,
which selected dormant old tokens twitching. v2 selects live runners. The
prior may no longer describe the population, which would make the near-uniform
fade verdicts a prompt artifact rather than a judgment.

## The rule (decided now, applied later)
Take v2 candidates where arm C said `fade` or `strong_fade` and a net 6h
return exists. Require n >= 20 before reading it at all.

- Win rate (net 6h > 0) among faded tokens <= 30%
  -> the fades are calibrated. KEEP the sentence. The uniform fade is the
     finding, not a bug.
- Win rate 30-45%
  -> ambiguous. Keep, and re-read at n >= 50.
- Win rate > 45%
  -> the prior is miscalibrated for the v2 universe. Rewrite the sentence to
     describe the v2 population honestly, accept the new PREREG_VERSION, and
     do not pool with ebf0ce5fd7e7.

## What does NOT justify changing it
- Disliking the distribution of verdicts.
- Wanting more `continue` calls so Discord has something to show.
- Any comparison run after seeing which choice produces a better-looking AUC.

Changing the prompt alters PREREG_VERSION (prompts are hashed in), so every
change restarts the sample. That cost is the reason this rule exists.
