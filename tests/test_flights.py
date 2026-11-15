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


if __name__ == "__main__":
    unittest.main()
