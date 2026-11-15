import math
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import httpx

import tides

EVENTS = [
    {"t": "2026-11-15T00:00:00Z", "v": 0.2, "type": "L"},
    {"t": "2026-11-15T06:00:00Z", "v": 2.2, "type": "H"},
    {"t": "2026-11-15T12:00:00Z", "v": 0.4, "type": "L"},
]
STATIONS = [
    {"id": "8443970", "name": "Boston", "state": "MA", "lat": 42.35, "lon": -71.05, "tide_type": "Semidiurnal",
     "kind": "gauge", "datum": "MLLW", "source": "NOAA CO-OPS"},
    {"id": "9414290", "name": "San Francisco", "state": "CA", "lat": 37.81, "lon": -122.47, "tide_type": "Mixed",
     "kind": "gauge", "datum": "MLLW", "source": "NOAA CO-OPS"},
    {"id": "1890000", "name": "Wake Island", "state": None, "lat": 19.29, "lon": 166.62, "tide_type": None,
     "kind": "harmonic", "datum": "MLLW", "source": "NOAA CO-OPS"},
]


def utc(h, m=0):
    return datetime(2026, 11, 15, h, m, tzinfo=timezone.utc)


def response(body):
    return httpx.Response(200, json=body, request=httpx.Request("GET", "https://x"))


def hourly(fn, hours=48):
    start = utc(0)
    return [{"t": (start + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:00Z"), "v": round(fn(h), 3)} for h in range(hours)]


def model(name, lat, lon, series):
    station = {"id": tides._slug(name), "name": name, "state": "X", "lat": lat, "lon": lon, "tide_type": None,
               "kind": "model", "datum": "MSL", "source": "Open-Meteo"}
    return {station["id"]: {"station": station, "series": series, "events": tides.turns(series)}}


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


class TurnsTest(unittest.TestCase):
    def test_semidiurnal_curve_gives_alternating_refined_turns(self):
        # 12.42 h M2 tide, amplitude 1 m: highs at 0, 12.42, 24.84 h ...
        out = tides.turns(hourly(lambda h: math.cos(2 * math.pi * h / 12.42)))
        self.assertEqual([e["type"] for e in out], ["L", "H", "L", "H", "L", "H", "L"])
        first_high = next(e for e in out if e["type"] == "H")
        self.assertEqual(first_high["t"][:13], "2026-11-15T12")  # 12.42 h, not snapped to 12:00 or 13:00
        self.assertAlmostEqual(first_high["v"], 1.0, places=2)

    def test_small_wiggles_are_not_tides(self):
        wiggle = hourly(lambda h: 0.01 * (h % 2))
        self.assertEqual(tides.turns(wiggle), [])


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

    def test_open_meteo_error_body_is_upstream_error(self):
        with patch.object(tides.httpx, "get", return_value=response({"error": True, "reason": "rate limited"})):
            with self.assertRaisesRegex(httpx.HTTPError, "rate limited"):
                tides._get(tides.MARINE, {})


class OverviewTest(unittest.TestCase):
    def setUp(self):
        tides._overview.cache_clear()
        tides.tide_station.cache_clear()

    def test_failed_station_is_dropped_not_fatal(self):
        def series(station_id, *args):
            if station_id == "9414290":
                raise tides.NoaaError("down")
            return EVENTS

        with patch.object(tides, "tide_stations", return_value=STATIONS[:2]), \
             patch.object(tides, "_series", side_effect=series), \
             patch.object(tides, "_model_tides", return_value={}):
            out = tides.tide_overview()
        self.assertEqual([s["id"] for s in out["stations"]], ["8443970"])
        self.assertEqual(out["stations"][0]["datum"], "MLLW")
        self.assertIn("now", out["stations"][0])

    def test_model_points_fill_the_world_but_not_next_to_noaa(self):
        curve = hourly(lambda h: math.cos(2 * math.pi * h / 12.42))
        models = {**model("Boston Harbor", 42.36, -71.0, curve), **model("Saint-Malo", 48.68, -2.05, curve)}
        with patch.object(tides, "tide_stations", return_value=STATIONS[:1]), \
             patch.object(tides, "_series", return_value=EVENTS), \
             patch.object(tides, "_model_tides", return_value=models):
            out = tides.tide_overview()
        self.assertEqual([s["id"] for s in out["stations"]], ["8443970", "om-saint-malo"])
        self.assertEqual(out["stations"][1]["datum"], "MSL")

    def test_noaa_outage_still_shows_model_tides(self):
        curve = hourly(lambda h: math.cos(2 * math.pi * h / 12.42))
        with patch.object(tides, "tide_stations", side_effect=tides.NoaaError("down")), \
             patch.object(tides, "_model_tides", return_value=model("Saint-Malo", 48.68, -2.05, curve)):
            out = tides.tide_overview()
        self.assertEqual([s["kind"] for s in out["stations"]], ["model"])
        self.assertTrue(out["partial_errors"])

    def test_unknown_station_rejected(self):
        with patch.object(tides, "tide_stations", return_value=STATIONS):
            with self.assertRaises(ValueError):
                tides.tide_station("0000000")
        with self.assertRaises(ValueError):
            tides.tide_station("../etc")

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

    def test_harmonic_station_does_not_ask_for_a_gauge(self):
        products = []

        def series(station_id, product, *args):
            products.append(product)
            return EVENTS

        with patch.object(tides, "tide_stations", return_value=STATIONS), \
             patch.object(tides, "_series", side_effect=series):
            out = tides.tide_station("1890000")
        self.assertNotIn("water_level", products)
        self.assertIsNone(out["observed_error"])

    def test_model_station_detail_comes_from_the_model(self):
        start = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) - timedelta(hours=24)
        curve = [{"t": (start + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:00Z"),
                  "v": round(math.cos(2 * math.pi * h / 12.42), 3)} for h in range(96)]
        with patch.object(tides, "_model_tides", return_value=model("Saint-Malo", 48.68, -2.05, curve)):
            out = tides.tide_station("om-saint-malo")
        self.assertEqual(out["datum"], "MSL")
        self.assertEqual(out["observed"], [])
        self.assertTrue(out["predicted"] and out["events"])


if __name__ == "__main__":
    unittest.main()
