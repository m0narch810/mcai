# v4 pre-registration: reading rules

Written 2026-09-24 (UTC), BEFORE any v4 candidate existed.
PREREG_VERSION e16de5aac8ee. Supersedes v3 (5e07836521a5), which ran for
under a day and is not pooled with v4.

## Why v4 exists
The user's design direction: target early coins, because a 2x on a $50k coin
needs ~1/100th of the net buying a 2x on a $5M coin does. That is a
market-structure argument made before looking at outcomes. It is NOT a subgroup
found by slicing v1-v3 results (v3 had ~10 bracket results; nothing was
sliced). The band edges ($10k, $300k) come from that argument and from the
user's direction, not from data.

Second change, a fix rather than a design choice: pump.fun bonding-curve pairs
were silently excluded in v1-v3 (no DexScreener liquidity). v4 includes them
at curve-equivalent depth.

## Primary questions: unchanged from v3
Same as data/v3_prereg.md (AUC of p_runner vs target-first; mean net of
p_runner >= 35 trades vs the not_sampled control), on v4 candidates only.

## Additional checks before reading anything
- Null check as in v3: control-pool target-first rate at or below ~0.5.
- Split every readout by `trigger.bonding_curve` (curve vs graduated pool).
  The two have different fees, depth models and rug mechanics; report both,
  and do not report only the half that looks better.
- Graduation audit: for curve coins that graduated inside the 6h window,
  check that the stitched bars show no gap or price jump at the seam larger
  than the surrounding bars' range. A jump there is a data error, not a win.

## When to read
Same thresholds as v3: n >= 100 filled researched candidates and >= 10
target hits. Before that, pipeline checks only.

## Changing things
As in v3. In particular, do not narrow the band further (e.g. to $10k-$50k)
because early v4 results look better there; that is a new version with its
reason written first.

## Bugs fixed (logged as required above)
- 2026-09-25: rug-spike fake wins. When an LP is pulled, dust trades against
  the emptied pool print absurd highs (#497 SI: x2,000,000 on $392 against a
  $170k pool), which the bracket scored as a +100% fill (+95% net). A target
  now also needs the bar's volume to be able to reach the level in a CPMM
  of the last observed liquidity, with 10x headroom for measurement error
  (outcomes._reachable). Effect on v4 so far: 3 fake targets removed (SI,
  GENGO, McDonald's), all genuine targets kept. Found during a manual review,
  before any v4 outcome was stored.
