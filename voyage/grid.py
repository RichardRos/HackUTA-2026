"""The navigable ocean as a graph.

Nodes are the water cells of a 0.5° lat/lon grid between 65°S and 72°N (sea ice and polar routes
are out of scope). Each node links to up to 16 neighbours: the 8 adjacent cells plus 8 "knight"
moves, so paths can hold a heading within ~11° instead of zig-zagging at 45°. A move is allowed
only when both ends and four points along it are water on a 1 km land mask, so routes do not cut
across capes or islands. Canals and straits narrower than the grid (Suez, Panama, Gibraltar,
Messina, the Dardanelles...) are added as explicit links along their real course.
"""
from __future__ import annotations

import math
from collections import deque
from functools import lru_cache

import numpy as np
from global_land_mask import globe

RES = 0.5
LAT_MIN, LAT_MAX = -65.0, 72.0
EARTH_NM = 3440.065
MAX_SNAP_KM = 200
# (dy rows of latitude, dx columns of longitude), counter-clockwise from east
MOVES = [(0, 1), (1, 2), (1, 1), (2, 1), (1, 0), (2, -1), (1, -1), (1, -2),
         (0, -1), (-1, -2), (-1, -1), (-2, -1), (-1, 0), (-2, 1), (-1, 1), (-1, 2)]
_SAMPLES = (0.2, 0.4, 0.6, 0.8)

# Passages too narrow for a 0.5° grid, as water waypoints along their real course.
LINKS = {
    "Suez Canal": [(31.35, 32.35), (30.6, 32.32), (29.85, 32.55)],
    "Panama Canal": [(9.45, -79.95), (9.1, -79.75), (8.85, -79.5)],
    "Kiel Canal": [(54.45, 10.25), (53.9, 9.2)],
    "Bosporus": [(40.95, 29.0), (41.3, 29.15)],
    "Dardanelles": [(39.95, 26.1), (40.25, 26.4), (40.45, 26.8)],
    "Strait of Messina": [(38.05, 15.55), (38.3, 15.65)],
    "Strait of Gibraltar": [(35.95, -5.95), (36.0, -5.2)],
    "Singapore Strait": [(1.2, 103.6), (1.25, 104.2)],
    "Øresund": [(55.4, 12.8), (56.1, 12.6)],
    "Bab-el-Mandeb": [(12.75, 43.3), (12.45, 43.5)],
}
# Canals and the Turkish straits take a fixed time whatever the vessel's sea speed: speed limits,
# convoys, locks and pilotage. Typical transit times, not counting waiting at anchor for a slot
# (which can add a day or more at Suez and Panama). Wider straits are sailed at sea speed.
TRANSIT_HOURS = {
    "Suez Canal": 12.0,  # ~120 nm in convoy at 7.5-8 kn
    "Panama Canal": 9.0,  # three lock flights each way plus Gatun Lake
    "Kiel Canal": 8.0,  # ~53 nm at the 8 kn limit, plus locks at each end
    "Bosporus": 2.0,  # ~17 nm under pilotage and traffic control
    "Dardanelles": 4.0,  # ~38 nm under pilotage and traffic control
}


def haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 2 * EARTH_NM * math.asin(min(1.0, math.sqrt(a)))


def bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial true bearing, degrees clockwise from north."""
    p1, p2, dl = math.radians(lat1), math.radians(lat2), math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


class OceanGrid:
    def __init__(self, res: float = RES):
        self.res = res
        self.lats = np.arange(LAT_MIN, LAT_MAX + res / 2, res)
        self.lons = np.arange(-180.0, 180.0, res)
        self.n_lat, self.n_lon = len(self.lats), len(self.lons)
        lat2d, lon2d = np.meshgrid(self.lats, self.lons, indexing="ij")
        self.water = ~globe.is_land(lat2d, lon2d)

        # One bit per allowed move, per node (plain Python ints: fast to test in the search loop).
        mask = np.zeros((self.n_lat, self.n_lon), dtype=np.int64)
        for k, (dy, dx) in enumerate(MOVES):
            ok = self.water.copy()
            rows = np.arange(self.n_lat) + dy
            inside = (rows >= 0) & (rows < self.n_lat)
            ok[~inside, :] = False
            tgt = np.zeros_like(ok)
            tgt[inside, :] = np.roll(self.water, -dx, axis=1)[rows[inside], :]
            ok &= tgt
            for t in _SAMPLES:  # points along the move must be water too
                lat_s = lat2d + dy * res * t
                lon_s = ((lon2d + dx * res * t + 180) % 360) - 180
                ok &= ~globe.is_land(np.clip(lat_s, -90, 90), lon_s)
            mask |= ok.astype(np.int64) << k
        self.moves = mask.tolist()

        # Distance (nm) and bearing of each move depend only on the row's latitude.
        self.move_nm = [[0.0] * len(MOVES) for _ in range(self.n_lat)]
        self.move_brg = [[0.0] * len(MOVES) for _ in range(self.n_lat)]
        for i, lat in enumerate(self.lats):
            for k, (dy, dx) in enumerate(MOVES):
                lat2 = lat + dy * res
                self.move_nm[i][k] = haversine_nm(lat, 0.0, lat2, dx * res)
                self.move_brg[i][k] = bearing(lat, 0.0, lat2, dx * res)

        self.links: dict[int, list[tuple[int, float, float, str]]] = {}
        self._components()  # before links: snapping link ends needs the open-ocean components
        for name, points in LINKS.items():
            a = self.snap(*points[0], max_km=120, min_component=50)  # small seas (Black Sea) count here
            b = self.snap(*points[-1], max_km=120, min_component=50)
            if a is None or b is None or a == b:
                continue
            nm = sum(haversine_nm(*points[n], *points[n + 1]) for n in range(len(points) - 1))
            nm += haversine_nm(*self.latlon(a), *points[0]) + haversine_nm(*points[-1], *self.latlon(b))
            self.links.setdefault(a, []).append((b, nm, bearing(*points[0], *points[-1]), name))
            self.links.setdefault(b, []).append((a, nm, bearing(*points[-1], *points[0]), name))
        self._components()  # again, now that links join seas (Mediterranean-Red Sea, Atlantic-Pacific...)

    # --- nodes ---------------------------------------------------------------------------------
    def node(self, i: int, j: int) -> int:
        return i * self.n_lon + (j % self.n_lon)

    def ij(self, node: int) -> tuple[int, int]:
        return divmod(node, self.n_lon)

    def latlon(self, node: int) -> tuple[float, float]:
        i, j = self.ij(node)
        return float(self.lats[i]), float(self.lons[j])

    def neighbours(self, node: int):
        """(next node, distance nm, bearing deg, link name or None) for every allowed move."""
        i, j = divmod(node, self.n_lon)
        bits = self.moves[i][j]
        if bits:
            row_nm, row_brg = self.move_nm[i], self.move_brg[i]
            for k, (dy, dx) in enumerate(MOVES):
                if bits >> k & 1:
                    yield (i + dy) * self.n_lon + (j + dx) % self.n_lon, row_nm[k], row_brg[k], None
        for nxt, nm, brg, name in self.links.get(node, ()):
            yield nxt, nm, brg, name

    def _components(self) -> None:
        """Label connected water regions so snapping avoids lakes and enclosed pockets."""
        comp = np.full(self.n_lat * self.n_lon, -1, dtype=np.int32)
        sizes: list[int] = []
        for start in np.flatnonzero(self.water.ravel()):
            if comp[start] >= 0:
                continue
            label, size = len(sizes), 0
            comp[start] = label
            queue = deque([int(start)])
            while queue:
                n = queue.popleft()
                size += 1
                for nxt, *_ in self.neighbours(n):
                    if comp[nxt] < 0:
                        comp[nxt] = label
                        queue.append(nxt)
            sizes.append(size)
        self.component = comp
        self.component_size = np.array(sizes + [0], dtype=np.int64)  # [-1] -> 0 for land

    def snap(self, lat: float, lon: float, max_km: float = MAX_SNAP_KM, min_component: int = 2000) -> int | None:
        """Nearest water node within `max_km` that belongs to an open sea (not a lake or pocket)."""
        reach = int(math.ceil(max_km / 111 / self.res)) + 1
        i0 = int(round((lat - LAT_MIN) / self.res))
        j0 = int(round((lon + 180) / self.res))
        best, best_km = None, float("inf")
        for i in range(max(0, i0 - reach), min(self.n_lat, i0 + reach + 1)):
            span = int(math.ceil(reach / max(0.2, math.cos(math.radians(self.lats[i])))))
            for j in range(j0 - span, j0 + span + 1):
                n = self.node(i, j)
                if not self.water[i, j % self.n_lon] or self.component_size[self.component[n]] < min_component:
                    continue
                km = haversine_nm(lat, lon, *self.latlon(n)) * 1.852
                if km < best_km:
                    best, best_km = n, km
        return best if best_km <= max_km else None


@lru_cache(maxsize=1)
def ocean() -> OceanGrid:
    """The process-wide grid (a few seconds to build, ~60 MB)."""
    return OceanGrid()
