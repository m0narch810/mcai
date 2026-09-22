# Data snapshot

Point-in-time copy of the live experiment state taken 2026-09-22 21:42 UTC.

- `research.db` - SQLite: candidates, packets, reports, entries, outcomes, events, usage, heartbeat.
  Consistent copy made with the SQLite backup API (no WAL needed).
- `run.log` - human-readable event log.

The raw Claude outputs per candidate/arm are in `data/raw/`, and the hash-chained
append-only ledger is `data/ledger.jsonl`. The live DB in `data/` is gitignored.

To run against this snapshot: `cp snapshot/research.db data/research.db`.
