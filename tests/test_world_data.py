import unittest
from unittest.mock import patch

import world_data


def closes(*values):
    return [{"date": f"2026-11-{d:02d}", "close": v} for d, v in enumerate(values, 1)]


class CommodityFuturesTest(unittest.TestCase):
    def setUp(self):
        world_data.commodity_futures.cache_clear()

    def test_brent_wti_spread_with_physical_units(self):
        fake = {"BZ=F": ("USD", closes(100, 95)), "CL=F": ("USD", closes(90, 88)), "DBL=F": (None, closes(94, 93)),
                "HO=F": ("USD", closes(4, 4)), "RB=F": ("USD", closes(2.5, 2.6))}
        with patch.object(world_data, "_yahoo_closes", side_effect=lambda s, d: fake[s]):
            out = world_data.commodity_futures("oil")
        self.assertEqual(out["spread"], {"name": "Brent crude - WTI crude", "value": 7.0, "unit": "USD/bbl"})
        by = {c["symbol"]: c for c in out["contracts"]}
        self.assertEqual(by["DBL=F"]["unit"], "USD/bbl")  # Yahoo omits Dubai's currency; NYMEX lists it in USD
        self.assertEqual(by["HO=F"]["unit"], "USD/gal")
        self.assertEqual(by["BZ=F"]["change_pct"], -5.0)

    def test_no_spread_across_gas_hubs(self):
        fake = {"TTF=F": ("EUR", closes(30, 31)), "NG=F": ("USD", closes(3, 3.2)), "JKM=F": (None, closes(11, 12))}
        with patch.object(world_data, "_yahoo_closes", side_effect=lambda s, d: fake[s]):
            out = world_data.commodity_futures("natural-gas")
        self.assertIsNone(out["spread"])
        self.assertEqual({c["unit"] for c in out["contracts"]}, {"EUR/MWh", "USD/MMBtu"})

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


class StoredTest(unittest.TestCase):
    def test_fresh_stale_retry_and_max_age(self):
        calls, fail = [], []

        @world_data.stored(ttl=100, max_age=1000, mark_stale=True)
        def grid():
            calls.append(1)
            if fail:
                raise RuntimeError("upstream down")  # any exception, not only httpx
            return {"v": len(calls)}

        clock = [0.0]
        with patch.object(world_data.time, "time", lambda: clock[0]):
            self.assertEqual(grid(), {"v": 1})
            clock[0] = 50
            self.assertEqual(grid(), {"v": 1})                  # fresh: no call
            self.assertEqual(len(calls), 1)
            fail.append(1)
            clock[0] = 150
            self.assertEqual(grid(), {"v": 1, "stale": True})   # refresh failed: last good, marked
            clock[0] = 300
            grid()
            self.assertEqual(len(calls), 2)                     # within RETRY_AFTER: upstream not called again
            clock[0] = 150 + world_data.RETRY_AFTER + 1
            grid()
            self.assertEqual(len(calls), 3)                     # retry window over: tries again
            clock[0] = 2000
            with self.assertRaises(RuntimeError):               # older than max_age: no stale fallback
                grid()

    def test_keyed_dicts_and_lists_are_never_marked(self):
        ok = []

        @world_data.stored(ttl=1, max_age=1000)
        def stations():
            if ok:
                raise RuntimeError("down")
            ok.append(1)
            return {"8443970": {"station": "Boston"}}

        clock = [0.0]
        with patch.object(world_data.time, "time", lambda: clock[0]):
            stations()
            clock[0] = 5
            self.assertEqual(stations(), {"8443970": {"station": "Boston"}})  # no "stale" key polluting the mapping
