"""Fetch 1-min bars for v5.1 coins whose 6h window is complete, into a
scratch JSON file. Reads the experiment DB, never writes to it."""
import json
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from nr import db, outcomes, sources  # noqa: E402

sources._MIN_GAP[sources.GT_HOST] = 4.5
OUT = Path(__file__).with_name("bars_v51.json")
V = "19ce3af93839"

db.init()
have = json.loads(OUT.read_text()) if OUT.exists() else {}
cut = db.iso(db.now_utc() - timedelta(hours=6, minutes=5))
rows = [dict(r) for r in db.conn().execute(
    "SELECT c.* FROM candidates c JOIN entries e ON e.candidate_id=c.id "
    "WHERE c.prereg_version=? AND e.ok=1 AND e.liquidity_usd>0 AND c.t_decision<? "
    "ORDER BY c.id", (V, cut))]
todo = [c for c in rows if str(c["id"]) not in have]
print(f"{len(rows)} complete, {len(todo)} to fetch", flush=True)
for i, c in enumerate(todo):
    try:
        have[str(c["id"])] = outcomes.fetch_bars(c, cache=False)
    except Exception as e:
        print("fail", c["id"], repr(e), flush=True)
        continue
    if i % 10 == 9:
        OUT.write_text(json.dumps(have))
        print(i + 1, flush=True)
OUT.write_text(json.dumps(have))
print("done", len(have))
