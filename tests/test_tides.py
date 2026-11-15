import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import httpx

import tides

EVENTS = [
    {"t": "2026-11-15T00:00:00Z", "v": 0.2, "type": "L"},
    {"t": "2026-11-15T06:00:00Z", "v": 2.2, "type": "H"},
    {"t": "2026-11-15T12:00:00Z", "v": 0.4, "type": "L"},
]
STATIONS = [
    {"id": "8443970", "name": "Boston", "state": "MA", "lat": 42.35, "lon": -71.05, "tide_type": "Semidiurnal"},
    {"id": "9414290", "name": "San Francisco", "state": "CA", "lat": 37.81, "lon": -122.47, "tide_type": "Mixed"},
]


def utc(h, m=0):
    return datetime(2026, 11, 15, h, m, tzinfo=timezone.utc)


def response(body):
    return httpx.Response(200, json=body, request=httpx.Request("GET", "https://x"))


class TideNowTest(unittest.TestCase):
    def test_midway_is_mean_level_at_peak_rate(self):
        now = tides.tide_now(EVENTS, utc(3))
        self.assertAlmostEqual(now["level_m"], 1.2)
        self.assertTrue(now["rising"])
        self.assertAlmostEqual(now["rate_m_per_h"], 2.0 * 3.14159 / 2 / 6, places=2)
        self.assertEqual(now["next"]["type"], "H")

    def test_slack_at_turn_and_falling_after(self):
        self.assertAlmostEqual(tides.tide_now(EVENTS, utc(6))["rate_m_per_h"], 0.0)
        later = tides.tide_now(EVENTS, utc(9))
        self.assertFalse(later["rising"])
        self.assertEqual(later["next"]["v"], 0.4)

    def test_outside_window_is_none(self):
        self.assertIsNone(tides.tide_now(EVENTS, utc(13)))


class SeriesTest(unittest.TestCase):
    def test_parses_times_heights_and_skips_blanks(self):
        body = {"predictions": [{"t": "2026-11-15 06:00", "v": "2.2", "type": "H"}, {"t": "2026-11-15 06:06", "v": ""}]}
        with patch.object(tides.httpx, "get", return_value=response(body)):
            out = tides._series("8443970", "predictions", utc(0), utc(12), "hilo")
        self.assertEqual(out, [{"t": "2026-11-15T06:00:00Z", "v": 2.2, "type": "H"}])

    def test_error_body_is_upstream_error(self):
        with patch.object(tides.httpx, "get", return_value=response({"error": {"message": "No data"}})):
            with self.assertRaises(httpx.HTTPError):
                tides._series("8443970", "water_level", utc(0), utc(12))


class OverviewTest(unittest.TestCase):
    def setUp(self):
        tides._overview.cache_clear()
        tides.tide_station.cache_clear()

    def test_failed_station_is_dropped_not_fatal(self):
        def series(station_id, *args):
            if station_id == "9414290":
                raise tides.NoaaError("down")
            return EVENTS

        with patch.object(tides, "tide_stations", return_value=STATIONS), \
             patch.object(tides, "_series", side_effect=series):
            out = tides.tide_overview()
        self.assertEqual([s["id"] for s in out["stations"]], ["8443970"])
        self.assertEqual(out["datum"], "MLLW")
        self.assertIn("now", out["stations"][0])

    def test_unknown_station_rejected(self):
        with patch.object(tides, "tide_stations", return_value=STATIONS):
            with self.assertRaises(ValueError):
                tides.tide_station("0000000")

    def test_offline_gauge_keeps_predictions(self):
        def series(station_id, product, *args):
            if product == "water_level":
                raise tides.NoaaError("gauge offline")
            return EVENTS

        with patch.object(tides, "tide_stations", return_value=STATIONS), \
             patch.object(tides, "_series", side_effect=series):
            out = tides.tide_station("8443970")
        self.assertEqual(out["observed"], [])
        self.assertEqual(out["observed_error"], "gauge offline")
        self.assertEqual(out["predicted"], EVENTS)


if __name__ == "__main__":
    unittest.main()
