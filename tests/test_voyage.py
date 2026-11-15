import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
from fastapi.testclient import TestClient
from fastmcp.server.auth.auth import AccessToken

from voyage import conditions, router, storage
from voyage.grid import ocean
from voyage.model import VESSELS, features, fit_speed_model, kwon_factor, synthetic, trireme_factor
from voyage.ports import APPROACH, ODYSSEY_STOPS, PORTS_BY_ID


class PhysicsModel:
    """Stands in for a trained SpeedModel: the physics prior itself, so tests are fast and exact."""
    q90 = 0.05
    meta = {"note": "test"}

    def __init__(self, vessel):
        self.vessel = vessel

    def factor(self, X):
        return kwon_factor(self.vessel, X)


def fake_field(storm=None, hours=240):
    """A conditions.field stand-in: calm seas, plus waves of `hs` m inside a lat/lon box between two
    hours after departure. storm = (lat_min, lat_max, lon_min, lon_max, hour_from, hour_to, hs)."""

    def field(points, t0=None):
        pts = np.array(points, dtype=float)
        P = len(pts)
        hs = np.full((P, hours), 0.5)
        if storm:
            la0, la1, lo0, lo1, h0, h1, h = storm
            inside = (pts[:, 0] >= la0) & (pts[:, 0] <= la1) & (pts[:, 1] >= lo0) & (pts[:, 1] <= lo1)
            hs[np.ix_(inside, np.arange(h0, h1))] = h
        wind = hs * 3.0
        zeros = np.zeros((P, hours))
        return conditions.Field(t0=t0 or datetime.now(timezone.utc), points=pts, hs=hs, tp=np.full((P, hours), 8.0),
                                wave_from=zeros, wind=wind, wind_from=zeros, cur_u=zeros, cur_v=zeros,
                                is_sea=np.ones(P, dtype=bool), horizon_h=hours - 1)
    return field


class GridTest(unittest.TestCase):
    def test_ports_and_odyssey_reach_the_open_sea(self):
        g = ocean()
        main = g.component_size.argmax()
        for place in list(PORTS_BY_ID.values()) + ODYSSEY_STOPS:
            node = g.snap(place["lat"], place["lon"])
            self.assertIsNotNone(node, place["name"])
            self.assertEqual(g.component[node], main, place["name"])

    def test_hamburg_routes_from_the_north_sea_not_the_baltic(self):
        g = ocean()
        lat, lon = g.latlon(g.snap(*APPROACH[(53.54, 9.97)]))
        self.assertLess(lon, 9.5)  # west of Schleswig-Holstein: the Elbe side, reached via Kiel or Skagen

    def test_suez_is_a_link_and_land_is_not_crossed(self):
        g = ocean()
        names = {name for links in g.links.values() for *_, name in links}
        self.assertTrue({"Suez Canal", "Panama Canal", "Strait of Messina", "Dardanelles"} <= names)
        path = router._astar_distance(g, g.snap(37.94, 23.62), g.snap(39.44, -0.31))  # Piraeus -> Valencia
        self.assertTrue(all(g.water.ravel()[n] for n in path))


class ModelTest(unittest.TestCase):
    def test_kwon_head_seas_slow_more_than_following(self):
        v = VESSELS["container"]
        head = features(v, 0.0, 5.0, 10.0, 0.0, 15.0, 0.0, 0.0, 0.0)  # waves from dead ahead
        astern = features(v, 0.0, 5.0, 10.0, 180.0, 15.0, 180.0, 0.0, 0.0)
        calm = features(v, 0.0, 0.2, 5.0, 0.0, 2.0, 0.0, 0.0, 0.0)
        self.assertLess(kwon_factor(v, head)[0], kwon_factor(v, astern)[0])
        self.assertGreater(kwon_factor(v, calm)[0], 0.97)

    def test_current_along_track_adds_speed(self):
        v = VESSELS["bulk"]
        fair = features(v, 0.0, 1.0, 7.0, 0.0, 5.0, 0.0, 0.0, 1.0)  # 1 m/s current toward north, heading north
        foul = features(v, 0.0, 1.0, 7.0, 0.0, 5.0, 0.0, 0.0, -1.0)
        self.assertGreater(kwon_factor(v, fair)[0] - kwon_factor(v, foul)[0], 0.25)

    def test_trireme_sails_downwind_and_suffers_in_a_seaway(self):
        v = VESSELS["trireme"]
        downwind = features(v, 0.0, 0.5, 5.0, 180.0, 10.0, 180.0, 0.0, 0.0)
        upwind = features(v, 0.0, 0.5, 5.0, 0.0, 10.0, 0.0, 0.0, 0.0)
        rough = features(v, 0.0, 2.5, 5.0, 180.0, 10.0, 180.0, 0.0, 0.0)
        self.assertGreater(trireme_factor(v, downwind)[0], trireme_factor(v, upwind)[0])
        self.assertLess(trireme_factor(v, rough)[0], trireme_factor(v, downwind)[0] * 0.5)

    def test_superlearner_beats_linear_and_calibrates(self):
        X, y, g = synthetic(rows_per_vessel=600)
        model = fit_speed_model(X, y, g, small=True, n_jobs=1)
        cv = model.meta["cv"]
        self.assertGreater(cv["superlearner"]["r2"], cv["linear"]["r2"])
        self.assertGreater(cv["superlearner"]["r2"], 0.8)
        self.assertTrue(0 < model.q90 < 0.2)
        self.assertEqual(set(model.meta["weights"]), {"linear", "ridge", "lasso", "rf", "xgb"})


class RouterTest(unittest.TestCase):
    """Piraeus -> Valencia by container ship (~65 h), with a fake forecast."""

    def plan(self, storm=None):
        router._cache.clear()
        vessel = VESSELS["container"]
        with patch.object(conditions, "field", fake_field(storm)):
            return router.plan(mode="ports", vessel_id="container", start_port="grpir", end_port="esvlc",
                               model=PhysicsModel(vessel))

    def test_calm_sea_route_is_the_shortest(self):
        r = self.plan()
        self.assertAlmostEqual(r["legs"][0]["distance_nm"], r["legs"][0]["shortest"]["distance_nm"], delta=40)
        self.assertLess(abs(r["saved_h"]), 1)
        self.assertLess(r["eta_band"]["early"], r["arrive"])
        self.assertGreater(r["eta_band"]["late"], r["arrive"])

    def test_storm_where_the_ship_will_be_is_avoided(self):
        calm = self.plan()
        mid = calm["legs"][0]["path"][len(calm["legs"][0]["path"]) // 2]
        box = (mid["lat"] - 3, mid["lat"] + 3, mid["lon"] - 3, mid["lon"] + 3, 0, 240, 9.0)
        stormy = self.plan(box)
        lats = [p["lat"] for p in stormy["legs"][0]["path"]]
        lons = [p["lon"] for p in stormy["legs"][0]["path"]]
        through = [abs(la - mid["lat"]) < 1 and abs(lo - mid["lon"]) < 1 for la, lo in zip(lats, lons)]
        self.assertFalse(any(through), "route should leave the storm box")
        self.assertGreater(stormy["legs"][0]["shortest"]["duration_h"], stormy["duration_h"])

    def test_storm_that_clears_before_arrival_is_ignored(self):
        """Conditions at n+1: waves there at departure but gone by the time the ship arrives cost nothing."""
        calm = self.plan()
        mid = calm["legs"][0]["path"][len(calm["legs"][0]["path"]) // 2]
        box = (mid["lat"] - 3, mid["lat"] + 3, mid["lon"] - 3, mid["lon"] + 3, 0, 6, 9.0)  # only the first 6 h
        r = self.plan(box)
        self.assertAlmostEqual(r["duration_h"], calm["duration_h"], delta=0.5)
        self.assertLess(r["max_hs_m"], 2)

    def test_rate_limited_forecast_plans_on_a_calm_sea_and_is_not_cached(self):
        router._cache.clear()
        limited = MagicMock(side_effect=conditions.RateLimited("429"))
        with patch.object(conditions, "field", limited):
            r = router.plan(mode="ports", vessel_id="container", start_port="grpir", end_port="esvlc",
                            model=PhysicsModel(VESSELS["container"]))
        self.assertTrue(r["conditions_source"].startswith("Forecast unavailable"))
        self.assertFalse(r["legs"][0]["forecast"])
        self.assertGreater(r["distance_nm"], 1000)
        self.assertEqual(router._cache, {})  # retried with a real forecast next time

    def test_odyssey_visits_every_stop_in_order(self):
        router._cache.clear()
        with patch.object(conditions, "field", fake_field(hours=48)):
            r = router.plan(mode="odyssey", vessel_id="trireme", model=PhysicsModel(VESSELS["trireme"]), dwell_h=12)
        self.assertEqual([leg["to"]["id"] for leg in r["legs"]], [s["id"] for s in ODYSSEY_STOPS[1:]])
        self.assertIn("Strait of Messina", {p for leg in r["legs"] for p in leg["passages"]})
        departs = [datetime.fromisoformat(leg["depart"]) for leg in r["legs"]]
        arrives = [datetime.fromisoformat(leg["arrive"]) for leg in r["legs"]]
        self.assertTrue(all(d >= a + timedelta(hours=12) - timedelta(seconds=1) for a, d in zip(arrives, departs[1:])))
        band = datetime.fromisoformat(r["eta_band"]["late"]) - datetime.fromisoformat(r["eta_band"]["early"])
        last = datetime.fromisoformat(r["legs"][-1]["eta_band"]["late"]) - datetime.fromisoformat(r["legs"][-1]["eta_band"]["early"])
        self.assertGreater(band, last * 3)  # uncertainty accumulates over the whole voyage

    def test_bad_requests(self):
        with self.assertRaises(ValueError):
            router.plan(mode="ports", vessel_id="container", start_port="xxxxx", end_port="esvlc", model=PhysicsModel(None))
        with self.assertRaises(ValueError):
            router.plan(mode="points", vessel_id="container", start=(39.0, -98.0), end=(40.0, -70.0),
                        model=PhysicsModel(None))  # Kansas is not near the sea
        with self.assertRaises(ValueError):
            router.plan(mode="points", vessel_id="submarine", start=(0, 0), end=(1, 1))
        with self.assertRaisesRegex(ValueError, "same place"):
            router.plan(mode="ports", vessel_id="container", start_port="sgsin", end_port="sgsin",
                        model=PhysicsModel(VESSELS["container"]))


class CanalTest(unittest.TestCase):
    """Canals take a fixed transit time, not sea speed, and their sheltered water is never 'above the limit'."""

    def plan(self, start, end, vessel="container", storm=None):
        router._cache.clear()
        with patch.object(conditions, "field", fake_field(storm)):
            return router.plan(mode="ports", vessel_id=vessel, start_port=start, end_port=end,
                               model=PhysicsModel(VESSELS[vessel]))

    def test_panama_takes_its_transit_time_whatever_the_speed(self):
        from voyage.grid import TRANSIT_HOURS

        ship = self.plan("pabal", "paonx")
        ferry = self.plan("pabal", "paonx", vessel="ferry")  # 22 kn: faster at sea, same canal
        for r in (ship, ferry):
            leg = r["legs"][0]
            self.assertEqual(leg["passage_hours"], {"Panama Canal": TRANSIT_HOURS["Panama Canal"]})
            self.assertGreaterEqual(r["duration_h"], TRANSIT_HOURS["Panama Canal"])
            self.assertLess(r["duration_h"], TRANSIT_HOURS["Panama Canal"] + 4)  # plus the short approaches
        self.assertAlmostEqual(ship["duration_h"], ferry["duration_h"], delta=1.5)

    def test_storm_over_the_canal_is_not_hazard_time(self):
        r = self.plan("pabal", "paonx", storm=(8, 10, -81, -79, 0, 240, 9.0))
        canal = [p for p in r["legs"][0]["path"] if p.get("passage") == "Panama Canal"]
        self.assertTrue(canal)
        self.assertLess(r["hours_above_limit"], r["duration_h"] - 8)  # the 9 h transit is excluded

    def test_suez_in_a_red_sea_run(self):
        r = self.plan("egpsd", "sajed")
        self.assertIn("Suez Canal", r["legs"][0]["passage_hours"])
        sea_h = r["distance_nm"] / VESSELS["container"].service_kn
        self.assertGreater(r["duration_h"], sea_h)  # the canal costs more than sailing its length


class StorageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = patch.dict(os.environ, {"VOYAGE_MODEL_DIR": self.tmp.name, "VULTR_OBJECT_STORAGE_ENDPOINT": "",
                                      "VULTR_OBJECT_STORAGE_BUCKET": ""})
        env.start()
        self.addCleanup(env.stop)
        storage._state.update(model=None, key=None, checked=0.0)
        X, y, g = synthetic(rows_per_vessel=300)
        self.model = fit_speed_model(X, y, g, small=True, n_jobs=1)

    def test_local_round_trip(self):
        key = storage.save(self.model)
        loaded = storage.current()
        self.assertEqual(storage._state["key"], key)
        X, _, _ = synthetic(rows_per_vessel=5)
        np.testing.assert_allclose(loaded.factor(X), self.model.factor(X))

    def test_uploads_to_vultr_object_storage(self):
        client = MagicMock()
        with patch.dict(os.environ, {"VULTR_OBJECT_STORAGE_ENDPOINT": "ewr1.vultrobjects.com",
                                     "VULTR_OBJECT_STORAGE_BUCKET": "nostos-models"}), \
             patch("boto3.client", return_value=client) as make:
            key = storage.save(self.model)
        self.assertEqual(make.call_args.kwargs["endpoint_url"], "https://ewr1.vultrobjects.com")
        keys = [c.kwargs["Key"] for c in client.put_object.call_args_list]
        self.assertEqual(keys, [key, "voyage/models/latest.json"])

    def test_bootstrap_never_uploads(self):
        client = MagicMock()
        client.get_object.side_effect = Exception("empty bucket")
        with patch.dict(os.environ, {"VULTR_OBJECT_STORAGE_ENDPOINT": "ewr1.vultrobjects.com",
                                     "VULTR_OBJECT_STORAGE_BUCKET": "nostos-models"}), \
             patch("boto3.client", return_value=client), \
             patch.object(storage, "synthetic", return_value=synthetic(rows_per_vessel=200)):
            storage.current()
        client.put_object.assert_not_called()


class VoyageApiTest(unittest.TestCase):
    def setUp(self):
        from api import main as api

        self.api = api
        self.client = TestClient(api.app)
        verifier = AsyncMock()
        verifier.verify_token.return_value = AccessToken(token="t", client_id="c", scopes=[],
                                                         claims={"sub": "u1", "azp": "http://localhost:5173"})
        patcher = patch.object(api, "_verifier", verifier)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.headers = {"Authorization": "Bearer good"}

    def test_route_validates_and_maps_errors(self):
        bad = self.client.post("/api/voyage/route", json={"mode": "teleport"}, headers=self.headers)
        self.assertEqual(bad.status_code, 422)
        with patch.object(router, "plan", side_effect=ValueError("no sea route connects these two points")):
            r = self.client.post("/api/voyage/route", json={"mode": "points", "start": {"lat": 1, "lon": 2},
                                                             "end": {"lat": 3, "lon": 4}}, headers=self.headers)
        self.assertEqual(r.status_code, 400)
        with patch.object(router, "plan", side_effect=conditions.RateLimited("try again in a minute")):
            r = self.client.post("/api/voyage/route", json={"mode": "odyssey"}, headers=self.headers)
        self.assertEqual(r.status_code, 502)

    def test_options_lists_ports_vessels_and_stops(self):
        r = self.client.get("/api/voyage/options", headers=self.headers).json()
        self.assertIn("nlrtm", {p["id"] for p in r["ports"]})
        self.assertIn("trireme", {v["id"] for v in r["vessels"]})
        self.assertEqual(r["odyssey"][-1]["id"], "ithaca")


if __name__ == "__main__":
    unittest.main()


class VultrJobTest(unittest.TestCase):
    def test_user_data_sends_only_training_env_and_always_powers_off(self):
        import base64
        import re

        from voyage import vultr

        script = vultr.user_data({"VULTR_API_KEY": "account-key", "TIMESCALE_SERVICE_URL": "postgres://x",
                                  "UNRELATED": "y"}, "nostos-train-t", ["--no-ais"])
        sent = base64.b64decode(re.search(r"echo (\S+) \| base64 -d > .env", script).group(1)).decode()
        self.assertEqual(sent, "TIMESCALE_SERVICE_URL=postgres://x\n")
        self.assertNotIn("account-key", script)
        self.assertIn("trap 'poweroff' EXIT", script)
        self.assertIn("python -m voyage.train --no-ais", script)
        self.assertIn("voyage/logs/nostos-train-t.log", script)


class ParallelForecastTest(unittest.TestCase):
    def test_chunks_fetched_in_parallel_stay_matched_to_their_points(self):
        import time as _time

        points = [(float(i % 80), float(i)) for i in range(200)]  # 3 chunks

        def fake(url, chunk, fields, days, extra):
            _time.sleep(0.2 if chunk[0] == points[0] else 0)  # first chunk finishes last
            return [{"hourly": {"time": ["t"], fields[0]: [p[1]]}} for p in chunk]

        conditions._cache.clear()
        with patch.object(conditions, "_fetch", side_effect=fake):
            conditions._load(points)
        for p in points:
            data = conditions._cache[p][1]
            self.assertEqual((data["wave_height"], data["wind_speed_10m"]), ([p[1]], [p[1]]))
        conditions._cache.clear()
