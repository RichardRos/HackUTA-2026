import unittest
from unittest.mock import patch

import world_data


def closes(*values):
    return [{"date": f"2026-11-{d:02d}", "close": v} for d, v in enumerate(values, 1)]


class CommodityFuturesTest(unittest.TestCase):
    def setUp(self):
        world_data.commodity_futures.cache_clear()

    def test_same_unit_pair_gets_spread(self):
        fake = {"BZ=F": ("USD", closes(100, 95)), "CL=F": ("USD", closes(90, 88))}
        with patch.object(world_data, "_yahoo_closes", side_effect=lambda s, d: fake[s]):
            out = world_data.commodity_futures("oil")
        self.assertEqual(out["spread"], {"name": "Brent - WTI", "value": 7.0, "unit": "USD"})
        self.assertEqual([c["settlement"] for c in out["contracts"]], ["cash", "physical"])
        self.assertEqual(out["contracts"][0]["change_pct"], -5.0)

    def test_mixed_units_get_no_spread(self):
        fake = {"TTF=F": ("EUR", closes(30, 31)), "NG=F": ("USD", closes(3, 3.2))}
        with patch.object(world_data, "_yahoo_closes", side_effect=lambda s, d: fake[s]):
            self.assertIsNone(world_data.commodity_futures("natural-gas")["spread"])

    def test_unknown_commodity_rejected(self):
        with self.assertRaises(ValueError):
            world_data.commodity_futures("lumber")


class CommodityEtfTest(unittest.TestCase):
    def setUp(self):
        world_data.commodity_etf_trend.cache_clear()

    def test_livestock_maps_to_a_real_alpaca_symbol(self):
        with patch.object(world_data, "_alpaca_funds", side_effect=lambda funds, days: list(funds)):
            livestock = world_data.commodity_etf_trend("livestock")
            headline = world_data.commodity_etf_trend(None)
        self.assertEqual(livestock["funds"], ["COW"])
        self.assertIn("COW", headline["funds"])
        self.assertIn("DBC", headline["funds"])


if __name__ == "__main__":
    unittest.main()
