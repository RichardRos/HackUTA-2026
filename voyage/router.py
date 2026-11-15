"""Weather-optimal ship routing on the ocean grid.

1. Shortest-distance A* from start to end: the baseline, and the corridor whose forecast we fetch.
2. Speed table: the SuperLearner predicts the speed factor for every corridor lattice cell, time
   slice and heading (16 bins) in one vectorised batch. The search then only does table lookups.
3. Time-dependent A* inside the corridor. Leaving node n at time t along a move of d nm, the ship's
   speed is predicted from the conditions forecast at node n+1 *at the time it gets there*:
       t' = t + d / (V * f(n+1, t + d / V)),   then   t'' = t + d / (V * f(n+1, t'))
   (one fixed-point refinement), so a storm that will have moved on by the time the ship arrives
   costs nothing, and one that will arrive with it does. Waves above the vessel's limit add a
   penalty instead of a hard wall, so a route always exists.
4. The baseline is replayed through the same model, so "time saved" compares like with like, and the
   conformal half-width q gives an early/late ETA band (speed factor ± q along the whole route).
"""
from __future__ import annotations

import heapq
import math
import threading
import time
from datetime import datetime, timedelta

import httpx
import numpy as np

from voyage import conditions
from voyage.grid import TRANSIT_HOURS, OceanGrid, haversine_nm, ocean
from voyage.model import VESSELS, SpeedModel, Vessel, features
from voyage.ports import APPROACH, ODYSSEY_STOPS, PORTS_BY_ID

HEADINGS = 16
CORRIDOR_DEG = 5.0  # search this far either side of the shortest path
HAZARD_WEIGHT = 2.0  # each metre of wave above the limit makes a move this much more costly
_cache: dict[tuple, tuple[float, dict]] = {}
_cache_lock = threading.Lock()


def _astar_distance(g: OceanGrid, start: int, goal: int) -> list[int] | None:
    """Plain shortest sea path by distance."""
    glat, glon = g.latlon(goal)
    best = {start: 0.0}
    prev: dict[int, int] = {}
    heap = [(0.0, 0.0, start)]
    while heap:
        _, d, node = heapq.heappop(heap)
        if node == goal:
            path = [node]
            while path[-1] in prev:
                path.append(prev[path[-1]])
            return path[::-1]
        if d > best.get(node, math.inf):
            continue
        for nxt, nm, _, _ in g.neighbours(node):
            nd = d + nm
            if nd < best.get(nxt, math.inf):
                best[nxt] = nd
                prev[nxt] = node
                heapq.heappush(heap, (nd + haversine_nm(*g.latlon(nxt), glat, glon), nd, nxt))
    return None


def _corridor_nodes(g: OceanGrid, path: list[int], radius_deg: float) -> np.ndarray:
    """Boolean (n_lat * n_lon) mask of nodes within `radius_deg` of the path."""
    mask = np.zeros((g.n_lat, g.n_lon), dtype=bool)
    r = int(math.ceil(radius_deg / g.res))
    for node in path:
        i, j = g.ij(node)
        span = int(math.ceil(r / max(0.25, math.cos(math.radians(g.lats[i])))))
        cols = np.arange(j - span, j + span + 1) % g.n_lon
        mask[max(0, i - r):i + r + 1][:, cols] = True
    return mask.ravel() & g.water.ravel()


class SpeedTable:
    """Predicted speed factor and wave height by (lattice cell, time slice, heading bin)."""

    def __init__(self, field: conditions.Field, model: SpeedModel, vessel: Vessel,
                 cell_time_h: np.ndarray, duration_h: float):
        self.field = field
        self.slice_h = 3 if duration_h <= 96 else 6 if duration_h <= 300 else 12
        self.window_h = max(48.0, 0.4 * duration_h)
        horizon = min(field.hours - 1, int(duration_h * 2.5) + 48)
        self.n_slices = horizon // self.slice_h + 1
        P = len(field.points)
        lo = np.clip(((cell_time_h - self.window_h) // self.slice_h).astype(int), 0, self.n_slices - 1)
        hi = np.clip(((cell_time_h + self.window_h) // self.slice_h).astype(int), 0, self.n_slices - 1)
        self.lo, self.hi = lo.tolist(), hi.tolist()
        cells, slices = [], []
        for p in range(P):
            if field.is_sea[p]:
                span = np.arange(lo[p], hi[p] + 1)
                cells.append(np.full(len(span), p))
                slices.append(span)
        cells = np.concatenate(cells)
        slices = np.concatenate(slices)
        hours = np.minimum(slices * self.slice_h, field.hours - 1)
        headings = np.arange(HEADINGS) * (360 / HEADINGS)
        rows = len(cells)
        c = np.repeat(cells, HEADINGS)
        hrs = np.repeat(hours, HEADINGS)
        X = features(vessel, np.tile(headings, rows), field.hs[c, hrs], field.tp[c, hrs], field.wave_from[c, hrs],
                     field.wind[c, hrs], field.wind_from[c, hrs], field.cur_u[c, hrs], field.cur_v[c, hrs])
        factor = model.factor(X).reshape(rows, HEADINGS)
        self.factor = np.full((P, self.n_slices, HEADINGS), np.nan, dtype=np.float32)
        self.factor[cells, slices] = factor
        self.max_factor = float(np.nanmax(factor))
        self.rows = rows * HEADINGS

    def slice_of(self, cell: int, t_h: float) -> int:
        s = int(t_h // self.slice_h)
        return min(max(s, self.lo[cell]), self.hi[cell])

    def f(self, cell: int, t_h: float, brg: float) -> float:
        return float(self.factor[cell, self.slice_of(cell, t_h), int(round(brg / (360 / HEADINGS))) % HEADINGS])

    def at(self, cell: int, t_h: float) -> dict:
        h = min(int(round(t_h)), self.field.hours - 1)
        F = self.field
        return {"hs": float(F.hs[cell, h]), "tp": float(F.tp[cell, h]), "wind": float(F.wind[cell, h]),
                "wave_from": float(F.wave_from[cell, h]), "cur_u": float(F.cur_u[cell, h]),
                "cur_v": float(F.cur_v[cell, h]), "beyond_forecast": h > F.horizon_h}


def _arrive(table: SpeedTable, cell: int, t: float, nm: float, brg: float, kn: float) -> float:
    """Arrival time at the next node, using the conditions predicted there at arrival (n+1)."""
    t1 = t + nm / (kn * table.f(cell, t + nm / kn, brg))
    return t + nm / (kn * table.f(cell, t1, brg))


def _step(table: SpeedTable, cell: int, t: float, nm: float, brg: float, kn: float, name: str | None) -> float:
    """Arrival at the next node: a fixed transit time through a canal, otherwise sailed at the speed
    predicted for the conditions there on arrival."""
    if name in TRANSIT_HOURS:
        return t + TRANSIT_HOURS[name]
    return _arrive(table, cell, t, nm, brg, kn)


def _astar_time(g: OceanGrid, start: int, goal: int, allowed: np.ndarray, cell_of: dict[int, int],
                table: SpeedTable, vessel: Vessel) -> list[tuple[int, float, str | None]] | None:
    """Fastest path; returns (node, arrival hours, passage name) per step."""
    kn = vessel.service_kn
    fastest = kn * table.max_factor
    glat, glon = g.latlon(goal)
    best = {start: 0.0}
    arrive = {start: 0.0}
    prev: dict[int, tuple[int, str | None]] = {}
    heap = [(0.0, 0.0, start)]
    while heap:
        _, cost, node = heapq.heappop(heap)
        if node == goal:
            out = [(node, arrive[node], None)]
            while out[-1][0] in prev:
                p, name = prev[out[-1][0]]
                out[-1] = (out[-1][0], out[-1][1], name)
                out.append((p, arrive[p], None))
            return out[::-1]
        if cost > best.get(node, math.inf):
            continue
        t = arrive[node]
        for nxt, nm, brg, name in g.neighbours(node):
            if not allowed[nxt]:
                continue
            cell = cell_of[nxt]
            t2 = _step(table, cell, t, nm, brg, kn, name)
            over = 0.0 if name in TRANSIT_HOURS else table.at(cell, t2)["hs"] - vessel.hs_limit_m  # canals are sheltered
            c2 = cost + (t2 - t) * (1 + HAZARD_WEIGHT * max(0.0, over))
            if c2 < best.get(nxt, math.inf):
                best[nxt] = c2
                arrive[nxt] = t2
                prev[nxt] = (node, name)
                heapq.heappush(heap, (c2 + haversine_nm(*g.latlon(nxt), glat, glon) / fastest, c2, nxt))
    return None


def _replay(g: OceanGrid, path: list[int], cell_of: dict[int, int], table: SpeedTable, kn: float,
            shift: float = 0.0, t0: float = 0.0) -> float:
    """Hours to sail `path` from t0 with every speed factor shifted by `shift` (for the ETA band)."""
    t = t0
    for a, b in zip(path, path[1:]):
        nm, brg, name = _edge(g, a, b)
        if shift and name not in TRANSIT_HOURS:  # canal times are fixed; only sea legs carry model error
            t1 = _arrive(table, cell_of[b], t, nm, brg, kn)  # same n+1 arrival as the search, then shifted
            t += nm / (kn * max(0.05, nm / (kn * (t1 - t)) + shift))
        else:
            t = _step(table, cell_of[b], t, nm, brg, kn, name)
    return t


def _edge(g: OceanGrid, a: int, b: int) -> tuple[float, float, str | None]:
    """Distance, bearing and passage name of the move from a to b (a canal link wins over a grid move)."""
    found = None
    for nxt, nm, brg, name in g.neighbours(a):
        if nxt == b and (found is None or name):
            found = (nm, brg, name)
    return found or (haversine_nm(*g.latlon(a), *g.latlon(b)), 0.0, None)


def _thin(points: list[dict], keep: int = 500) -> list[dict]:
    if len(points) <= keep:
        return points
    step = len(points) / keep
    picked = [points[int(i * step)] for i in range(keep)]
    return picked + [points[-1]]


def route_leg(start: tuple[float, float], end: tuple[float, float], vessel: Vessel, depart: datetime,
              model: SpeedModel, kn: float | None = None) -> dict:
    g = ocean()
    a = g.snap(*APPROACH.get(start, start))
    b = g.snap(*APPROACH.get(end, end))
    if a is None:
        raise ValueError(f"start {start[0]:.2f}, {start[1]:.2f} is not within 200 km of navigable sea")
    if b is None:
        raise ValueError(f"end {end[0]:.2f}, {end[1]:.2f} is not within 200 km of navigable sea")
    if a == b:
        raise ValueError("start and end are the same place (within one 0.5° grid cell); pick points further apart")
    if g.component[a] != g.component[b]:
        raise ValueError("no sea route connects these two points")
    vessel = vessel if kn is None else Vessel(**{**vessel.__dict__, "service_kn": kn})
    base = _astar_distance(g, a, b)
    if base is None:
        raise ValueError("no sea route connects these two points")
    base_ll = [g.latlon(n) for n in base]
    base_nm = sum(_edge(g, p, q)[0] for p, q in zip(base, base[1:]))
    est_h = base_nm / (vessel.service_kn * 0.85) + 1

    # Forecast for the corridor; each lattice cell is weighted toward when the baseline passes it.
    corridor = conditions.corridor(base_ll, radius_deg=CORRIDOR_DEG + 1)
    try:
        field, forecast = conditions.field(corridor, depart), True
    except (conditions.RateLimited, httpx.HTTPError):  # provider throttled or down: plan on a calm sea, flagged
        field, forecast = conditions.calm(corridor, depart), False
    if not field.is_sea.any():
        raise ValueError("no marine forecast along this route")
    cum = np.concatenate([[0.0], np.cumsum([haversine_nm(*p, *q) for p, q in zip(base_ll, base_ll[1:])])])
    bl = np.array(base_ll)
    dl = (field.points[:, None, 0] - bl[None, :, 0]) ** 2 + (
        ((field.points[:, None, 1] - bl[None, :, 1] + 180) % 360 - 180) * np.cos(np.radians(bl[None, :, 0]))) ** 2
    cell_time = cum[np.argmin(dl, axis=1)] / (vessel.service_kn * 0.85)
    table = SpeedTable(field, model, vessel, cell_time, est_h)

    allowed = _corridor_nodes(g, base, CORRIDOR_DEG)
    for n in base:
        allowed[n] = True
    nodes = np.flatnonzero(allowed)
    for n in base:  # canal links can leave the corridor mask; keep both ends
        for nxt, *_ in g.neighbours(n):
            if not allowed[nxt]:
                allowed[nxt] = True
    nodes = np.flatnonzero(allowed)
    cell_of: dict[int, int] = {}
    for k in range(0, len(nodes), 4000):
        chunk = nodes[k:k + 4000]
        lat = g.lats[chunk // g.n_lon]
        lon = g.lons[chunk % g.n_lon]
        cell_of.update(zip(chunk.tolist(), field.nearest(lat, lon).tolist()))

    steps = _astar_time(g, a, b, allowed, cell_of, table, vessel)
    if steps is None:
        steps = [(n, 0.0, None) for n in base]
    path = [s[0] for s in steps]
    hours = _replay(g, path, cell_of, table, vessel.service_kn)
    base_hours = _replay(g, base, cell_of, table, vessel.service_kn)
    early = _replay(g, path, cell_of, table, vessel.service_kn, shift=model.q90)
    late = _replay(g, path, cell_of, table, vessel.service_kn, shift=-model.q90)

    points, passages, hazard_h, beyond, t_prev = [], [], 0.0, 0, 0.0
    passage_hours: dict[str, float] = {}
    t, sailed_nm = 0.0, 0.0
    for idx, (node, _, name) in enumerate(steps):
        lat, lon = g.latlon(node)
        if idx:
            nm, brg, link = _edge(g, path[idx - 1], node)
            t_prev, t = t, _step(table, cell_of[node], t, nm, brg, vessel.service_kn, link)
            f = nm / (vessel.service_kn * (t - t_prev)) if t > t_prev else 1.0
            sailed_nm += nm  # a canal counts its real course, not the straight line between its ends
        else:
            nm, brg, f, link = 0.0, 0.0, 1.0, None
        cond = table.at(cell_of[node], t)
        h = math.radians(brg)
        if cond["hs"] > vessel.hs_limit_m and link not in TRANSIT_HOURS:
            hazard_h += t - t_prev
        beyond += cond["beyond_forecast"]
        if name and name not in passages:
            passages.append(name)
            if name in TRANSIT_HOURS:
                passage_hours[name] = TRANSIT_HOURS[name]
        points.append({"lat": round(lat, 3), "lon": round(lon, 3), "t": (depart + timedelta(hours=t)).isoformat(),
                       "hours": round(t, 2), "hs": round(cond["hs"], 2), "wind": round(cond["wind"], 1),
                       "current_along": round(cond["cur_u"] * math.sin(h) + cond["cur_v"] * math.cos(h), 2),
                       "factor": round(f, 3), "sog_kn": round(vessel.service_kn * f, 1),
                       "beyond_forecast": cond["beyond_forecast"], **({"passage": name} if name else {})})
    nm_total = sailed_nm
    first, last = {"lat": start[0], "lon": start[1]}, {"lat": end[0], "lon": end[1]}
    return {
        "forecast": forecast,
        "depart": depart.isoformat(), "arrive": (depart + timedelta(hours=hours)).isoformat(),
        "duration_h": round(hours, 1), "distance_nm": round(nm_total, 1),
        "eta_band": {"early": (depart + timedelta(hours=early)).isoformat(),
                     "late": (depart + timedelta(hours=late)).isoformat()},
        "shortest": {"distance_nm": round(base_nm, 1), "duration_h": round(base_hours, 1),
                     "arrive": (depart + timedelta(hours=base_hours)).isoformat()},
        "saved_h": round(base_hours - hours, 1),
        "max_hs_m": round(max((p["hs"] for p in points), default=0.0), 2),
        "hours_above_limit": round(hazard_h, 1),
        "beyond_forecast_pct": round(100 * beyond / max(1, len(points)), 1),
        "passages": passages,
        "passage_hours": passage_hours,  # fixed transit time of each canal used
        "path": [{**first, "hours": 0.0}] + _thin(points) + [{**last, "hours": round(hours, 2)}],
        "shortest_path": [first] + [{"lat": round(la, 3), "lon": round(lo, 3)} for la, lo in base_ll[::2]] + [last],
        "debug": {"speed_table_rows": table.rows, "lattice_points": int(len(field.points)), "corridor_nodes": int(len(nodes))},
    }


def _model_summary(model: SpeedModel) -> dict:
    m = model.meta
    return {"trained_at": m.get("trained_at"), "rows": m.get("rows"), "real_rows": m.get("real_rows"),
            "cv_r2": m.get("cv", {}).get("superlearner", {}).get("r2"),
            "cv_rmse": m.get("cv", {}).get("superlearner", {}).get("rmse"),
            "weights": m.get("weights"), "conformal_q90": m.get("conformal_q90"), "note": m.get("note")}


def plan(*, mode: str, vessel_id: str, start: tuple[float, float] | None = None, end: tuple[float, float] | None = None,
         start_port: str | None = None, end_port: str | None = None, depart: str | None = None,
         speed_kn: float | None = None, dwell_h: float = 24.0, model: SpeedModel | None = None) -> dict:
    """Plan a voyage: mode 'points' (start/end coordinates), 'ports' (port ids) or 'odyssey'."""
    if vessel_id not in VESSELS:
        raise ValueError(f"vessel must be one of {', '.join(VESSELS)}")
    vessel = VESSELS[vessel_id]
    t0 = conditions.depart_time(depart)
    key = (mode, vessel_id, start and tuple(round(c, 2) for c in start), end and tuple(round(c, 2) for c in end),
           start_port, end_port, t0.replace(minute=0, second=0, microsecond=0).isoformat(), speed_kn, dwell_h)
    with _cache_lock:
        hit = _cache.get(key)
        if hit and time.monotonic() - hit[0] < 1800:
            return hit[1]
    if model is None:
        from voyage import storage
        model = storage.current()

    if mode == "odyssey":
        stops = ODYSSEY_STOPS
    elif mode == "ports":
        if start_port not in PORTS_BY_ID or end_port not in PORTS_BY_ID:
            raise ValueError("unknown port id")
        stops = [PORTS_BY_ID[start_port], PORTS_BY_ID[end_port]]
    elif mode == "points":
        if not start or not end:
            raise ValueError("start and end are required")
        stops = [{"name": "Start", "lat": start[0], "lon": start[1]}, {"name": "End", "lat": end[0], "lon": end[1]}]
    else:
        raise ValueError("mode must be points, ports or odyssey")
    if speed_kn is not None and not 2 <= speed_kn <= 30:
        raise ValueError("speed_kn must be between 2 and 30")

    legs, t = [], t0
    for a, b in zip(stops, stops[1:]):
        leg = route_leg((a["lat"], a["lon"]), (b["lat"], b["lon"]), vessel, t, model, speed_kn)
        leg["from"], leg["to"] = a, b
        legs.append(leg)
        t = datetime.fromisoformat(leg["arrive"]) + timedelta(hours=dwell_h if mode == "odyssey" else 0)
    arrive = datetime.fromisoformat(legs[-1]["arrive"])
    result = {
        "mode": mode, "vessel": {"id": vessel.id, "label": vessel.label, "service_kn": speed_kn or vessel.service_kn,
                                 "hs_limit_m": vessel.hs_limit_m},
        "depart": t0.isoformat(), "arrive": arrive.isoformat(),
        "duration_h": round((arrive - t0).total_seconds() / 3600, 1),
        "distance_nm": round(sum(leg["distance_nm"] for leg in legs), 1),
        "saved_h": round(sum(leg["saved_h"] for leg in legs), 1),
        "max_hs_m": max(leg["max_hs_m"] for leg in legs),
        "hours_above_limit": round(sum(leg["hours_above_limit"] for leg in legs), 1),
        "beyond_forecast_pct": round(float(np.mean([leg["beyond_forecast_pct"] for leg in legs])), 1),
        # Every leg's uncertainty adds up (speed errors are correlated along a voyage, so no sqrt-sum).
        "eta_band": {"early": (arrive - sum((datetime.fromisoformat(leg["arrive"]) - datetime.fromisoformat(leg["eta_band"]["early"])
                                            for leg in legs), timedelta())).isoformat(),
                     "late": (arrive + sum((datetime.fromisoformat(leg["eta_band"]["late"]) - datetime.fromisoformat(leg["arrive"])
                                           for leg in legs), timedelta())).isoformat()},
        "stops": stops, "legs": legs, "model": _model_summary(model),
        "conditions_source": "Open-Meteo forecast: waves, ocean currents (marine) and 10 m wind"
        if all(leg["forecast"] for leg in legs) else "Forecast unavailable (provider rate limit): calm-sea estimate",
    }
    if all(leg["forecast"] for leg in legs):  # a calm-sea fallback is retried next time, not cached
        with _cache_lock:
            _cache[key] = (time.monotonic(), result)
    return result
