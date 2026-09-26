# Exit-rule and pre-trade feature study (2026-09-26)

What this study found and what was done about it is written up in
`LAB_NOTEBOOK.md`.

- `rr_sim.py` is the exit simulator. It reproduces all 91 stored v4 brackets.
- `rr_search.py` runs the 3,840-rule search. Rules are ranked on the v5.1 time
  halves A and B, and v4 is kept as a holdout. The output is
  `search_final.txt`.
- `features.py` holds pre-registered pre-trade hypotheses H1-H10. The output
  is `features_results.txt`.
- `exits2.py` tests trencher-style exits: stale-exit, half at 2x and ride,
  and trim ladders. The output is `exits2_results.txt`.
- `gates.py` compares gate variants on the coins Claude researched.
- `dip.py` tests dip entry against immediate entry.
- `fetch_bars.py` fetched the v5.1 1-min bars (`bars_v51.json`). The v4 bars
  are in the ohlcv table of the experiment DB.

Run from the repo root, for example:
`python research/2026-09-26_exits_and_features/exits2.py`
