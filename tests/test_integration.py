"""Integration: temp DB, freeze triggers, ledger chain, outcome evaluation on
real historical GeckoTerminal bars, analysis report paths."""
import json
import random
import sqlite3
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from nr import config

TMP = Path(tempfile.mkdtemp())
config.DATA_DIR = TMP
from nr import db  # noqa: E402
db.DB_PATH, db.LEDGER_PATH, db.RAW_DIR, db.DATA_DIR = TMP / "t.db", TMP / "l.jsonl", TMP / "raw", TMP
from nr import analysis, outcomes, research, sources  # noqa: E402
analysis.DATA_DIR = TMP

POOL = "7a8xxAJBELDo6P9dikSYctdw6ce8F4mWr3ahcAD8Ao49"   # liquid Solana pool


def fake_report(view):
    return {"what_is_happening": "x", "narrative": {"description": "d", "stage": "nascent",
            "potential": random.choice(["weak", "strong"]), "why_spreading": "w"},
            "token_connection": {"assessment": "moderate", "why_this_token": "w",
            "competing_tokens": "c", "is_first_or_canonical": "yes", "ticker_hijack_risk": "low"},
            "authenticity": {"assessment": "mixed", "evidence": "e", "uncertainty": "u"},
            "market_feasibility": {"assessment": "adequate", "structural_risks": []},
            "counterargument": "c", "unknowns": [], "source_coverage": {
                "x_twitter": "inaccessible", "reddit": "searched_nothing_found",
                "telegram": "not_attempted", "discord": "not_attempted", "news": "not_attempted",
                "project_site": "searched_found", "notes": ""},
            "thesis": "t", "continuation_view": view, "research_confidence": "low",
            "observation_window": "6h", "sources": []}


class Pipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init()

    def test_1_freeze_and_ledger(self):
        c = db.conn()
        c.execute("INSERT INTO candidates (id,prereg_version,token,pair_address,t1_detected,"
                  "t_decision,trigger_json,research_draw,research_status,rerun_draw) "
                  "VALUES (999,'v','T','P','a','b','{}',0.1,'selected',0.5)")
        with self.assertRaises(sqlite3.IntegrityError):
            c.execute("UPDATE candidates SET token='X' WHERE id=999")
        with self.assertRaises(sqlite3.IntegrityError):
            c.execute("DELETE FROM candidates WHERE id=999")
        for i in range(3):
            db.ledger("test", {"i": i})
        self.assertTrue(db.verify_ledger()[0])
        lines = db.LEDGER_PATH.read_text().splitlines()
        rec = json.loads(lines[1]); rec["record"]["i"] = 42
        lines[1] = json.dumps(rec, sort_keys=True)
        db.LEDGER_PATH.write_text("\n".join(lines) + "\n")
        self.assertFalse(db.verify_ledger()[0])
        db.LEDGER_PATH.unlink()

    def test_2_evaluate_real_bars_and_analysis(self):
        now = db.now_utc()
        td = now - timedelta(hours=26)
        pair = sources.pair_now(POOL)
        self.assertIsNotNone(pair)
        c = db.conn()
        ids = []
        for i, view in enumerate(["fade", "continue", "neutral"]):
            cid = 1000 + i
            ids.append(cid)
            c.execute("INSERT INTO candidates (id,prereg_version,token,symbol,pair_address,dex_id,"
                      "t1_detected,t_decision,trigger_json,research_draw,research_status,rerun_draw)"
                      " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                      (cid, config.PREREG_VERSION, pair["baseToken"]["address"], "TST", POOL,
                       pair["dexId"], db.iso(td - timedelta(minutes=25)), db.iso(td),
                       json.dumps({"mcap_usd": 1e6, "liquidity_usd": 1e6, "vol_accel_m5_vs_h6": 3,
                                   "h1_buys": 200, "h1_sells": 150}),
                       0.1, "selected" if i else "not_sampled", 0.9))
            liq = pair["liquidity"]["usd"]
            c.execute("INSERT INTO entries VALUES (?,?,?,?,?,?,?,?)",
                      (cid, db.iso(td), float(pair["priceUsd"]), liq, 1e6, "{}", 1, None))
            for h in range(0, 26 * 60, 20):
                c.execute("INSERT INTO liquidity_obs VALUES (?,?,?,?,?,?)",
                          (cid, db.iso(td + timedelta(minutes=h)), float(pair["priceUsd"]), liq, 1e6, 1))
            c.execute("INSERT INTO packets VALUES (?,?,?,?)", (cid, db.iso(td), json.dumps({
                "trigger_at_detection": {"mcap_usd": 1e6, "liquidity_usd": 1e6,
                                         "vol_accel_m5_vs_h6": 3, "h1_buys": 200, "h1_sells": 150},
                "market": {"geckoterminal": {"available": False}},
                "structure": {"available": False},
                "social": {"project_socials": [], "project_websites": [],
                           "dexscreener_paid_boosts_active": None},
                "same_ticker_or_name_tokens": []}), "h"))
            for arm in ("C", "P"):
                research.freeze(cid, arm, db.iso(td - timedelta(minutes=20)), "9999",
                                {"ok": True, "obj": fake_report(view), "cost": 0.0})
        cand = dict(c.execute("SELECT * FROM candidates WHERE id=1000").fetchone())
        out = outcomes.evaluate(cand)
        print("\noutcome sample:", json.dumps({k: out.get(k) for k in (
            "bars", "entry_ref", "returns", "sigma_hourly_pre", "sigma_barriers", "mfe_6h",
            "mae_6h")}, default=str)[:900])
        self.assertGreater(out["bars"], 100)
        self.assertIn("1440", out["returns"])
        for cid in ids:
            cc = dict(c.execute("SELECT * FROM candidates WHERE id=?", (cid,)).fetchone())
            outcomes.store(cid, outcomes.evaluate(cc))
        txt = analysis.report()
        print(txt[:2500])
        self.assertIn("Null sanity", txt)


if __name__ == "__main__":
    unittest.main()
