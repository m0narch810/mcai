# v6 pre-registration: reading rules

Written 2026-09-26 (UTC), BEFORE any v6 candidate existed.
PREREG_VERSION 92825cca71a4. Supersedes v5.1 (19ce3af93839); not pooled with it.
Earlier v6 hashes (36786a85860c, fb8be881079c) ran ~15 min on Opus with no
researched coin (budget-paused) and are archived.

## Why v6 exists
v5.1's gate-taken trades lost: 53 closed, 17W/36L, EV -14.3%/trade, -$1,898 on
$250 clips (stats bot, after fixing its own premature-scoring bug). With costs a
+100%/-50% bracket needs ~39% target hits to break even, not 35%.

Offline studies on v5.1 (251 coins, two time halves A/B) and v4 (91 coins,
holdout H), all with an engine that reproduces the stored v4 brackets 91/91:
- Removable LP (not a curve coin, LP neither locked nor burned) rugged
  72%/72%/79% (A/B/H) vs 14%/11%/21% otherwise. Same direction everywhere,
  and the mechanism is direct: an LP pull zeroes a position and no stop fires.
- Max hold was the only exit change that helped in every set (30m vs 6h).
- Tighter stops, trim ladders, half-at-2x-and-ride, and dip entries did not
  help consistently. Time-of-day flipped sign between versions.
- Claude's rank matters: all researched -19%/trade, top half -3%, top
  quarter ~0% (n=30), before the rug guard.

## What changed
1. **Rug guard** (nr/rugguard.py): trade only curve coins or pools with top-
   market LP locked >= 90% (RugCheck) or burned >= 90% (GMGN); unverifiable =
   removable. Excluded coins are logged in `exclusions`, not researched, and
   still get a paper entry so the guard can be audited.
2. **30-minute hold**: bracket_max_minutes 360 -> 30 (stop -50%, target +100%).
3. **Shadow books** (nr/books.py), paper-traded on the same gate-taken coins,
   scored after each coin's 6h window, written before any v6 coin:
   "v5 exit (6h hold)", "trim ladder 100/175/250" (+BE, stale 30m),
   "no stop, TP+200%, 30m", "top 10% gate only".
4. Research: arm C only (packet-only arm P and the 10% C2 reruns are off;
   budget goes to more coins).
5. Research model: Sonnet (was Opus), the user's call to fit the weekly
   quota. Claude's ranking is the part that worked in v5.1, so the Sonnet
   gate is itself under test: compare C-gate EV and AUC with v5.1's Opus.
6. Packet: `rug_guard` and `derived.buys_per_unique_buyer_h1`; prompts
   describe the 30-min trade and the pre-filter.

## Primary question
Mean net of the primary trade for C-gate TAKEN coins, with n, W/L, total P&L.

## Checks before reading anything
- Guard audit: excluded coins' paper outcomes vs non-excluded controls. The
  excluded group's -100% rate should be far above the rest; if not, the
  guard is not doing what the offline study said.
- Null: control-pool target-first rate at or below ~0.5.
- Late share of C reports <= ~10%.

## When to read
n >= 60 closed primary trades and >= 10 target hits. Shadow books are read
at the same point, on the same coins, and the winner is chosen on v6 data
only. Adopting a shadow book as primary is a new version.
