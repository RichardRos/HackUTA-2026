import json
import unittest
import urllib.parse
from datetime import datetime, timezone
from unittest import mock

import open_feeds

CORE_KEYS = {
    "source", "dataset", "series_id", "observed_at", "available_at",
    "value", "unit", "dimensions", "raw_data", "fetched_at",
}


def _query(url):
    return urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)


class NwsTests(unittest.TestCase):
    POINTS = json.dumps({"properties": {
        "gridId": "OUN", "gridX": 124, "gridY": 116,
        "forecastHourly": "https://api.weather.gov/gridpoints/OUN/124,116/forecast/hourly",
        "relativeLocation": {"properties": {"city": "Cushing"}},
    }})
    HOURLY = json.dumps({"properties": {
        "updateTime": "2026-11-14T18:27:02+00:00",
        "periods": [
            {
                "startTime": "2026-11-14T19:00:00-06:00",
                "temperature": 9,
                "probabilityOfPrecipitation": {"value": 22},
                "relativeHumidity": {"value": 52},
                "dewpoint": {"value": 0},
                "windSpeed": "9 km/h",
            },
            {
                "startTime": "2026-11-14T20:00:00-06:00",
                "temperature": 11,
                "probabilityOfPrecipitation": {"value": None},
                "relativeHumidity": {"value": 60},
                "dewpoint": None,
                "windSpeed": None,
            },
        ],
    }})

    def test_null_metric_skipped_but_period_kept(self):
        def fake(url, headers=None, attempts=1):
            return self.POINTS if "/points/" in url else self.HOURLY

        with mock.patch.object(open_feeds, "NWS_ANCHORS", [("cushing_ok", 35.9, -96.7)]):
            with mock.patch.object(open_feeds, "_http_text", side_effect=fake):
                rows = open_feeds.nws_rows()
        self.assertEqual(len(rows), 7)
        first = datetime(2026, 11, 15, 1, tzinfo=timezone.utc)
        second = datetime(2026, 11, 15, 2, tzinfo=timezone.utc)
        by_key = {(r["series_id"], r["observed_at"]): r for r in rows}
        self.assertEqual(by_key[("cushing_ok.temperature", first)]["value"], 9.0)
        self.assertEqual(by_key[("cushing_ok.temperature", second)]["value"], 11.0)
        self.assertEqual(by_key[("cushing_ok.wind_speed", first)]["unit"], "km/h")
        self.assertNotIn(("cushing_ok.wind_speed", second), by_key)
        self.assertNotIn(("cushing_ok.dewpoint", second), by_key)
        self.assertNotIn(("cushing_ok.probability_of_precipitation", second), by_key)
        self.assertIn(("cushing_ok.relative_humidity", second), by_key)
        sample = by_key[("cushing_ok.temperature", first)]
        self.assertEqual(sample["source"], "noaa_nws")
        self.assertEqual(sample["dataset"], "nws_hourly_forecast")
        self.assertEqual(sample["unit"], "celsius")
        self.assertEqual(sample["available_at"],
                         datetime(2026, 11, 14, 18, 27, 2, tzinfo=timezone.utc))
        self.assertEqual(sample["dimensions"]["anchor"], "cushing_ok")
        self.assertEqual(sample["dimensions"]["wfo"], "OUN")
        self.assertEqual(set(sample), CORE_KEYS)

    def test_wind_string_number(self):
        self.assertEqual(open_feeds._first_number("5 to 10 km/h"), "5")
        self.assertEqual(open_feeds._first_number("12 km/h"), "12")


class FredTests(unittest.TestCase):
    def test_skips_dots_and_malformed_values(self):
        def fake(url, headers=None, attempts=1):
            series_id = _query(url)["id"][0]
            return (
                f"observation_date,{series_id}\n"
                "2026-01-02,3.5\n"
                "2026-01-03,.\n"
                "2026-01-04,oops\n"
                "not-a-date,4.2\n"
            )

        with mock.patch.object(open_feeds, "_http_text", side_effect=fake):
            rows = open_feeds.fred_rows()
        self.assertEqual(len(rows), len(open_feeds.FRED_SERIES))
        self.assertTrue(all(r["value"] == 3.5 for r in rows))
        self.assertTrue(all(
            r["observed_at"] == datetime(2026, 1, 2, tzinfo=timezone.utc) for r in rows
        ))
        self.assertTrue(all(r["source"] == "fred" and r["dataset"] == "commodity_price"
                            for r in rows))
        self.assertEqual({r["series_id"] for r in rows},
                         {sid for sid, _ in open_feeds.FRED_SERIES})
        self.assertEqual(rows[0]["raw_data"]["observation_date"], "2026-01-02")
        self.assertEqual(set(rows[0]), CORE_KEYS)


class CftcTests(unittest.TestCase):
    def test_url_encodes_hash_and_filter(self):
        url = open_feeds.cftc_url()
        self.assertNotIn("#", url)
        self.assertIn("%23", url)
        params = _query(url)
        self.assertIn("COPPER- #1", params["$where"][0])
        self.assertIn("CRUDE OIL, LIGHT SWEET-WTI", params["$where"][0])
        self.assertIn("WHEAT-SRW", params["$where"][0])
        self.assertIn("WHEAT-HRW", params["$where"][0])
        self.assertEqual(params["$limit"], ["500"])
        self.assertIn("report_date_as_yyyy_mm_dd DESC", params["$order"][0])

    def test_parses_selected_contracts_and_skips_nulls(self):
        records = [
            {
                "report_date_as_yyyy_mm_dd": "2026-11-03T00:00:00.000",
                "contract_market_name": "CRUDE OIL, LIGHT SWEET-WTI",
                "open_interest_all": "769009",
                "m_money_positions_long_all": "13415",
                "m_money_positions_short_all": "25233",
            },
            {
                "report_date_as_yyyy_mm_dd": "2026-11-03T00:00:00.000",
                "contract_market_name": "HENRY HUB",
                "open_interest_all": "1234",
                "m_money_positions_long_all": None,
                "m_money_positions_short_all": "55",
            },
        ]
        with mock.patch.object(open_feeds, "_http_text", return_value=json.dumps(records)):
            rows = open_feeds.cftc_rows()
        self.assertEqual(len(rows), 5)
        self.assertTrue(all(r["source"] == "cftc" and r["dataset"] == "cot_disaggregated"
                            for r in rows))
        self.assertTrue(all(r["unit"] == "contracts" for r in rows))
        self.assertTrue(all(r["observed_at"] == datetime(2026, 11, 3, tzinfo=timezone.utc)
                            for r in rows))
        self.assertNotIn("HENRY HUB.m_money_positions_long_all",
                         {r["series_id"] for r in rows})
        self.assertEqual(
            {r["series_id"] for r in rows},
            {
                "CRUDE OIL, LIGHT SWEET-WTI.open_interest_all",
                "CRUDE OIL, LIGHT SWEET-WTI.m_money_positions_long_all",
                "CRUDE OIL, LIGHT SWEET-WTI.m_money_positions_short_all",
                "HENRY HUB.open_interest_all",
                "HENRY HUB.m_money_positions_short_all",
            },
        )
        self.assertEqual(rows[0]["dimensions"]["contract"], "CRUDE OIL, LIGHT SWEET-WTI")


class UpsertTests(unittest.TestCase):
    def test_available_at_advances_only_when_value_changes(self):
        sql = " ".join(open_feeds.OBS_UPSERT.split())
        self.assertIn("ON CONFLICT (source, dataset, series_id, observed_at) DO UPDATE", sql)
        self.assertIn(
            "WHEN external_observations.value IS DISTINCT FROM EXCLUDED.value "
            "THEN EXCLUDED.available_at",
            sql,
        )
        self.assertIn(
            "ELSE LEAST(external_observations.available_at, EXCLUDED.available_at) END",
            sql,
        )


class CoopsTests(unittest.TestCase):
    def test_water_and_wind_fields(self):
        def fake(url, headers=None, attempts=1):
            params = _query(url)
            self.assertEqual(params["time_zone"], ["gmt"])
            self.assertEqual(params["format"], ["json"])
            if params["product"][0] == "water_level":
                return json.dumps({
                    "metadata": {"id": "9414290", "name": "San Francisco"},
                    "data": [{"t": "2026-11-15 01:24", "v": "3.751", "s": "0.364"}],
                })
            return json.dumps({
                "metadata": {"id": "9414290", "name": "San Francisco"},
                "data": [{"t": "2026-11-15 01:18", "s": "3.11", "d": "204.0", "g": "5.44"}],
            })

        with mock.patch.object(open_feeds, "COOPS_STATIONS", [("9414290", "San Francisco")]):
            with mock.patch.object(open_feeds, "_http_text", side_effect=fake):
                rows = open_feeds.coops_rows()
        by_id = {r["series_id"]: r for r in rows}
        self.assertEqual(len(rows), 4)
        self.assertEqual(by_id["9414290.v"]["value"], 3.751)
        self.assertEqual(by_id["9414290.v"]["unit"], "feet")
        self.assertEqual(by_id["9414290.s"]["unit"], "knots")
        self.assertEqual(by_id["9414290.g"]["value"], 5.44)
        self.assertEqual(by_id["9414290.d"]["value"], 204.0)
        self.assertEqual(by_id["9414290.d"]["unit"], "degrees")
        self.assertEqual(by_id["9414290.v"]["observed_at"],
                         datetime(2026, 11, 15, 1, 24, tzinfo=timezone.utc))
        self.assertEqual(by_id["9414290.v"]["dimensions"],
                         {"station": "9414290", "name": "San Francisco"})
        self.assertEqual(by_id["9414290.v"]["dataset"], "9414290.water_level")

    def test_error_payload_is_skipped(self):
        def fake(url, headers=None, attempts=1):
            if _query(url)["product"][0] == "water_level":
                return json.dumps({"metadata": {"name": "Sewells Point"},
                                   "data": [{"t": "2026-11-15 01:24", "v": "2.5"}]})
            return json.dumps({"error": {"message": "No data was found."}})

        with mock.patch.object(open_feeds, "COOPS_STATIONS", [("8638610", "Sewells Point")]):
            with mock.patch.object(open_feeds, "_http_text", side_effect=fake):
                rows = open_feeds.coops_rows()
        self.assertEqual([r["series_id"] for r in rows], ["8638610.v"])


class SseTests(unittest.TestCase):
    SCFI = json.dumps({"data": {
        "currentDate": "2026-11-13", "lastDate": "2026-11-06",
        "lineDataList": [
            {
                "properties": {"lineName_EN": "Comprehensive Index"},
                "currentContent": 3572.32, "lastContent": 3633.44,
                "dataItemTypeName": "SCFI_T",
            },
            {
                "properties": {"lineName_EN": "Europe 20ft"},
                "currentContent": None, "lastContent": None,
                "dataItemTypeName": "SCFI_L1",
            },
        ],
    }})
    CCFI = json.dumps({"data": {
        "currentDate": "2026-11-13", "lastDate": "2026-11-06",
        "lineDataList": [{
            "properties": {"lineName_EN": "COMPOSITE INDEX"},
            "currentContent": 1919.27, "lastContent": 1924.69,
            "dataItemTypeName": "CCFI_T",
        }],
    }})

    def test_current_and_previous_dates(self):
        def fake(url, headers=None, attempts=1):
            return self.SCFI if _query(url)["indexName"][0] == "scfi" else self.CCFI

        with mock.patch.object(open_feeds, "_http_text", side_effect=fake):
            rows = open_feeds.sse_rows()
        self.assertEqual(len(rows), 4)
        keys = {(r["dataset"], r["series_id"], r["observed_at"]) for r in rows}
        self.assertEqual(len(keys), len(rows))
        self.assertTrue(all(r["dataset"] == "container_freight_index" for r in rows))
        self.assertTrue(all(r["source"] == "sse" for r in rows))
        self.assertTrue(all(r["unit"] is None for r in rows))
        self.assertTrue(all(set(r) == CORE_KEYS for r in rows))
        current = [r for r in rows if r["dimensions"]["data_item_type"] == "current"]
        previous = [r for r in rows if r["dimensions"]["data_item_type"] == "previous"]
        self.assertEqual(len(current), 2)
        self.assertEqual(len(previous), 2)
        self.assertTrue(all(r["observed_at"] == datetime(2026, 11, 13, tzinfo=timezone.utc)
                            for r in current))
        self.assertTrue(all(r["observed_at"] == datetime(2026, 11, 6, tzinfo=timezone.utc)
                            for r in previous))
        europe = [r for r in rows if r["series_id"] == "scfi.Europe 20ft"]
        self.assertEqual(europe, [])


if __name__ == "__main__":
    unittest.main()
