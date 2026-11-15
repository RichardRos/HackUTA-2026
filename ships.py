"""Live ship positions from aisstream.io (AIS over a websocket; needs AISSTREAM_API_KEY).

A background task keeps the latest position and static data (name, type, destination...) for
every vessel heard in the last 30 minutes, in memory. The API process starts it in its lifespan.

    python ships.py            # listen for 60 s and print a summary
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import time

import websockets

URL = "wss://stream.aisstream.io/v0/stream"
STALE_SECONDS = 30 * 60
log = logging.getLogger("ships")

# AIS ship-type codes (ITU-R M.1371): tens digit is the class, units digit the hazard category.
CATEGORIES = {3: "special", 4: "high-speed craft", 6: "passenger", 7: "cargo", 8: "tanker", 9: "other"}
SPECIAL = {30: "fishing", 31: "towing", 32: "towing (large)", 33: "dredging", 34: "diving", 35: "military",
           36: "sailing", 37: "pleasure craft", 50: "pilot", 51: "search and rescue", 52: "tug",
           53: "port tender", 55: "law enforcement", 58: "medical transport"}
HAZARD = {1: "hazardous category A", 2: "hazardous category B", 3: "hazardous category C", 4: "hazardous category D"}
NAV_STATUS = ["under way using engine", "at anchor", "not under command", "restricted manoeuvrability",
              "constrained by draught", "moored", "aground", "engaged in fishing", "under way sailing"]

# MMSI maritime identification digits -> flag. shortcut: the ~50 busiest flags only; others show
# "unknown". Full table: ITU MID list.
FLAGS = {
    **dict.fromkeys(range(351, 358), "Panama"), **dict.fromkeys(range(370, 374), "Panama"),
    636: "Liberia", 637: "Liberia", 538: "Marshall Islands", 477: "Hong Kong",
    **dict.fromkeys(range(563, 567), "Singapore"), **dict.fromkeys((215, 229, 248, 249, 256), "Malta"),
    308: "Bahamas", 309: "Bahamas", 311: "Bahamas", **dict.fromkeys((237, 239, 240, 241), "Greece"),
    412: "China", 413: "China", 414: "China", 209: "Cyprus", 210: "Cyprus", 212: "Cyprus",
    257: "Norway", 258: "Norway", 259: "Norway", 431: "Japan", 432: "Japan",
    **dict.fromkeys(range(232, 236), "United Kingdom"), 338: "United States",
    **dict.fromkeys(range(366, 370), "United States"), 219: "Denmark", 220: "Denmark",
    211: "Germany", 218: "Germany", 244: "Netherlands", 245: "Netherlands", 246: "Netherlands",
    247: "Italy", 226: "France", 227: "France", 228: "France", 440: "South Korea", 441: "South Korea",
    419: "India", 273: "Russia", 271: "Turkey", 304: "Antigua and Barbuda", 305: "Antigua and Barbuda",
    255: "Portugal (Madeira)", 263: "Portugal", 205: "Belgium", 316: "Canada", 503: "Australia",
    710: "Brazil", 574: "Vietnam", 525: "Indonesia", 533: "Malaysia", 548: "Philippines",
    403: "Saudi Arabia", 470: "United Arab Emirates", 422: "Iran", 622: "Egypt", 224: "Spain", 225: "Spain",
    352: "Panama", 341: "Saint Kitts and Nevis", 312: "Belize", 378: "British Virgin Islands",
}


def ship_type(code: int | None) -> dict:
    """Readable class and cargo hazard for an AIS ship-type code."""
    if not code:
        return {"category": "unknown", "type": "unknown", "hazard": None}
    if code in SPECIAL:
        return {"category": "special", "type": SPECIAL[code], "hazard": None}
    category = CATEGORIES.get(code // 10, "other")
    hazard = HAZARD.get(code % 10) if category in ("cargo", "tanker", "passenger", "high-speed craft") else None
    return {"category": category, "type": category + (f", {hazard}" if hazard else ""), "hazard": hazard}


def flag(mmsi: int) -> str:
    return FLAGS.get(int(str(mmsi)[:3]), "unknown") if len(str(mmsi)) == 9 else "unknown"


def _clean(s: object) -> str | None:
    s = str(s or "").replace("@", "").strip()
    return s or None


class Tracker:
    def __init__(self) -> None:
        self.ships: dict[int, dict] = {}
        self.static: dict[int, dict] = {}  # stored vessel data (db vessels table) for ships not re-heard yet
        self.messages = 0
        self.connected = False
        self._task: asyncio.Task | None = None

    def seed(self, vessels: list[dict]) -> None:
        """Load stored static data so a vessel shows its name and type before AIS re-sends it
        (static messages come only every ~6 minutes)."""
        keep = ("name", "imo", "callsign", "destination", "type_code", "category", "hazard",
                "length_m", "beam_m", "draught_m", "eta")
        for v in vessels:
            data = {k: v[k] for k in keep if v.get(k) is not None}
            if data.get("type_code"):
                data["type"] = ship_type(data["type_code"])["type"]
            self.static[v["mmsi"]] = data

    def ingest(self, msg: dict) -> None:
        """Merge one aisstream message into the latest record for its vessel."""
        meta = msg.get("MetaData") or {}
        mmsi = meta.get("MMSI")
        if not mmsi:
            return
        ship = self.ships.get(mmsi)
        if ship is None:
            ship = self.ships[mmsi] = {"mmsi": mmsi, "flag": flag(mmsi), **self.static.get(mmsi, {})}
        ship["seen"] = time.time()
        if name := _clean(meta.get("ShipName")):
            ship["name"] = name
        body = (msg.get("Message") or {}).get(msg.get("MessageType"), {})
        if msg.get("MessageType") == "PositionReport":
            ship["lat"], ship["lon"] = meta.get("latitude"), meta.get("longitude")
            ship["speed_kn"] = body.get("Sog")
            ship["course"] = body.get("Cog")
            heading = body.get("TrueHeading")
            ship["heading"] = heading if heading is not None and heading < 360 else None
            status = body.get("NavigationalStatus")
            ship["status"] = NAV_STATUS[status] if isinstance(status, int) and status < len(NAV_STATUS) else None
            ship["position_at"] = meta.get("time_utc")
            ship["fix_ts"] = time.time()  # when this fix arrived; clients dead-reckon from it
        elif msg.get("MessageType") == "ShipStaticData":
            dim = body.get("Dimension") or {}
            eta = body.get("Eta") or {}
            ship |= {
                "name": _clean(body.get("Name")) or ship.get("name"),
                "imo": body.get("ImoNumber") or None,
                "callsign": _clean(body.get("CallSign")),
                "destination": _clean(body.get("Destination")),
                "type_code": body.get("Type"),
                **ship_type(body.get("Type")),
                "length_m": (dim.get("A") or 0) + (dim.get("B") or 0) or None,
                "beam_m": (dim.get("C") or 0) + (dim.get("D") or 0) or None,
                "draught_m": body.get("MaximumStaticDraught") or None,
                "eta": f"{eta['Month']:02d}-{eta['Day']:02d} {eta['Hour']:02d}:{eta['Minute']:02d} UTC"
                if eta.get("Month") else None,
            }

    def prune(self) -> None:
        cutoff = time.time() - STALE_SECONDS
        for mmsi in [m for m, s in self.ships.items() if s["seen"] < cutoff]:
            del self.ships[mmsi]

    async def run(self, key: str) -> None:
        sub = {"APIKey": key, "BoundingBoxes": [[[-90, -180], [90, 180]]],
               "FilterMessageTypes": ["PositionReport", "ShipStaticData"]}
        backoff = 1
        while True:
            try:
                async with websockets.connect(URL, open_timeout=20, ping_interval=30, max_size=2**20) as ws:
                    await ws.send(json.dumps(sub))
                    self.connected, backoff = True, 1
                    last_prune = time.time()
                    async for raw in ws:
                        msg = json.loads(raw)
                        if "error" in msg:
                            raise RuntimeError(msg["error"])
                        self.ingest(msg)
                        self.messages += 1
                        if time.time() - last_prune > 60:
                            self.prune()
                            last_prune = time.time()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # network drop or bad key: log, back off, reconnect
                log.warning("aisstream disconnected: %s (retry in %ss)", exc, backoff)
            self.connected = False
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)

    def start(self) -> bool:
        key = os.getenv("AISSTREAM_API_KEY", "").strip()
        if not key or self._task:
            return False
        self._task = asyncio.create_task(self.run(key), name="aisstream")
        return True

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    # --- queries ----------------------------------------------------------------------------

    def _located(self, categories: set[str] | None):
        for s in self.ships.values():
            if s.get("lat") is None or not (-90 <= s["lat"] <= 90):
                continue
            if categories and s.get("category") not in categories:
                continue
            yield s

    def overview(self, categories: set[str] | None = None, cell_deg: float = 1.0, limit: int = 3000) -> dict:
        """Located ships thinned to one per `cell_deg` grid cell (most recently heard wins), so a
        global view stays light enough to draw."""
        cells: dict[tuple, dict] = {}
        for s in self._located(categories):
            key = (math.floor(s["lat"] / cell_deg), math.floor(s["lon"] / cell_deg))
            if key not in cells or s["seen"] > cells[key]["seen"]:
                cells[key] = s
        pick = sorted(cells.values(), key=lambda s: s["seen"], reverse=True)[:limit]
        now = time.time()
        return {
            "connected": self.connected,
            "tracked": len(self.ships),
            "shown": len(pick),
            "ships": [{"mmsi": s["mmsi"], "name": s.get("name"), "lat": s["lat"], "lon": s["lon"],
                       "category": s.get("category", "unknown"), "heading": s.get("heading"),
                       "course": s.get("course"), "speed_kn": s.get("speed_kn"),
                       "age_s": round(now - s.get("fix_ts", s["seen"]), 1)}
                      for s in pick],
        }

    def near(self, lat: float, lon: float, radius_nm: float, categories: set[str] | None = None, limit: int = 50) -> list[dict]:
        out = []
        for s in self._located(categories):
            d = _distance_nm(lat, lon, s["lat"], s["lon"])
            if d <= radius_nm:
                out.append({**self.detail(s["mmsi"]), "distance_nm": round(d, 1)})
        return sorted(out, key=lambda s: s["distance_nm"])[:limit]

    def detail(self, mmsi: int) -> dict | None:
        s = self.ships.get(mmsi)
        if not s:
            return None
        out = {k: v for k, v in s.items() if k not in ("seen", "fix_ts")}
        out["last_heard_s"] = round(time.time() - s["seen"])
        out.setdefault("category", "unknown")
        return out


def _distance_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 3440.065 * math.asin(math.sqrt(a))


tracker = Tracker()


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()

    async def main() -> None:
        tracker.start()
        await asyncio.sleep(60)
        await tracker.stop()
        o = tracker.overview({"cargo", "tanker"})
        print(f"{tracker.messages} messages, {o['tracked']} ships, {o['shown']} cargo/tanker cells")
        for s in o["ships"][:5]:
            print(tracker.detail(s["mmsi"]))

    asyncio.run(main())
