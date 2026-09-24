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
    # Microcap territory where "narrative" plausibly drives price; above $30M
    # the move is usually flows/listings, below $30k it is pure launch noise.
    "mcap_min_usd": 30_000,
    "mcap_max_usd": 30_000_000,
    # A $250 clip must be tradeable: at $8k liquidity CPMM impact is ~6%/side.
    "liquidity_min_usd": 8_000,
    # Enough history that the paper entry at T1+25m has >=60 min of 1-min bars
    # behind it (pre_entry_sigma_minutes), and no more. The v1 floor of 120
    # minutes excluded the window in which Solana attention moves actually
    # happen; see "Universe rule v2" below. No maximum age: revivals of older
    # tokens are in scope, and results are split by age bucket instead.
    "pair_age_min_minutes": 35,
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
    "decision_delay_minutes": 25,
    # Research must be frozen before T_D; a later report is flagged LATE and
    # excluded from the primary analysis.
    "research_timeout_minutes": 20,

    # ---- Research allocation ----------------------------------------------------
    # Candidates are thinned at random (not by quality) so researched and
    # unresearched candidates interleave across the day. The unresearched
    # remainder is the randomized control / null pool.
    "research_sample_prob": 0.6,
    # Fraction of researched candidates that get a second, independent C run
    # started at the same moment (self-consistency measurement only).
    "consistency_rerun_prob": 0.10,

    # ---- Paper execution ----------------------------------------------------------
    "position_usd": 250.0,
    # Swap fee by DEX id; unknown DEX -> 1%.
    "dex_fee": {"pumpswap": 0.0025, "pump_fun_amm": 0.0025, "raydium": 0.0025,
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
    "pre_entry_sigma_minutes": 60,

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
    "bracket_max_minutes": 360,
    # Trade iff Claude's p_runner >= this. Break-even hit rate for +100% vs
    # -50% is 1/3; 35 adds a margin for costs. Derived from the payoff only.
    "trade_p_runner_min": 35,
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
    "claude_model": "opus",
    # Usage caps, metered by the cost the CLI reports (Max-plan equivalent $).
    # Sized for the v2 universe rule, which nominates several times more
    # candidates per day than v1 did. session_share_cap, not these, is what
    # actually protects your own Claude quota.
    "daily_budget_usd": 40.0,
    "window5h_budget_usd": 15.0,
    # Stop taking new coins (and post-mortems) once usage of the current 5h
    # Claude session limit has risen this much since the bot's first run in
    # that window. Counts all usage in the window, your own chats included.
    "session_share_cap": 0.25,
    # Hours per day the machine is actually up (school-day duty cycle), used
    # only to pace spending across the session so the budget is not gone by
    # 8pm. hourly cap = daily / active_hours * burst_multiple.
    "active_hours_per_day": 15.0,
    "hourly_burst_multiple": 2.0,
    "per_run_budget_usd": 3.0,          # hard cap passed to each claude -p run
    # Budget reserved before starting a candidate (C + P + possible skeptic
    # typically cost ~$1.10 in total; this leaves headroom).
    "reserve_per_candidate_usd": 1.5,
    "max_concurrent_research": 2,
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
