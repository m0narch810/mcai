"""A bullish frozen report must produce exactly one ENTRY message at T_D;
fade/neutral reports and late reports must produce none."""
import json
import tempfile
import unittest
from pathlib import Path

from nr.config import PREREG  # noqa: E402
from nr import config

TMP = Path(tempfile.mkdtemp())
from nr import db  # noqa: E402
db.DB_PATH, db.LEDGER_PATH, db.RAW_DIR, db.DATA_DIR = TMP / "t.db", TMP / "l.jsonl", TMP / "raw", TMP
from nr import notify, outcomes, research, sources  # noqa: E402
from tests.test_integration import fake_report  # noqa: E402

sent = []
_orig = (notify.entry, sources.pair_now)


def _fake_entry(c, rep, sk, price, liq, mcap):
    sent.append((c["id"], rep["continuation_view"], sk is not None))


def _fake_quote(pair):
    return {"priceUsd": "0.001", "liquidity": {"usd": 50000}, "marketCap": 400000}


def make(cid, view, late=False, skeptic=False, p_runner=None, version=None):
    c = db.conn()
    c.execute("INSERT INTO candidates (id,prereg_version,token,symbol,pair_address,t1_detected,t_decision,"
              "trigger_json,research_draw,research_status,rerun_draw) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
              (cid, version or config.PREREG_VERSION, f"TOK{cid}", f"S{cid}", f"PAIR{cid}",
               "2026-01-01T00:00:00.000000Z",
               "2026-01-01T00:25:00.000000Z", "{}", 0.1, "selected", 0.9))
    td = "2000-01-01T00:00:00.000000Z" if late else "9999-01-01T00:00:00.000000Z"
    research.freeze(cid, "C", "2026-01-01T00:01:00.000000Z", td,
                    {"ok": True, "obj": fake_report(view, p_runner), "cost": 0})
    if skeptic:
        research.freeze(cid, "S", "2026-01-01T00:02:00.000000Z", "9999-01-01T00:00:00.000000Z",
                        {"ok": True, "cost": 0, "obj": {
                            "strongest_bear_case": "b", "red_flags": [], "bear_case_strength": "weak",
                            "what_would_refute_bear_case": "w", "continuation_view": "neutral",
                            "research_confidence": "low", "coverage_notes": "", "sources": []}})
    return dict(c.execute("SELECT * FROM candidates WHERE id=?", (cid,)).fetchone())


class EntryNotify(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init()
        notify.entry, sources.pair_now = _fake_entry, _fake_quote

    @classmethod
    def tearDownClass(cls):
        notify.entry, sources.pair_now = _orig

    def test_only_bullish_on_time_reports_post(self):
        for cid, view, late, sk in [(1, "continue", False, True), (2, "strong_continue", False, False),
                                    (3, "fade", False, False), (4, "neutral", False, False),
                                    (5, "strong_fade", False, False), (6, "continue", True, False)]:
            outcomes.take_entry(make(cid, view, late, sk))
        self.assertEqual(sent, [(1, "continue", True), (2, "strong_continue", False)])

    def test_rank_gate_decides_not_the_label(self):
        # The gate is the top quarter of this arm's EARLIER on-time scores from
        # the SAME version. Other versions' scores are ignored, nothing trades
        # during warm-up, and then a fade label with a top-quarter p_runner is
        # a trade while a continue label below the bar is not; late never is.
        del sent[:]
        q, need = PREREG["trade_rank_quantile"], PREREG["trade_rank_min_prior"]
        for i in range(30):                                   # another model's scale
            make(200 + i, "fade", p_runner=90, version="other")
        for i in range(need - 1):                             # warm-up: need-1 priors
            make(300 + i, "fade", p_runner=1 + i % 8)
        outcomes.take_entry(make(399, "fade", p_runner=99))
        self.assertEqual(sent, [])                            # one short of warm-up
        prior = [1 + i % 8 for i in range(need - 1)] + [99]
        bar = sorted(prior)[max(0, -(-int(q * 100) * len(prior) // 100) - 1)]
        for cid, view, p in [(401, "fade", bar), (402, "continue", bar - 1)]:
            outcomes.take_entry(make(cid, view, p_runner=p))
        outcomes.take_entry(make(403, "fade", late=True, p_runner=99))
        self.assertEqual([x[0] for x in sent], [401])
        gate = json.loads(db.conn().execute(
            "SELECT report_json FROM reports WHERE candidate_id=401 AND arm='C'").fetchone()[0])["_gate"]
        self.assertEqual((gate["threshold"], gate["n_prior"]), (bar, need))

    def test_first_report_has_no_history_and_is_not_taken(self):
        from nr.research import rank_gate
        self.assertFalse(rank_gate("P", 50, "0000", False)["take"])

if __name__ == "__main__":
    unittest.main()
