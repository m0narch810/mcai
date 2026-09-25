"""v4 bonding-curve tests: curve depth, graduation guard, bar stitching."""
import unittest
from unittest import mock

from nr import outcomes as o
from nr import sources as s

SOL = "So11111111111111111111111111111111111111112"
LAUNCH_NATIVE = 30 / 1_073_000_191          # SOL per token at launch


def curve(native, sol_usd=150.0, **kw):
    return dict({"dexId": "pumpfun", "pairAddress": "CURVE", "quoteToken": {"address": SOL},
                 "priceNative": str(native), "priceUsd": str(native * sol_usd),
                 "liquidity": None}, **kw)


class CurveDepth(unittest.TestCase):
    def test_launch_is_30_virtual_sol(self):
        self.assertAlmostEqual(s.curve_vsol(curve(LAUNCH_NATIVE)), 30.0, places=3)

    def test_liquidity_is_twice_the_sol_side(self):
        p = s.with_curve_liquidity(curve(LAUNCH_NATIVE, sol_usd=150.0))
        self.assertAlmostEqual(p["liquidity"]["usd"], 2 * 30 * 150, delta=1)
        self.assertTrue(p["liquidity"]["curve"])

    def test_completion_price_is_115_virtual_sol(self):
        # 793.1M tokens sold: vTok = 1,073,000,191 - 793,100,000
        vtok = 1_073_000_191 - 793_100_000
        native = (s.PUMP_CURVE_K / vtok) / vtok
        self.assertAlmostEqual(s.curve_vsol(curve(native)), 115.0, delta=0.1)
        # a finished curve is not a live pool
        self.assertIsNone((s.with_curve_liquidity(curve(native))["liquidity"] or {}).get("usd"))

    def test_graduated_token_never_picks_the_curve(self):
        live = curve(LAUNCH_NATIVE * 2)
        swap = {"dexId": "pumpswap", "pairAddress": "SWAP", "quoteToken": {"address": SOL},
                "liquidity": {"usd": 5_000.0}}
        self.assertEqual(s.best_pair([live, swap], [SOL])["pairAddress"], "SWAP")
        self.assertEqual(s.best_pair([live], [SOL])["pairAddress"], "CURVE")

    def test_other_dexes_untouched(self):
        p = {"dexId": "meteoradbc", "priceNative": "1e-7", "priceUsd": "1e-5", "liquidity": None}
        self.assertIs(s.with_curve_liquidity(p), p)

    def test_curve_impact_matches_cpmm(self):
        """Buying $250 into a 30-vSOL curve with the CPMM model must match the
        exact constant-product result on the virtual reserves."""
        sol_usd, spend_sol = 150.0, 250 / 150.0
        vsol, vtok = 30.0, 1_073_000_191
        got = vtok - s.PUMP_CURVE_K / (vsol + spend_sol)      # tokens out, exact
        exact_avg = spend_sol / got                           # SOL per token paid
        liq = s.with_curve_liquidity(curve(LAUNCH_NATIVE, sol_usd))["liquidity"]["usd"]
        model_avg = LAUNCH_NATIVE * (1 + 250 / (liq / 2))     # _round_trip's impact_in
        self.assertAlmostEqual(model_avg / exact_avg, 1.0, delta=0.01)


class Stitch(unittest.TestCase):
    def test_curve_until_graduation_then_pumpswap(self):
        cb = {t: [t, 1, 1, 1, 1, 1] for t in range(0, 600, 60)}
        pb = {t: [t, 2, 2, 2, 2, 1] for t in range(300, 900, 60)}
        c = {"token": "MINT", "dex_id": "pumpfun", "pair_address": "CURVE"}
        with mock.patch.object(s, "token_pairs", return_value=[
                {"dexId": "pumpswap", "pairAddress": "SWAP", "liquidity": {"usd": 9e4}}]), \
             mock.patch.object(o, "_pool_bars", return_value=pb):
            m = o._stitch_graduation(c, cb, 0, 900)
        self.assertEqual([m[t][4] for t in sorted(m)], [1] * 5 + [2] * 10)

    def test_no_graduation_keeps_curve_bars(self):
        cb = {0: [0, 1, 1, 1, 1, 1]}
        with mock.patch.object(s, "token_pairs", return_value=[curve(LAUNCH_NATIVE)]):
            self.assertIs(o._stitch_graduation({"token": "M"}, cb, 0, 60), cb)


if __name__ == "__main__":
    unittest.main()
