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


if __name__ == "__main__":
    unittest.main()
