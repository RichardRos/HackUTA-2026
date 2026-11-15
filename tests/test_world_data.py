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


class LocalPlacesTest(unittest.TestCase):
    def test_most_specific_first_ending_with_country(self):
        self.assertEqual(world_data.local_places({"town": "Arles", "county": "Bouches", "state": "Provence", "country": "France"}),
                         ["Arles", "Bouches", "Provence", "France"])
        self.assertEqual(world_data.local_places({"city": "Singapore", "country": "Singapore"}), ["Singapore"])
        self.assertEqual(world_data.local_places({}), [])


class SovereignCurveTest(unittest.TestCase):
    def setUp(self):
        world_data._tv_yields.cache_clear()

    def test_parses_tenors_month_ago_and_euro_fallback(self):
        rows = [{"s": "TVC:DE10Y", "d": ["Germany 10 Year Government Bonds Yield", 3.3, 10.0]},
                {"s": "TVC:DE03MY", "d": ["Germany 3 Month Government Bonds Yield", 2.0, None]},
                {"s": "TVC:EU10Y", "d": ["Euro 10 Year Government Bonds Yield", 3.0, 0.0]}]
        resp = type("R", (), {"raise_for_status": lambda self: None, "json": lambda self: {"data": rows}})()
        with patch.object(world_data.httpx, "post", return_value=resp):
            de = world_data.sovereign_curve("Germany")
            ee = world_data.sovereign_curve("Estonia")
        self.assertEqual([b["tenor"] for b in de["bonds"]], ["3 Mo", "10 Yr"])
        self.assertEqual(de["bonds"][1]["month_ago"], 3.0)
        self.assertEqual(de["bonds"][1]["change_1m_bp"], 30.0)
        self.assertEqual(len(de["month_ago"]["curve"]), 1)  # no 1M change, no past point
        self.assertIn("euro-area", ee["source"])
        with self.assertRaises(ValueError):
            world_data.sovereign_curve("Chad")
