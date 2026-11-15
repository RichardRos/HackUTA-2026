import unittest
from unittest.mock import patch

import flights

PAYLOAD = {"ac": [
    {"hex": "a1", "flight": "FDX90   ", "r": "N123FE", "t": "B77L", "lat": 35.0, "lon": -90.0,
     "alt_baro": 31000, "gs": 480.2, "track": 90.1, "emergency": "none"},
    {"hex": "a2", "flight": "SWA3427 ", "t": "B38M", "lat": 32.9, "lon": -97.0, "alt_baro": "ground"},
    {"hex": "a3", "flight": "N12345", "lat": 33.0, "lon": -97.1},          # prefix 'N12' isn't alpha
    {"hex": "a4", "flight": "UPS2"},                                       # no position: dropped
]}


class FlightTests(unittest.TestCase):
    def setUp(self):
        flights._cache.clear()

    @patch("flights._get", return_value=PAYLOAD)
    def test_parse_and_cargo_filter(self, get):
        rows = flights.aircraft_near(35, -90, 100)
        get.assert_called_once_with("point/35/-90/100")
        self.assertEqual([r["hex"] for r in rows], ["a1", "a3", "a2"])
        fedex = rows[0]
        self.assertEqual((fedex["callsign"], fedex["cargo_operator"], fedex["emergency"]),
                         ("FDX90", "FedEx Express", False))
        ground = rows[2]
        self.assertTrue(ground["on_ground"])
        self.assertIsNone(ground["altitude_ft"])
        self.assertEqual([r["hex"] for r in flights.aircraft_near(35, -90, 100, cargo_only=True)], ["a1"])
        self.assertEqual(get.call_count, 1)  # second call served from cache

    def test_bounds(self):
        with self.assertRaises(ValueError):
            flights.aircraft_near(95, 0)
        with self.assertRaises(ValueError):
            flights.aircraft_near(0, 0, radius_nm=500)

    @patch("flights._get", side_effect=OSError("down"))
    def test_upstream_failure_is_runtime_error(self, _):
        with self.assertRaises(RuntimeError):
            flights.aircraft_near(1, 1)


STATES = {"time": 1763180000, "states": [
    ["abc123", "UPS2    ", "United States", 1, 1763179995, -85.7, 38.2, 10000.0, False, 230.0, 45.0, -5.0,
     None, 10100.0, "7700", False, 0],
    ["def456", "DAL1    ", "United States", 1, 1763179990, -84.4, 33.6, None, True, 0.0, 0.0, None,
     None, None, None, False, 0],
    ["nopos1", "AAL2", "United States", None, 1, None, None, None, False, None, None, None, None, None,
     None, False, 0],
]}


class GlobalTests(unittest.TestCase):
    def setUp(self):
        flights._global.update(at=0.0, rows=[], fetched_at=None, credits_left=None)

    def test_state_conversion(self):
        r = flights.normalize_state(STATES["states"][0])
        self.assertEqual((r["callsign"], r["cargo_operator"], r["altitude_ft"], r["emergency"]),
                         ("UPS2", "UPS Airlines", 32808, True))
        self.assertEqual(r["ground_speed_kt"], 447.1)
        self.assertEqual(r["vertical_rate_fpm"], -984)
        ground = flights.normalize_state(STATES["states"][1])
        self.assertTrue(ground["on_ground"])
        self.assertIsNone(ground["altitude_ft"])
        self.assertIsNone(flights.normalize_state(STATES["states"][2]))

    def test_cache_and_stale_fallback(self):
        calls = []

        def fake_refresh():
            calls.append(1)
            if len(calls) > 1:
                raise RuntimeError("OpenSky daily credits used up")
            flights._global.update(at=flights.time.monotonic(),
                                   rows=list(filter(None, map(flights.normalize_state, STATES["states"]))),
                                   fetched_at=STATES["time"], credits_left=396)

        with patch("flights._refresh_global", side_effect=fake_refresh):
            snap = flights.aircraft_global()
            self.assertEqual((snap["count"], snap["credits_left"]), (2, 396))
            self.assertEqual(flights.aircraft_global(cargo_only=True)["count"], 1)
            self.assertEqual(len(calls), 1)                      # second call hit the cache
            flights._global["at"] = 0.0                           # expire it; refresh now fails
            stale = flights.aircraft_global()
            self.assertEqual(stale["count"], 2)
            self.assertIn("credits", stale["stale_error"])
            flights._global.update(rows=[], at=0.0)
            with self.assertRaises(RuntimeError):                 # nothing cached to fall back on
                flights.aircraft_global()


if __name__ == "__main__":
    unittest.main()
