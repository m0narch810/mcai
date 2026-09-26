"""SQLite storage. Evidence, reports and entries are append-only: triggers abort
any UPDATE or DELETE, and every frozen record is also written to a hash-chained
JSONL ledger so tampering with the DB file is detectable."""
import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timezone

from .config import DATA_DIR, DB_PATH, LEDGER_PATH, RAW_DIR

SCHEMA = """
CREATE TABLE IF NOT EXISTS candidates (
    id              INTEGER PRIMARY KEY,
    prereg_version  TEXT NOT NULL,
    token           TEXT NOT NULL,
    symbol          TEXT,
    name            TEXT,
    pair_address    TEXT NOT NULL,
    dex_id          TEXT,
    t1_detected     TEXT NOT NULL,      -- ISO UTC
    t_decision      TEXT NOT NULL,      -- T1 + decision delay
    trigger_json    TEXT NOT NULL,      -- the exact numbers that met the rule
    research_draw   REAL NOT NULL,      -- uniform draw for random allocation
    research_status TEXT NOT NULL,      -- selected | not_sampled | no_budget
    rerun_draw      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_cand_token ON candidates(token, t1_detected);

CREATE TABLE IF NOT EXISTS packets (
    candidate_id    INTEGER PRIMARY KEY REFERENCES candidates(id),
    t2_collected    TEXT NOT NULL,
    packet_json     TEXT NOT NULL,
    sha256          TEXT NOT NULL
);

-- arm: C (full research), C2 (consistency rerun), P (packet only), S (skeptic)
CREATE TABLE IF NOT EXISTS reports (
    id              INTEGER PRIMARY KEY,
    candidate_id    INTEGER NOT NULL REFERENCES candidates(id),
    arm             TEXT NOT NULL,
    t3_started      TEXT NOT NULL,
    t4_frozen       TEXT NOT NULL,
    late            INTEGER NOT NULL,   -- frozen after t_decision
    ok              INTEGER NOT NULL,   -- parsed & schema-valid
    report_json     TEXT,               -- parsed report (NULL if not ok)
    error           TEXT,
    cost_usd        REAL,
    raw_path        TEXT,
    sha256          TEXT NOT NULL,
    UNIQUE(candidate_id, arm)
);

CREATE TABLE IF NOT EXISTS entries (
    candidate_id    INTEGER PRIMARY KEY REFERENCES candidates(id),
    t5_quote        TEXT NOT NULL,
    price_usd       REAL,
    liquidity_usd   REAL,
    mcap_usd        REAL,
    pair_json       TEXT,
    ok              INTEGER NOT NULL,
    note            TEXT
);

CREATE TABLE IF NOT EXISTS liquidity_obs (
    candidate_id    INTEGER NOT NULL REFERENCES candidates(id),
    t               TEXT NOT NULL,
    price_usd       REAL,
    liquidity_usd   REAL,
    mcap_usd        REAL,
    present         INTEGER NOT NULL,   -- pair still returned by the API
    PRIMARY KEY(candidate_id, t)
);

-- Outcomes are derived and may be recomputed, so this table is mutable.
CREATE TABLE IF NOT EXISTS outcomes (
    candidate_id    INTEGER PRIMARY KEY REFERENCES candidates(id),
    computed_at     TEXT NOT NULL,
    outcome_json    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ohlcv (
    candidate_id    INTEGER NOT NULL REFERENCES candidates(id),
    ts              INTEGER NOT NULL,   -- bar open, unix seconds
    o REAL, h REAL, l REAL, c REAL, v REAL,
    PRIMARY KEY(candidate_id, ts)
);

CREATE TABLE IF NOT EXISTS postmortems (
    candidate_id    INTEGER PRIMARY KEY REFERENCES candidates(id),
    created_at      TEXT NOT NULL,
    ok              INTEGER NOT NULL,
    postmortem_json TEXT,
    error           TEXT,
    cost_usd        REAL,
    raw_path        TEXT
);

CREATE TABLE IF NOT EXISTS usage (
    t               TEXT NOT NULL,
    purpose         TEXT NOT NULL,
    candidate_id    INTEGER,
    cost_usd        REAL NOT NULL
);

-- Real Claude plan usage, as reported by the CLI's rate_limit_event.
CREATE TABLE IF NOT EXISTS ratelimit (
    t               TEXT NOT NULL,
    resets_at       INTEGER NOT NULL,   -- unix seconds, identifies the 5h window
    util_5h         REAL NOT NULL,      -- 0..1 of the 5h session limit
    util_7d         REAL
);

-- One row per 5-minute slot the loop was alive. The PC is off during school
-- hours, so the experiment has a known duty cycle rather than 24h coverage;
-- analysis needs to be able to state which hours were actually observed
-- instead of silently treating the sample as round-the-clock.
CREATE TABLE IF NOT EXISTS heartbeat (
    slot            TEXT PRIMARY KEY    -- ISO UTC truncated to 5 minutes
);

CREATE TABLE IF NOT EXISTS events (
    t               TEXT NOT NULL,
    level           TEXT NOT NULL,
    msg             TEXT NOT NULL
);

-- v6 rug guard: candidates whose pool liquidity the deployer can remove.
-- Selected ones are not researched (never traded); all still get a paper
-- entry so the guard itself can be audited.
CREATE TABLE IF NOT EXISTS exclusions (
    candidate_id    INTEGER PRIMARY KEY REFERENCES candidates(id),
    t               TEXT NOT NULL,
    reason          TEXT NOT NULL
);
"""

FROZEN_TABLES = ["candidates", "packets", "reports", "entries", "liquidity_obs", "exclusions"]


def _freeze_triggers():
    out = []
    for t in FROZEN_TABLES:
        for op in ("UPDATE", "DELETE"):
            out.append(
                f"CREATE TRIGGER IF NOT EXISTS freeze_{t}_{op.lower()} "
                f"BEFORE {op} ON {t} BEGIN "
                f"SELECT RAISE(ABORT, '{t} is append-only'); END;")
    return "\n".join(out)


def now_utc() -> datetime:
    """True UTC (drift-corrected), not the raw PC clock."""
    from . import clock
    return clock.now_utc()


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def parse_iso(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)


def sha(obj) -> str:
    data = obj if isinstance(obj, str) else json.dumps(obj, sort_keys=True)
    return hashlib.sha256(data.encode()).hexdigest()


_local = threading.local()
_ledger_lock = threading.Lock()


def conn() -> sqlite3.Connection:
    c = getattr(_local, "conn", None)
    if c is None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=ON")
        _local.conn = c
    return c


def init():
    c = conn()
    c.executescript(SCHEMA)
    c.executescript(_freeze_triggers())


def beat():
    """Mark this 5-minute slot as covered. Cheap, idempotent, append-only."""
    now = now_utc()
    slot = now.replace(minute=now.minute - now.minute % 5, second=0,
                       microsecond=0).isoformat().replace("+00:00", "Z")
    conn().execute("INSERT OR IGNORE INTO heartbeat VALUES (?)", (slot,))


def coverage(hours: int = 24) -> float:
    """Fraction of the last N hours the loop was running."""
    from datetime import timedelta
    since = iso(now_utc() - timedelta(hours=hours))
    seen = conn().execute("SELECT COUNT(*) FROM heartbeat WHERE slot>=?",
                          (since,)).fetchone()[0]
    return min(1.0, seen / (hours * 12))


def ledger(kind: str, record: dict):
    """Append a record to the hash-chained ledger."""
    with _ledger_lock:
        prev = "0" * 64
        if LEDGER_PATH.exists():
            with open(LEDGER_PATH, "rb") as f:
                size = f.seek(0, 2)
                back = 4096
                while True:   # widen the tail until it holds one complete line
                    f.seek(max(0, size - back))
                    tail = f.read()
                    lines = tail.splitlines()
                    if back >= size or len(lines) >= 2:
                        break
                    back *= 4
                if lines:
                    prev = json.loads(lines[-1])["hash"]
        body = {"t": iso(now_utc()), "kind": kind, "record": record, "prev": prev}
        body["hash"] = sha(body)
        with open(LEDGER_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(body, sort_keys=True) + "\n")


def verify_ledger() -> tuple[bool, int, str]:
    if not LEDGER_PATH.exists():
        return True, 0, "empty"
    prev = "0" * 64
    n = 0
    with open(LEDGER_PATH, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            body = json.loads(line)
            h = body.pop("hash")
            if body["prev"] != prev or sha(body) != h:
                return False, n, f"chain broken at line {n}"
            prev = h
    return True, n, "ok"


def log(level: str, msg: str):
    """Record an event. Logging must never be able to abort the caller: a
    token whose symbol is outside cp1252 once killed a whole detect cycle,
    because stdout is redirected to a file handle Windows encodes as cp1252
    and the UnicodeEncodeError propagated out of detect_once. Every sink is
    guarded individually and degrades to ASCII rather than raising."""
    conn().execute("INSERT INTO events VALUES (?,?,?)", (iso(now_utc()), level, msg))
    line = f"[{iso(now_utc())[:19]}] {level.upper():5} {msg}"
    try:
        print(line, flush=True)
    except (UnicodeEncodeError, OSError, ValueError):
        try:
            print(line.encode("ascii", "replace").decode("ascii"), flush=True)
        except Exception:
            pass
    try:
        with _ledger_lock, open(DATA_DIR / "run.log", "a", encoding="utf-8") as f:
            f.write(line + chr(10))
    except OSError:
        pass
    if level == "error":
        try:
            from . import notify
            notify.error(msg)
        except Exception:
            pass
