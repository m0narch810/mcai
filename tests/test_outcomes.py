"""Outcome-logic tests: sequential first touch, fill-bar rule, costs, null."""
import math
import random
import unittest

from nr import outcomes as o

T = 1_800_000_000  # fill time, aligned mid-bar below
def bar(ts, op, h, l, c): return (ts, op, h, l, c, 1.0)


class FirstTouch(unittest.TestCase):
    def test_stop_before_target_is_loss_even_if_target_later(self):
        bars = [bar(T - 30, 1, 1, 1, 1), bar(T + 30, 1, 1.0, 0.7, 0.8),
                bar(T + 90, 0.8, 1.6, 0.8, 1.5)]
        up = o._first_touch(bars, T, 1.5, "up", T + 3600)
        dn = o._first_touch(bars, T, 0.75, "down", T + 3600)
        self.assertEqual(o._race(up, dn), "down")

    def test_fill_bar_high_ignored_low_counted(self):
        # fill bar spans T-30..T+30: its high may have printed before entry
        bars = [bar(T - 30, 1, 2.0, 0.5, 1)]
        self.assertIsNone(o._first_touch(bars, T, 1.5, "up", T + 3600))
        self.assertEqual(o._first_touch(bars, T, 0.6, "down", T + 3600), 0.0)

    def test_same_bar_both_levels_is_loss(self):
        bars = [bar(T - 30, 1, 1, 1, 1), bar(T + 30, 1, 1.6, 0.7, 1)]
        up = o._first_touch(bars, T, 1.5, "up", T + 3600)
        dn = o._first_touch(bars, T, 0.75, "down", T + 3600)
        self.assertEqual(o._race(up, dn), "down")

    def test_window_end_respected(self):
        bars = [bar(T + 7200, 1, 3, 1, 1)]
        self.assertIsNone(o._first_touch(bars, T, 2, "up", T + 3600))


class Bracket(unittest.TestCase):
    """v3 trade: +100% target, -50% stop, else time exit."""
    def run_(self, bars, mins=360):
        return o.bracket(bars, T, 1.0, 1.0, -0.5, mins)

    def test_stop_first_is_loss_even_if_target_later(self):
        bars = [bar(T - 30, 1, 1, 1, 1), bar(T + 30, 1, 1, 0.45, 0.5), bar(T + 90, 0.5, 3, 0.5, 3)]
        res, _, px = self.run_(bars)
        self.assertEqual(res, "stop")
        self.assertLessEqual(px, 0.5)

    def test_same_bar_both_levels_is_loss(self):
        bars = [bar(T - 30, 1, 1, 1, 1), bar(T + 30, 1, 2.5, 0.4, 1)]
        self.assertEqual(self.run_(bars)[0], "stop")

    def test_target_needs_trade_through_and_fills_at_level(self):
        touch = [bar(T - 30, 1, 1, 1, 1), bar(T + 30, 1, 2.0, 0.9, 1.5)]
        self.assertEqual(self.run_(touch)[0], "time")
        through = [bar(T - 30, 1, 1, 1, 1), bar(T + 30, 1, 2.4, 0.9, 2.3)]
        res, _, px = self.run_(through)
        self.assertEqual((res, px), ("target", 2.0))

    def test_fill_bar_high_cannot_fill_target(self):
        bars = [bar(T - 30, 1, 5.0, 0.9, 1)]
        self.assertEqual(self.run_(bars)[0], "time")

    def test_gap_through_stop_fills_at_open_not_level(self):
        bars = [bar(T - 30, 1, 1, 1, 1), bar(T + 30, 0.2, 0.25, 0.1, 0.15)]
        res, _, px = self.run_(bars)
        self.assertEqual(res, "stop")
        self.assertAlmostEqual(px, 0.15)

    def test_time_exit_uses_last_close_before_end(self):
        bars = [bar(T - 30, 1, 1, 1, 1), bar(T + 30, 1, 1.2, 0.9, 1.1), bar(T + 7200, 1.1, 1.3, 1, 1.3)]
        res, ts, px = self.run_(bars, mins=60)
        self.assertEqual((res, ts, px), ("time", T + 3600, 1.1))

    def test_log_symmetric_bracket_random_walk_is_half(self):
        # Null: x2 / x0.5 on a driftless log random walk -> target first ~0.5.
        rng, wins, n = random.Random(3), 0, 0
        for _ in range(1500):
            p, t, bars = 1.0, T - 30, []
            for _ in range(3000):
                q = p * math.exp(rng.gauss(0, 0.03))
                bars.append((t, p, max(p, q), min(p, q), q, 1.0))
                p, t = q, t + 60
            res = o.bracket(bars, T, 1.0, 1.0, -0.5, 3000)[0]
            if res != "time":
                n += 1
                wins += res == "target"
        self.assertAlmostEqual(wins / n, 0.5, delta=0.05)


class Costs(unittest.TestCase):
    def test_flat_price_loses_costs(self):
        r = o._round_trip(1.0, 1.0, 50_000, 50_000, 0.0025)
        self.assertLess(r, -0.02)
        self.assertGreater(r, -0.05)

    def test_thin_exit_liquidity_is_total_loss(self):
        self.assertEqual(o._round_trip(1.0, 5.0, 50_000, 1_000, 0.0025), -1.0)
        self.assertEqual(o._round_trip(1.0, 5.0, 50_000, None, 0.0025), -1.0)

    def test_impact_grows_with_smaller_pool(self):
        self.assertLess(o._round_trip(1, 1, 10_000, 10_000, 0.0025),
                        o._round_trip(1, 1, 200_000, 200_000, 0.0025))


class Null(unittest.TestCase):
    def test_symmetric_barrier_random_walk_is_half(self):
        """Driftless random walk: +/-k sigma first touch must be ~0.5 (the
        same-bar loss rule biases slightly downward)."""
        rng = random.Random(1)
        ups = n = 0
        for _ in range(1500):
            t0 = T - 3600 * 2
            p, bars = 1.0, []
            for m in range(8 * 60):
                ts = t0 + m * 60
                path = [p]
                for _ in range(6):
                    p *= math.exp(rng.gauss(0, 0.004))
                    path.append(p)
                bars.append((ts, path[0], max(path), min(path), path[-1], 1.0))
            t_fill = T + 17
            sig = o._sigma_hourly(bars, t_fill)
            entry = o._last_close_at(bars, t_fill)
            up = o._first_touch(bars, t_fill, entry * math.exp(sig), "up", t_fill + 6 * 3600)
            dn = o._first_touch(bars, t_fill, entry * math.exp(-sig), "down", t_fill + 6 * 3600)
            r = o._race(up, dn)
            if r != "neither":
                n += 1
                ups += r == "up"
        frac = ups / n
        print(f"\nnull up-first fraction = {frac:.3f} (n={n})")
        self.assertTrue(0.42 <= frac <= 0.56, frac)


class RugSpike(unittest.TestCase):
    """Dust trades after an LP pull print absurd highs; they are not fills."""
    def test_si_rug_spike_is_not_a_target(self):
        # real #497 bars: $392 of volume printed x2,000,000 against a $170k pool
        bars = [(T, 1.151e-4, 1.172e-4, 1.140e-4, 1.151e-4, 7728.0),
                (T + 60, 1.151e-4, 2.675e+2, 1.142e-4, 9.481e-1, 392.0),
                (T + 180, 9.481e-1, 9.570e+3, 9.481e-1, 3.350e+0, 45.0)]
        res = o.bracket(bars, T + 1, 1.138e-4, 1.0, -0.5, 360, lambda ts: 171_265.0)[0]
        self.assertNotEqual(res, "target")

    def test_genuine_climb_still_fills(self):
        # A real breakout climbs over several bars on real volume (Avery went
        # +100% in 14 one-minute bars on ~$3k/min into an ~$18k pool).
        p, bars = 2.929e-5, []
        for m in range(6):
            bars.append((T + 60 * m, p, p * 1.2, p * 0.98, p * 1.18, 2500.0))
            p *= 1.18
        res = o.bracket(bars, T + 1, 2.929e-5, 1.0, -0.5, 360, lambda ts: 18_000.0)[0]
        self.assertEqual(res, "target")

    def test_zero_volume_or_unknown_liquidity_never_fills(self):
        self.assertFalse(o._reachable(1.0, 0.0, 50_000, 1.5))
        self.assertFalse(o._reachable(1.0, 1e6, None, 1.5))
        self.assertFalse(o._reachable(1.0, 1e6, 0.0, 1.5))

    def test_cpmm_bound_with_slack(self):
        # $1k into a $40k pool: strict CPMM bound (1.05)^2; with 10x slack (1.5)^2
        self.assertTrue(o._reachable(1.0, 1_000, 40_000, 2.2))
        self.assertFalse(o._reachable(1.0, 1_000, 40_000, 2.3))

    def test_dalguddi_real_curve_minute_passes(self):
        # real #509 bar: +19% on $487 into a ~$10.8k curve (strict bound 1.188x)
        self.assertTrue(o._reachable(8.242e-6, 487, 10_801.72, 9.838e-6))


if __name__ == "__main__":
    unittest.main()
