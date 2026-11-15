import time
import unittest

import ships


def position(mmsi, lat, lon, name="ALPHA", sog=12.3, status=0):
    return {"MessageType": "PositionReport",
            "MetaData": {"MMSI": mmsi, "ShipName": f"{name}@@@ ", "latitude": lat, "longitude": lon, "time_utc": "t"},
            "Message": {"PositionReport": {"Sog": sog, "Cog": 90.0, "TrueHeading": 511, "NavigationalStatus": status}}}


def static(mmsi, type_code=82, dest="ROTTERDAM"):
    return {"MessageType": "ShipStaticData", "MetaData": {"MMSI": mmsi, "ShipName": "ALPHA"},
            "Message": {"ShipStaticData": {"Name": "ALPHA", "ImoNumber": 9000001, "CallSign": "ABC1", "Type": type_code,
                                           "Destination": dest, "Dimension": {"A": 200, "B": 50, "C": 20, "D": 20},
                                           "Eta": {"Month": 11, "Day": 20, "Hour": 6, "Minute": 0},
                                           "MaximumStaticDraught": 12.5}}}


class ShipTypeTests(unittest.TestCase):
    def test_tanker_with_hazard(self):
        self.assertEqual(ships.ship_type(82), {"category": "tanker", "type": "tanker, hazardous category B",
                                               "hazard": "hazardous category B"})

    def test_special_and_unknown(self):
        self.assertEqual(ships.ship_type(52)["type"], "tug")
        self.assertEqual(ships.ship_type(None)["category"], "unknown")

    def test_flag_from_mmsi(self):
        self.assertEqual(ships.flag(563288500), "Singapore")
        self.assertEqual(ships.flag(12345), "unknown")


class TrackerTests(unittest.TestCase):
    def setUp(self):
        self.t = ships.Tracker()

    def test_merges_position_and_static(self):
        self.t.ingest(position(244670775, 52.0, 5.1))
        self.t.ingest(static(244670775))
        d = self.t.detail(244670775)
        self.assertEqual(d["name"], "ALPHA")  # AIS "@" padding stripped
        self.assertEqual((d["category"], d["hazard"], d["destination"]), ("tanker", "hazardous category B", "ROTTERDAM"))
        self.assertEqual((d["length_m"], d["beam_m"], d["eta"]), (250, 40, "11-20 06:00 UTC"))
        self.assertIsNone(d["heading"])  # 511 means "not available"
        self.assertEqual(d["status"], "under way using engine")
        self.assertEqual(d["flag"], "Netherlands")

    def test_overview_keeps_one_ship_per_cell_and_filters(self):
        for mmsi, lat in ((244000001, 10.1), (244000002, 10.2), (244000003, 20.5)):
            self.t.ingest(position(mmsi, lat, 5.5))
            self.t.ingest(static(mmsi, 70))
        self.t.ingest(position(244000004, 30.5, 5.5))  # no static data yet -> unknown
        o = self.t.overview({"cargo"})
        self.assertEqual(o["shown"], 2)
        self.assertEqual(self.t.overview(None)["shown"], 3)

    def test_near_and_prune(self):
        self.t.ingest(position(244000001, 51.9, 4.1))
        self.t.ingest(position(244000002, 40.0, -70.0))
        near = self.t.near(51.95, 4.05, 20)
        self.assertEqual([s["mmsi"] for s in near], [244000001])
        self.t.ships[244000002]["seen"] = time.time() - ships.STALE_SECONDS - 1
        self.t.prune()
        self.assertNotIn(244000002, self.t.ships)


class StoredDataTests(unittest.TestCase):
    def test_seed_fills_static_until_aiss_resends_it(self):
        t = ships.Tracker()
        t.seed([{"mmsi": 244000001, "name": "STORED", "type_code": 80, "category": "tanker", "destination": "RTM"}])
        t.ingest(position(244000001, 51.9, 4.1, name=""))
        d = t.detail(244000001)
        self.assertEqual((d["name"], d["category"], d["type"], d["destination"]), ("STORED", "tanker", "tanker", "RTM"))

    def test_get_ship_merges_live_over_stored(self):
        from unittest.mock import patch

        import mcp_server

        t = ships.Tracker()
        t.ingest(position(244000001, 51.9, 4.1, name="LIVE"))
        stored = [{"mmsi": 244000001, "name": "OLD", "destination": "RTM", "first_seen": "2026-11-01",
                   "stored_lat": 50.0, "stored_lon": 3.0, "stored_at": "2026-11-14"}]
        with patch.object(ships, "tracker", t), patch.object(mcp_server, "_query", return_value=stored):
            d = mcp_server.get_ship(244000001)
            self.assertEqual((d["name"], d["destination"], d["lat"], d["live"]), ("LIVE", "RTM", 51.9, True))
        # Not heard live: falls back to the stored fix
        with patch.object(ships, "tracker", ships.Tracker()), patch.object(mcp_server, "_query", return_value=stored):
            d = mcp_server.get_ship(244000001)
        self.assertEqual((d["lat"], d["lon"], d["live"]), (50.0, 3.0, False))


if __name__ == "__main__":
    unittest.main()
