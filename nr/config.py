"""Pre-registered experiment parameters.

Every number here was chosen from market-structure reasoning BEFORE any outcome
was observed. None of them may be tuned on results. Changing any value in
PREREG creates a new PREREG_VERSION (its hash), and analysis never pools
candidates across versions.
"""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "research.db"
LEDGER_PATH = DATA_DIR / "ledger.jsonl"
RAW_DIR = DATA_DIR / "raw"          # raw Claude transcripts, one file per run
PROMPT_DIR = Path(__file__).resolve().parent / "prompts"

PREREG = {
    # ---- Candidate universe (Solana only, Phase 1) --------------------------
    # v4: early coins only. The trade is a 2x; doubling a $50k coin takes
    # ~$50k of net buying, doubling a $5M coin takes ~$5M, so the same burst
    # of attention moves a small, young coin much further. Above $300k the
    # 2x needs flows this universe rarely has. The floor is the bottom of
    # "tens of thousands": below $10k a pump.fun coin is minutes from launch
    # and its first hour of bars (pre_entry_sigma_minutes) does not exist.
    # Chosen from that reasoning; v1-v3 outcomes were NOT sliced by mcap to
    # pick these numbers (v3 had ~10 bracket results in total).
    "mcap_min_usd": 10_000,
    "mcap_max_usd": 300_000,
    # v4: pump.fun coins still on their bonding curve are in scope, at their
    # curve-equivalent depth (nr/sources.py). Before v4 DexScreener's missing
    # curve liquidity silently excluded every one of them, i.e. the earliest
    # coins were invisible. Other launchpad curves (Meteora DBC, etc.) have
    # per-launch curve parameters and no public depth, so they stay out.
    "bonding_curves": ["pumpfun"],
    # A $250 clip must be tradeable: at $8k liquidity CPMM impact is ~6%/side.
    "liquidity_min_usd": 8_000,
    # v5: 10 minutes (was 35). The 35 existed only so the T1+25m entry had 60
    # min of bars for the descriptive sigma null. v4 flagged coins already up
    # ~+150% on the hour, i.e. after the move the user wants to catch. 10 min
    # is the least history at which RugCheck/GMGN holder data is populated and
    # the first dev/sniper dump has usually happened. No maximum age: revivals
    # are in scope, and results are split by age bucket instead.
    "pair_age_min_minutes": 10,
    # ---- Universe rule v2 ------------------------------------------------
    # "Unusual activity" is measured against the MARKET, not against the
    # token's own volume since launch.
    #
    # v1 normalised both volume tests by V6 / min(6h, pair_age), i.e. the
    # average hourly volume since the pair was created. For a pair that is
    # minutes into a vertical launch that denominator IS the launch, so a
    # genuine runner scores ~0.8x and can never pass; for a year-old token
    # that trades $3k a day, any single $1k print scores 30x and always
    # passes. The rule therefore selected dormant tokens twitching and
    # rejected every token that was actually moving. Both tests below are
    # age-independent:
    #   turnover  - the pool must trade its own depth at least once an hour.
    #               Unusual in absolute market terms and scale-free, so it
    #               means the same thing at $10k and $10M of liquidity.
    #   not fading - the last 5 minutes must still be running at or above the
    #               hour's own pace, so the move is live at T1 rather than
    #               already over. Compares two windows that both exist at any
    #               pair age; no since-launch baseline is involved.
    "h1_turnover_min": 1.0,
    "m5_rate_vs_h1_rate_min": 1.0,
    # Breadth: a move with fewer than 100 buys in an hour is a few wallets.
    "h1_buys_min": 100,
    # Phase 1 studies upward moves only ("token begins moving").
    "h1_price_change_min_pct": 0.0,
    # A token re-enters the pool at most once per 24h.
    "dedup_hours": 24,
    "quote_tokens": ["So11111111111111111111111111111111111111112",
                     "EPjFWdd5AufLSSxaE6fT3kVdQRBzHAyFgZ6VD8m8H2cU"],

    # ---- Timing ---------------------------------------------------------------
    # Every candidate, researched or not, gets its paper entry at
    # T_D = T1 + decision_delay. All arms (A/B/P/C) share this entry time, so
    # arm differences reflect information, not latency.
    # v5.1: 4 (v4: 25, v5: 10). Measured: the "3 min packet" was mostly a
    # ~2.5 min discovery cycle stamped as T1 before it ran. With T1 stamped
    # at read time, detection on its own thread and ~5 GeckoTerminal calls
    # per fast cycle, packets take ~30s and research froze in ~66s (median).
    # v6.2: 5 (was 4) and research timeout 4 (was 3). Sonnet with web search
    # took 112-170s per report (median ~145s; Opus ~66s) and 9 of 24 runs hit
    # the 180s timeout - a biased sample, since coins with more to find online
    # time out more. The prompt now also caps web calls at 4.
    "decision_delay_minutes": 5,
    # Research must be frozen before T_D; a later report is flagged LATE and
    # excluded from the primary analysis.
    "research_timeout_minutes": 4,

    # ---- Research allocation ----------------------------------------------------
    # Candidates are thinned at random (not by quality) so researched and
    # unresearched candidates interleave across the day. The unresearched
    # remainder is the randomized control / null pool.
    # v6.1: 0.8 (was 0.6). The rug guard removes ~1/3 of coins before
    # research, and the gate needs 20 same-version reports to warm up; 20% of
    # coins remain the untraded random control, ~7/hour at current volume.
    "research_sample_prob": 0.8,
    # Fraction of researched candidates that get a second, independent C run
    # started at the same moment (self-consistency measurement only).
    # v6: 0 (was 0.10) - budget goes to researching more coins instead.
    "consistency_rerun_prob": 0.0,
    # v6: the packet-only arm P is off. It answered its question (P ranked
    # about as well as C in v4/v5.1) and cost ~25% of each coin's research;
    # the weekly Claude quota, not the daily cap, is what limits trades.
    "packet_only_arm": False,

    # ---- Paper execution ----------------------------------------------------------
    "position_usd": 250.0,
    # Swap fee by DEX id; unknown DEX -> 1%.
    # pump.fun curve: 0.95% protocol + 0.30% creator. Charged for the whole
    # round trip even when the exit happens on PumpSwap after graduation.
    "dex_fee": {"pumpfun": 0.0125, "pumpswap": 0.0025, "pump_fun_amm": 0.0025, "raydium": 0.0025,
                "raydium-cpmm": 0.0025, "raydium-clmm": 0.0025,
                "orca": 0.003, "meteora": 0.01, "meteoradbc": 0.01},
    "dex_fee_default": 0.01,
    # MEV / sandwich / quote-drift buffer charged on each side.
    "slippage_buffer": 0.005,
    # Priority fee + tip per transaction, USD.
    "tx_cost_usd": 0.30,
    # Below this pool liquidity at exit time the position is unexitable -> 0.
    "exit_liquidity_min_usd": 2_000,

    # ---- Outcomes ----------------------------------------------------------------------
    "horizons_minutes": [60, 360, 1440],
    # Primary evaluation horizon, chosen before any outcome was seen: memecoin
    # attention plays out over hours; 1h is mostly microstructure noise and
    # 24h mostly post-narrative decay. 1h and 24h stay as secondary views.
    "primary_horizon_minutes": 360,
    # Vol-scaled symmetric barriers (units of pre-entry hourly sigma), first
    # touch wins, evaluated on 1-min bars within the 6h window.
    "sigma_barriers_k": [1.0, 2.0],
    "barrier_window_minutes": 360,
    # Descriptive % barriers from the master plan. Not vol-scaled, so they
    # are reported but never used as the primary outcome.
    "pct_targets": [0.5, 1.0, 2.0],
    "pct_stops": [-0.2, -0.3, -0.5],
    # v5: 20 (was 60) - a 10-min-old coin entered at T1+10 has 20 min of bars.
    "pre_entry_sigma_minutes": 20,

    # ---- v3: the trade being forecast ------------------------------------------
    # v2 asked "will attention persist over 6h" and scored a 6h hold. In this
    # universe ~60% of coins have their LP pulled within 6h and ~1 in 7 double
    # first, so a hold-to-6h label cannot tell a runner from a rug and Claude
    # faded everything, runners included. v3 asks Claude for a probability of
    # one concrete bracket trade and scores exactly that trade:
    #   buy at T_D, take profit at +100%, stop at -50%, else sell at 6h.
    # +100%/-50% is symmetric in log space (x2 / x0.5), so for a driftless
    # price the chance of hitting the target first is 0.5 - a known null.
    # Sequential on 1-min bars; same bar = loss; a pulled LP is -100%, not a
    # filled stop. These levels were fixed from the payoff, not the data.
    "bracket_target": 1.0,
    "bracket_stop": -0.5,
    # v6: 30 (was 360). The only exit change that improved EV in every set
    # tested (v5.1 halves A/B and the v4 holdout; 3,840-rule search on
    # 2026-09-26): SL-50/TP+100 held 30m vs 6h went A -0.335->-0.294,
    # B -0.149->-0.086, v4 -0.543->-0.262. These coins decay; a trade that
    # hasn't doubled in 30 minutes mostly doesn't. Chosen by consistency
    # across sets, not by the best single-set number.
    "bracket_max_minutes": 30,
    # v5: trade by RANK, not by level. v4 required p_runner >= 35 and Claude
    # (honestly, with a ~12% base rate) cleared it once in 39 coins, while its
    # ranking was informative (AUC ~0.75). The gate is now: p_runner at or
    # above the trade_rank_quantile of the same arm's previous
    # trade_rank_window on-time reports (scores only, never outcomes, all
    # strictly earlier -> causal). 0.75 = top quarter, chosen for alert
    # frequency (~1-2/hour at v4 volumes), not from any P&L. Whether the top
    # quarter clears the 1/3 break-even is exactly what v5 tests. The window
    # spans versions so the gate works from the first v5 coin; v4 scores roll
    # out after 100 v5 reports.
    "trade_rank_quantile": 0.75,
    "trade_rank_window": 100,
    # v6.1: the window only holds this version's reports (see
    # research.rank_gate), and nothing is traded until it has this many.
    "trade_rank_min_prior": 20,
    # v6 rug guard (nr/rugguard.py): a coin is traded only if its pool
    # liquidity cannot be pulled - still on the pump.fun curve, or top-market
    # LP locked >= 90% (RugCheck) or burned >= 90% (GMGN). Unverifiable =
    # removable. Coins with removable LP rugged 72-79% of the time in v4/v5.1
    # vs 11-21% otherwise, same direction in every set. Mechanism first: an
    # LP pull is what zeroes a position, and no stop can prevent it.
    "rug_guard": {"lp_locked_min_pct": 90, "lp_burn_min": 0.9},
    # v6 shadow books (nr/books.py): alternative rules paper-traded on the
    # same gate-taken coins, scored after each coin's 6h window. Written
    # down before any v6 coin; they compete on forward data only.
    "shadow_books": {
        "v5 exit (6h hold)": {"stop": -0.5, "levels": [[1.0, 1.0]], "max_min": 360},
        "trim ladder 100/175/250": {"stop": -0.5, "levels": [[1.0, 0.3333], [1.75, 0.3333], [2.5, 0.3334]],
                                    "stale_min": 30, "be": True, "max_min": 360},
        "no stop, TP+200%, 30m": {"stop": None, "levels": [[2.0, 1.0]], "max_min": 30},
        "top 10% gate only": {"stop": -0.5, "levels": [[1.0, 1.0]], "max_min": 30,
                              "gate_quantile": 0.9},
        # v6.1: primary rule, position capped at 1.25% of entry liquidity
        # (impact <= 2.5% per side). In a $9k pool a $250 round trip costs
        # ~15% before any price move, so a 30-min time exit is structurally
        # negative there. EV is per dollar risked; $ figures are smaller.
        "primary, sized to pool": {"stop": -0.5, "levels": [[1.0, 1.0]], "max_min": 30,
                                   "size_frac_of_liq": 0.0125},
    },
    # v5 descriptive ladder (ChatGPT review: measure the right tail instead of
    # one binary): for each target, did it fill before the -50% stop within
    # 24h, on the same sequential engine as the bracket. Reported by gate
    # group vs control; never used to pick a threshold.
    "ladder_targets": [0.5, 1.0, 3.0],
    "ladder_stop": -0.5,
    "ladder_max_minutes": 1440,
    # A pool with no liquidity (or no pair) at T_D cannot be bought. v2
    # scored those as -100%; v3 records them as unfilled (net 0, no trade)
    # and reports how many there were.
    "unfillable_is_no_trade": True,
    # The skeptic never changed a verdict in v2 (it only ever agreed on fade)
    # and cost ~$0.40 a run. Budget, not the random draw, was deciding which
    # coins got researched, so its spend goes to researching more coins.
    "skeptic_enabled": False,

    # ---- Evidence sources (part of the experiment definition) -------------------------
    # TwitterAPI.io (paid) is off: the user is staying on free data. Flip
    # x_social_enabled to True to re-enable; that starts a new version.
    "x_social_enabled": False,
    "evidence_sources": ["dexscreener", "geckoterminal", "rugcheck", "gmgn:info+security"],
    # All experiment timestamps are true UTC (PC clock drift corrected via
    # HTTPS Date headers, nr/clock.py); market data is stamped in true time.
    "timestamps": "drift-corrected",
}

# The prompts are part of the experiment: editing one changes the version too.
_PROMPTS = {p.name: p.read_text(encoding="utf-8")
            for p in sorted(PROMPT_DIR.glob("*.md"))}


# ---- Operational (may change without a new version, except claude_model) -------------
RUNTIME = {
    "poll_seconds": 60,
    # Detection has its own thread: a fast cycle (early-coin feeds) every
    # detect_poll_seconds, all feeds every detect_full_every_s. GeckoTerminal
    # (~27 calls/min at our gap) is the limit: ~5 calls per fast cycle.
    "detect_poll_seconds": 30,
    "detect_full_every_s": 300,
    # v6: sonnet (was opus). User's call on 2026-09-26: the weekly Claude
    # quota was the binding limit on how many coins get researched, and
    # Sonnet costs a fraction of Opus per coin. Part of PREREG_VERSION.
    "claude_model": "sonnet",
    # Usage caps, metered by the cost the CLI reports (Max-plan equivalent $).
    # Sized for the v2 universe rule, which nominates several times more
    # candidates per day than v1 did. session_share_cap, not these, is what
    # actually protects your own Claude quota.
    # v5: raised from 40/15. On 2026-09-25 the $40 cap, not the Claude quota
    # (5h session at 7%), excluded 41 of 116 drawn coins as `no_budget`.
    # v5.1: raised again from 60/22. Faster detection nominates ~35 coins/h
    # (v4: ~8), and the $22/5h cap paused research after ~3h while the real
    # 5h session sat at 11%. The dollar caps are now ceilings only; the
    # session-share and weekly-utilization caps are what protect the quota.
    # v6: 150/50 ceilings; with Sonnet at a fraction of Opus's cost these
    # rarely bind. The session-share and weekly caps protect the quota.
    "daily_budget_usd": 150.0,
    "window5h_budget_usd": 50.0,
    # Stop new research above this 7-day Claude utilization, so the bot never
    # eats the user's weekly limit.
    "weekly_util_cap": 0.85,
    # Stop taking new coins (and post-mortems) once usage of the current 5h
    # Claude session limit has risen this much since the bot's first run in
    # that window. Counts all usage in the window, your own chats included.
    "session_share_cap": 0.35,
    # Hours per day the machine is actually up (school-day duty cycle), used
    # only to pace spending across the session so the budget is not gone by
    # 8pm. hourly cap = daily / active_hours * burst_multiple.
    "active_hours_per_day": 15.0,
    "hourly_burst_multiple": 2.0,
    "per_run_budget_usd": 3.0,          # hard cap passed to each claude -p run
    # Budget reserved before starting a candidate (C + P + possible skeptic
    # typically cost ~$1.10 in total; this leaves headroom).
    "reserve_per_candidate_usd": 0.4,
    "max_concurrent_research": 4,
    "liquidity_snapshot_minutes": 20,
    # Post-mortems run only from leftover daily budget.
    "postmortem_enabled": True,
    # "entries": Discord gets only tokens Claude calls continue/strong_continue
    # (entry + 6h result), plus digest/errors/restarts. "all": every event.
    "discord_mode": "entries",
    # In "entries" mode, also post a one-line verdict for every researched
    # candidate, including the fades. Without this, a night in which Claude
    # faded everything is indistinguishable from a night the bot was dead.
    "discord_verdict_lines": True,
    # Post a "still alive, nothing qualified" note at most this often (hours)
    # when no candidate has been detected for that long.
    "quiet_heartbeat_hours": 3,
}

# The research model is part of the experiment too.
PREREG_VERSION = hashlib.sha256(
    json.dumps({"prereg": PREREG, "prompts": _PROMPTS, "model": RUNTIME["claude_model"]},
               sort_keys=True).encode()
).hexdigest()[:12]
