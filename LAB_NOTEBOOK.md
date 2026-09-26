# Lab notebook

One entry per version: what we believed, what we changed, what happened,
and what we learned. Pre-registrations (`data/v*_prereg.md`) say what each
version tests and when to read it. This file is where results get written
down and learned from. Newest entry first.

**Goal:** consistent green, measured net of all costs on forward data.

**Rules we don't break:** trades are scored sequentially on 1-min bars, and
a stop that shares a bar with a target counts as a loss. There is no
look-ahead. Fills are pessimistic, and fees, impact and slippage are charged
on every buy and every sell. Every change is a new version whose reason is
written down first. A filter is only adopted if it has a mechanism and holds
in data it wasn't chosen on.

---

## v6 — rug guard, 30-minute trade, shadow books (2026-09-26, `92825cca71a4`)

**Hypothesis.** The losses come mostly from LP pulls and from holding decaying
coins too long. Claude's top-quarter rank is a real edge, but it was being
diluted by these two effects.

**Changes.**
- Trade only pump.fun curve coins or pools whose LP is at least 90% locked or
  burned (`nr/rugguard.py`).
- Hold for 30 minutes instead of 6 hours.
- Shadow books run on the same coins: the v5 6h exit, a 100/175/250 trim
  ladder, no stop with a +200% target, and a top-10% gate.
- Research uses Sonnet with arm C only.
- Prereg: `data/v6_prereg.md`.

**Read at** 60 closed primary trades and 10 or more target hits.

**Result.** Pending.

## Study — exits and pre-trade features (2026-09-26)

Scripts and results are in `research/2026-09-26_exits_and_features/`. The
simulator reproduced all 91 stored v4 bracket results exactly. Sets: v5.1
split into two time halves (A, B), v4 as the holdout (H), and Claude's taken
trades (T).

| Finding | Evidence | Status |
|---|---|---|
| Removable LP is the rug mechanism | Rug rate 72/72/79% (A/B/H) vs 14/11/21% for everything else | Adopted in v6 |
| Short holds beat long holds | 30m vs 6h: A -0.335→-0.294, B -0.149→-0.086, H -0.543→-0.262 EV/trade | Adopted in v6 |
| Claude's rank is the edge | All researched coins -19%/trade, top half -3%, top quarter ~0% (n=30) | Kept |
| Tighter stops don't help | After a -30% drawdown, 52-58% of coins went on to -50% and 34-38% recovered | Rejected |
| Trims and half-at-2x-and-ride don't beat a full TP at +100% | Worse or equal in every set | Kept as a shadow book |
| Dip entry (buy 20-30% below the post-detection high) | Better in A/H, worse in B/T | Rejected |
| Time of day | Sign flipped between v4 and v5.1 | Rejected |
| Break-even hit rate for +100%/-50% after costs is ~39%, not 35% | A win nets about +85%, a stop about -55% | Documented |

**Lesson.** The single best rule picked on half A ranked around 1,200th of
3,840 on half B. A rule must be chosen by its consistency across sets, never
by its best single-set number.

## v5.1 — rank gate, 4-minute entry (2026-09-25, `19ce3af93839`)

**Hypothesis.** A fixed p_runner ≥ 35 threshold vetoed almost everything,
while Claude's ranking carried signal (AUC ~0.75). Trading the top quarter by
rank, and entering faster, should produce trades that pay.

**Result.** 53 closed trades: 17W/36L, EV -14.3% per trade, -$1,898 on $250
positions. The first ~30 trades were near break-even (+0% EV). An overnight
cold streak from 06:00 to 12:00 UTC followed.

**Learned.**
- Rugs (LP pulls) and gap-throughs caused the -100% trades.
- The 6h hold gave back gains.
- The stats bot scored exits before any liquidity snapshot existed. It
  counted about $580 of phantom losses; fixed.
- Detection was slow: a 143s cycle, with T1 stamped at the start of the
  cycle. Fixed with a 21s cycle.

## v5.0 → v5.1 speed fixes (2026-09-25)

- Detection moved to its own thread, with priority on GeckoTerminal.
- The fast cycle uses early-coin feeds only.
- Entry moved from T1+25m to T1+4m.
- Budget caps, not the Claude quota, were blocking research. Caps raised.

## v4 — early coins, $10k-$300k plus pump.fun curve (2026-09-24, `e16de5aac8ee`)

**Result.** 127 outcomes. Buying everything averaged -46% per trade. Claude
ranked well (AUC 0.75-0.78) but took only 1 trade. Fixed a bug where fake
targets were filled from rug spikes (the reachability check).

**Learned.** A threshold on calibrated probabilities can't work when the base
rate is ~12%. Gate on rank instead.

## v1-v3 (2026-09-22/23)

- **v1:** a universe rule that could only select dormant tokens. Replaced.
- **v2:** faded everything, so the median-based "keep" missed the right tail.
- **v3:** moved to forecasting the +100%/-50% trade (p_runner).
