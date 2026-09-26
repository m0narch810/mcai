"""v6: shadow-book simulator and rug guard."""
import unittest

from nr import books, outcomes, rugguard
from nr.config import PREREG

T0 = 1_800_000_000          # fill time (bar-aligned)
LIQ = 100_000.0


def trade(path, fee=0.0025, vol=1e9):
    """1-min bars from the fill; path = [(o, h, l, c), ...]."""
    bars = [(T0 - 60, 1.0, 1.0, 1.0, 1.0, vol)]
    bars += [(T0 + 60 * i, o, h, l, c, vol) for i, (o, h, l, c) in enumerate(path)]
    return books.prep({"bars": bars, "t_fill": T0, "entry": 1.0, "liq_in": LIQ,
                       "obs": [(T0 + 60 * i, LIQ) for i in range(0, 1500, 20)], "fee": fee})


def rt(exit_px, frac=1.0):
    """Expected net for selling frac of the position at exit_px (constant liquidity)."""
    return outcomes._round_trip(1.0, exit_px, LIQ, LIQ, 0.0025) if frac == 1.0 else None


class Books(unittest.TestCase):
    def test_primary_matches_bracket_target(self):
        t = trade([(1, 1.1, 0.95, 1.05), (1.05, 2.2, 1.0, 2.1)])
        self.assertAlmostEqual(books.sim(t, -0.5, [(1.0, 1.0)], max_min=30), rt(2.0), places=6)

    def test_bar_spanning_both_levels_is_a_stop(self):
        t = trade([(1, 1.1, 0.95, 1.0), (1.0, 2.5, 0.4, 1.0)])
        self.assertAlmostEqual(books.sim(t, -0.5, [(1.0, 1.0)], max_min=30), rt(0.5), places=6)

    def test_no_target_on_fill_bar(self):
        t = trade([(1, 3.0, 0.9, 1.0)] + [(1, 1, 1, 1)] * 40)
        self.assertAlmostEqual(books.sim(t, -0.5, [(1.0, 1.0)], max_min=30), rt(1.0), places=6)

    def test_time_exit_at_last_close(self):
        t = trade([(1, 1.2, 0.9, 1.1)] * 40)
        self.assertAlmostEqual(books.sim(t, -0.5, [(1.0, 1.0)], max_min=30), rt(1.1), places=6)

    def test_ladder_partial_fills_then_time(self):
        # climbs to +200%: the +100% and +175% thirds fill, the +250% third
        # is sold at the time limit's close.
        path = [(1, 1.1, 1.0, 1.05), (1.05, 2.1, 1.0, 2.0), (2.0, 2.9, 1.9, 2.8)] + [(2.8, 2.9, 2.7, 2.8)] * 5
        t = trade(path)
        lv = [(1.0, 1 / 3), (1.75, 1 / 3), (2.5, 1 / 3)]
        got = books.sim(t, -0.5, lv, max_min=6)
        three_thirds = books.sim(t, -0.5, [(1.0, 1.0)], max_min=6)
        self.assertGreater(got, three_thirds)     # sold two thirds higher than +100%

    def test_stale_exit_when_first_target_never_fills(self):
        t = trade([(1, 1.2, 0.9, 1.1)] * 60)
        self.assertAlmostEqual(books.sim(t, -0.5, [(1.0, 0.5)], stale_min=30, max_min=360),
                               rt(1.1), places=6)

    def test_break_even_stop_after_first_trim(self):
        # half sold at +100%, then it collapses through entry and on through -50%
        path = [(1, 1.1, 1.0, 1.05), (1.05, 2.1, 1.0, 2.0), (2.0, 2.0, 0.6, 0.7), (0.7, 0.7, 0.3, 0.35)]
        t = trade(path)
        with_be = books.sim(t, -0.5, [(1.0, 0.5), (3.0, 0.5)], be=True, max_min=30)
        without = books.sim(t, -0.5, [(1.0, 0.5), (3.0, 0.5)], be=False, max_min=30)
        self.assertGreater(with_be, without)

    def test_lock_stop_after_first_trim(self):
        # half sold at +100%, then a bar dips through +50% and closes +45%:
        # the lock sells the rest there (worse of level/open/close) instead
        # of riding the fade to +-0% at the time limit.
        path = [(1, 1.1, 1.0, 1.05), (1.05, 2.1, 1.0, 2.0), (2.0, 2.0, 1.6, 1.7),
                (1.7, 1.7, 1.4, 1.45)] + [(1.0, 1.0, 1.0, 1.0)] * 10
        t = trade(path)
        lv = [(1.0, 0.5), (3.0, 0.5)]
        locked = books.sim(t, -0.5, lv, lock=0.5, max_min=30)
        half = lambda px: outcomes._round_trip(1.0, px, LIQ, LIQ, 0.0025)
        self.assertGreater(locked, books.sim(t, -0.5, lv, max_min=30))
        self.assertAlmostEqual(locked, (half(2.0) + half(1.45)) / 2, delta=0.01)

    def test_empty_pool_at_exit_is_worth_zero(self):
        t = trade([(1, 1.2, 0.9, 1.1)] * 40)
        t["obs"] = [(T0 + 60, 0.0)]
        books.prep(t)
        self.assertEqual(books.sim(t, -0.5, [(1.0, 1.0)], max_min=30), -1.0)


class RugGuard(unittest.TestCase):
    def test_curve_is_safe(self):
        self.assertFalse(rugguard.assess({"bonding_curve": True}, {}, {})["lp_removable"])

    def test_locked_or_burned_is_safe(self):
        self.assertFalse(rugguard.assess({}, {"lp_locked_pct_top_markets": [100]}, {})["lp_removable"])
        self.assertFalse(rugguard.assess({}, {"lp_locked_pct_top_markets": [0]},
                                         {"security": {"lp_burn_ratio": 1.0}})["lp_removable"])

    def test_unlocked_or_unverifiable_is_removable(self):
        self.assertTrue(rugguard.assess({}, {"lp_locked_pct_top_markets": [5]},
                                        {"security": {"lp_burn_ratio": 0.0}})["lp_removable"])
        self.assertTrue(rugguard.assess({}, {}, {})["lp_removable"])

    def test_thresholds_are_the_preregistered_ones(self):
        g = PREREG["rug_guard"]
        self.assertTrue(rugguard.assess({}, {"lp_locked_pct_top_markets": [g["lp_locked_min_pct"] - 1]},
                                        {})["lp_removable"])


if __name__ == "__main__":
    unittest.main()
