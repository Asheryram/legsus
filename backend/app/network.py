"""Load the trotro network from GTFS into an in-memory structure.

The open Accra feed (AccraMobile3 / Trufi) has synthetic timetables
(every route 'every 5 min, 06:00-23:00'), which is fiction for trotros.
So we ignore times entirely and model trotros as *frequency-less* lines:
cost = loading wait + in-vehicle time estimated from distance along the
route shape. That is more honest than pretending to know departures.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

from .geo import GridIndex, haversine_m


@dataclass
class Stop:
    id: str
    name: str
    lat: float
    lon: float


@dataclass
class Pattern:
    """One direction of one trotro line (e.g. 'Trotro 31: 37 → Abeka Lapaz')."""
    idx: int
    route_id: str
    ref: str
    long_name: str
    origin: str
    terminal: str
    stop_ids: list[str]
    cum_m: list[float]                      # distance along shape at each stop
    shape: list[tuple[float, float]]
    stop_shape_idx: list[int]

    def geometry(self, a: int, b: int) -> list[tuple[float, float]]:
        i, j = self.stop_shape_idx[a], self.stop_shape_idx[b]
        return self.shape[i:j + 1] if j >= i else [self.shape[i]]


@dataclass
class Network:
    stops: dict[str, Stop]
    patterns: list[Pattern]
    stop_patterns: dict[str, list[tuple[int, int]]] = field(default_factory=dict)
    grid: GridIndex = field(default_factory=GridIndex)
    transfers: dict[str, list[tuple[str, float]]] = field(default_factory=dict)

    def summary(self) -> dict:
        return {"stops": len(self.stops), "patterns": len(self.patterns),
                "routes": len({p.ref for p in self.patterns})}


_NAME_RE = re.compile(r"^(?:Trotro\s+)?(?P<ref>[^:]+?)\s*:\s*(?P<a>.+?)\s*(?:→|->|=>)\s*(?P<b>.+)$")


def _parse_name(long_name: str, short: str) -> tuple[str, str, str]:
    m = _NAME_RE.match(long_name or "")
    if m:
        return (short or m["ref"]).strip(), m["a"].strip(), m["b"].strip()
    return short or long_name, "", long_name


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def _project(stops: list[Stop], shape: list[tuple[float, float]]) -> list[int]:
    """Snap each stop (in order) to a shape vertex, moving monotonically forward."""
    out, start = [], 0
    n = len(shape)
    for k, s in enumerate(stops):
        remaining = len(stops) - k - 1
        start = min(start, n - 1)
        end = min(n, max(start + 1, n - remaining))  # leave room for later stops
        best_i, best_d = start, float("inf")
        for i in range(start, end):
            d = haversine_m(s.lat, s.lon, shape[i][0], shape[i][1])
            if d < best_d:
                best_i, best_d = i, d
        out.append(best_i)
        start = best_i
    return out


def load_network(gtfs_dir: Path, transfer_radius_m: float = 400.0) -> Network:
    stops = {r["stop_id"]: Stop(r["stop_id"], (r.get("stop_name") or "").strip() or "Unnamed stop",
                                 float(r["stop_lat"]), float(r["stop_lon"]))
             for r in _read(gtfs_dir / "stops.txt")}
    routes = {r["route_id"]: r for r in _read(gtfs_dir / "routes.txt")}
    trips = _read(gtfs_dir / "trips.txt")

    shapes: dict[str, list[tuple[int, float, float]]] = {}
    shp = gtfs_dir / "shapes.txt"
    if shp.exists():
        for r in _read(shp):
            shapes.setdefault(r["shape_id"], []).append(
                (int(r["shape_pt_sequence"]), float(r["shape_pt_lat"]), float(r["shape_pt_lon"])))

    seqs: dict[str, list[tuple[int, str]]] = {}
    for r in _read(gtfs_dir / "stop_times.txt"):
        seqs.setdefault(r["trip_id"], []).append((int(r["stop_sequence"]), r["stop_id"]))

    patterns: list[Pattern] = []
    seen: set[tuple] = set()
    for t in trips:
        seq = [sid for _, sid in sorted(seqs.get(t["trip_id"], []))]
        seq = [s for s in seq if s in stops]
        if len(seq) < 2:
            continue
        key = (t["route_id"], tuple(seq))
        if key in seen:
            continue
        seen.add(key)
        rt = routes.get(t["route_id"], {})
        ref, origin, terminal = _parse_name(rt.get("route_long_name", ""), rt.get("route_short_name", ""))
        stop_objs = [stops[s] for s in seq]
        raw_shape = sorted(shapes.get(t.get("shape_id") or "", []))
        shape = [(la, lo) for _, la, lo in raw_shape] or [(s.lat, s.lon) for s in stop_objs]
        idxs = _project(stop_objs, shape)
        # cumulative distance along shape
        cum_shape = [0.0]
        for i in range(1, len(shape)):
            cum_shape.append(cum_shape[-1] + haversine_m(*shape[i - 1], *shape[i]))
        cum = [cum_shape[i] for i in idxs]
        # guard against bad projections: never less than straight-line
        for k in range(1, len(cum)):
            straight = haversine_m(stop_objs[k - 1].lat, stop_objs[k - 1].lon, stop_objs[k].lat, stop_objs[k].lon)
            if cum[k] - cum[k - 1] < straight:
                cum[k] = cum[k - 1] + straight * 1.2
        patterns.append(Pattern(len(patterns), t["route_id"], ref, rt.get("route_long_name", ""),
                                origin or stop_objs[0].name, terminal or stop_objs[-1].name,
                                seq, cum, shape, idxs))

    net = Network(stops=stops, patterns=patterns)
    for p in patterns:
        for pos, sid in enumerate(p.stop_ids):
            net.stop_patterns.setdefault(sid, []).append((p.idx, pos))
    for s in stops.values():
        if s.id in net.stop_patterns:
            net.grid.add(s.id, s.lat, s.lon)
    for sid in net.stop_patterns:
        s = stops[sid]
        net.transfers[sid] = [(o, d) for o, d in net.grid.within(s.lat, s.lon, transfer_radius_m) if o != sid]
    return net
