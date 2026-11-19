"""Walk → trotro → (walk/transfer) → trotro → walk journey planner.

Plain Dijkstra over a small state graph. Node types:
  S(stop)   : standing at a stop, ready to board
  A(stop)   : just alighted at a stop (may walk to transfer / destination)
  R(p, pos) : sitting in pattern p at position pos
Splitting S and A stops chains of walk→walk→walk through stops.

Edge costs are *generalised minutes* (time + hassle). Real minutes, walk
metres and fares are recomputed from the legs afterwards.
"""
from __future__ import annotations

import heapq
import math
from dataclasses import dataclass
from typing import Any

from .config import Overrides
from .geo import haversine_m
from .network import Network

ORIGIN, DEST = -1, -2


@dataclass
class Place:
    lat: float
    lon: float
    name: str = ""


class Planner:
    def __init__(self, net: Network, ov: Overrides):
        self.net = net
        self.ov = ov
        self.cfg = ov.settings
        self.stop_ids = list(net.stop_patterns.keys())
        self.stop_ix = {s: i for i, s in enumerate(self.stop_ids)}
        n = len(self.stop_ids)
        self.S0, self.A0 = 0, n
        self.R0 = 2 * n
        self.r_off: list[int] = []
        off = self.R0
        for p in net.patterns:
            self.r_off.append(off)
            off += len(p.stop_ids)
        self.n_nodes = off
        self.disabled = {p.idx for p in net.patterns if self.ov.route(p.route_id, p.ref).get("disabled")}
        self.pos_of: list[dict[str, int]] = []
        for p in net.patterns:
            first: dict[str, int] = {}
            for k, sid in enumerate(p.stop_ids):
                first.setdefault(sid, k)
            self.pos_of.append(first)

    def serving(self, a: str, b: str) -> list[int]:
        """All enabled patterns that go from stop a to stop b (in that order)."""
        out = []
        for p_idx, pos in self.net.stop_patterns.get(a, ()):
            if p_idx in self.disabled:
                continue
            pb = self.pos_of[p_idx].get(b)
            if pb is not None and pb > pos:
                out.append(p_idx)
        return out

    # ---------- cost helpers ----------
    def walk_min(self, metres: float) -> float:
        return metres * self.cfg["walk_detour_factor"] / (self.cfg["walk_speed_kmh"] * 1000 / 60)

    def ride_min(self, metres: float, n_hops: int) -> float:
        return metres / (self.cfg["trotro_speed_kmh"] * 1000 / 60) + n_hops * self.cfg["dwell_secs_per_stop"] / 60

    def fare_estimate(self, metres: float) -> float:
        km = metres / 1000
        c = self.cfg
        raw = c["fare_min_ghs"] + max(0.0, km - c["fare_base_km"]) * c["fare_per_km_ghs"]
        for step in c["fare_steps_ghs"]:
            if step >= raw - 1e-9:
                return step
        return round(raw, 2)

    def uber_estimate(self, o: Place, d: Place) -> dict[str, Any]:
        c = self.cfg
        km = haversine_m(o.lat, o.lon, d.lat, d.lon) * c["road_detour_factor"] / 1000
        mins = km / c["car_speed_kmh"] * 60
        cost = max(c["uber_min_fare_ghs"], c["uber_base_ghs"] + km * c["uber_per_km_ghs"] + mins * c["uber_per_min_ghs"])
        return {"km": round(km, 1), "minutes": round(mins), "fare_ghs": round(cost, 2),
                "note": "Estimate from your configured rates — calibrate in overrides.yaml"}

    # ---------- search ----------
    def _dijkstra(self, o: Place, d: Place, pattern_penalty: dict[int, float]):
        c = self.cfg
        net = self.net
        dist: dict[int, float] = {ORIGIN: 0.0}
        prev: dict[int, tuple[int, str, Any]] = {}
        pq: list[tuple[float, int]] = [(0.0, ORIGIN)]

        access = net.grid.within(o.lat, o.lon, c["max_access_walk_m"])
        egress = {sid: m for sid, m in net.grid.within(d.lat, d.lon, c["max_access_walk_m"])}
        board_cost = c["board_wait_min"] + c["transfer_penalty_min"]
        rel = c["walk_reluctance"]

        def relax(u: int, v: int, w: float, kind: str, info: Any = None):
            nd = dist[u] + w
            if nd < dist.get(v, math.inf):
                dist[v] = nd
                prev[v] = (u, kind, info)
                heapq.heappush(pq, (nd, v))

        while pq:
            du, u = heapq.heappop(pq)
            if du > dist.get(u, math.inf):
                continue
            if u == DEST:
                break
            if u == ORIGIN:
                for sid, m in access:
                    relax(u, self.S0 + self.stop_ix[sid], self.walk_min(m) * rel, "walk", m)
                direct = haversine_m(o.lat, o.lon, d.lat, d.lon)
                if direct <= c["max_access_walk_m"] * 1.5:
                    relax(u, DEST, self.walk_min(direct) * rel, "walk", direct)
            elif u < self.A0:  # S node: board any pattern here
                sid = self.stop_ids[u - self.S0]
                for p_idx, pos in net.stop_patterns[sid]:
                    if p_idx in self.disabled or pos >= len(net.patterns[p_idx].stop_ids) - 1:
                        continue
                    relax(u, self.r_off[p_idx] + pos, board_cost + pattern_penalty.get(p_idx, 0.0), "board", p_idx)
            elif u < self.R0:  # A node: transfer walk, stay, or finish
                sid = self.stop_ids[u - self.A0]
                relax(u, self.S0 + (u - self.A0), 0.0, "stay")
                for other, m in net.transfers.get(sid, ()):
                    relax(u, self.S0 + self.stop_ix[other], self.walk_min(m) * rel, "walk", m)
                if sid in egress:
                    relax(u, DEST, self.walk_min(egress[sid]) * rel, "walk", egress[sid])
            else:  # R node
                p_idx = self._pattern_of(u)
                p = net.patterns[p_idx]
                pos = u - self.r_off[p_idx]
                if pos + 1 < len(p.stop_ids):
                    seg = p.cum_m[pos + 1] - p.cum_m[pos]
                    relax(u, u + 1, self.ride_min(seg, 1), "ride", p_idx)
                if pos > 0:
                    relax(u, self.A0 + self.stop_ix[p.stop_ids[pos]], 0.0, "alight", p_idx)
        if DEST not in dist:
            return None
        # backtrack
        path = []
        v = DEST
        while v != ORIGIN:
            u, kind, info = prev[v]
            path.append((u, v, kind, info))
            v = u
        path.reverse()
        return dist[DEST], path

    def _pattern_of(self, node: int) -> int:
        # binary search over r_off
        lo, hi = 0, len(self.r_off) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self.r_off[mid] <= node:
                lo = mid
            else:
                hi = mid - 1
        return lo

    def _node_stop(self, node: int) -> str | None:
        if node < 0:
            return None
        if node < self.R0:
            return self.stop_ids[node % len(self.stop_ids)]
        p = self._pattern_of(node)
        return self.net.patterns[p].stop_ids[node - self.r_off[p]]

    # ---------- itinerary building ----------
    def _point(self, node: int, o: Place, d: Place) -> dict[str, Any]:
        if node == ORIGIN:
            return {"name": o.name or "Start", "lat": o.lat, "lon": o.lon}
        if node == DEST:
            return {"name": d.name or "Destination", "lat": d.lat, "lon": d.lon}
        s = self.net.stops[self._node_stop(node)]
        note = self.ov.stops.get(s.id, {})
        return {"name": note.get("alias") or s.name, "lat": s.lat, "lon": s.lon,
                "stop_id": s.id, "note": note.get("note")}

    def _build(self, path, o: Place, d: Place) -> dict[str, Any]:
        legs: list[dict[str, Any]] = []
        i = 0
        while i < len(path):
            u, v, kind, info = path[i]
            if kind == "walk":
                a, b = self._point(u, o, d), self._point(v, o, d)
                m = info * self.cfg["walk_detour_factor"]
                legs.append({"mode": "walk", "from": a, "to": b, "metres": round(m),
                             "minutes": round(self.walk_min(info), 1),
                             "geometry": [[a["lat"], a["lon"]], [b["lat"], b["lon"]]]})
            elif kind == "board":
                p = self.net.patterns[info]
                start_pos = v - self.r_off[info]
                j = i + 1
                while j < len(path) and path[j][2] == "ride":
                    j += 1
                end_pos = path[j - 1][1] - self.r_off[info] if j > i + 1 else start_pos
                # path[j] is the alight
                metres = p.cum_m[end_pos] - p.cum_m[start_pos]
                hops = end_pos - start_pos
                from_sid, to_sid = p.stop_ids[start_pos], p.stop_ids[end_pos]
                rov = self.ov.route(p.route_id, p.ref)
                logged = self.ov.logged_fare(p.ref, from_sid, to_sid)
                if logged is not None:
                    fare, fare_src = logged, "logged"
                elif rov.get("fare"):
                    fare, fare_src = float(rov["fare"]), "route override"
                else:
                    fare, fare_src = self.fare_estimate(metres), "estimate"
                also = sorted({self.net.patterns[q].ref for q in self.serving(from_sid, to_sid)} - {p.ref},
                              key=lambda r: (len(r), r))
                board_pt = self._point(self.S0 + self.stop_ix[from_sid], o, d)
                alight_pt = self._point(self.S0 + self.stop_ix[to_sid], o, d)
                legs.append({
                    "mode": "trotro", "route_ref": p.ref, "route_id": p.route_id,
                    "also_routes": also,
                    "route_name": p.long_name, "towards": p.terminal,
                    "shout": rov.get("shout") or p.terminal,
                    "route_note": rov.get("note"),
                    "from": board_pt, "to": alight_pt, "stops": hops,
                    "stop_names": [self.net.stops[s].name for s in p.stop_ids[start_pos:end_pos + 1]],
                    "metres": round(metres),
                    "wait_minutes": self.cfg["board_wait_min"],
                    "minutes": round(self.ride_min(metres, hops), 1),
                    "fare_ghs": fare, "fare_source": fare_src,
                    "geometry": [[la, lo] for la, lo in p.geometry(start_pos, end_pos)],
                })
                i = j  # skip rides; alight handled next
                continue
            i += 1
        # merge consecutive walk legs (e.g. alight→transfer)
        merged: list[dict[str, Any]] = []
        for leg in legs:
            if leg["mode"] == "walk" and leg["metres"] < 15:
                continue
            merged.append(leg)
        total_min = sum(l["minutes"] + l.get("wait_minutes", 0) for l in merged)
        fare = sum(l.get("fare_ghs", 0) for l in merged)
        rides = [l for l in merged if l["mode"] == "trotro"]
        return {
            "summary": " → ".join(["Walk"] + [f"Trotro to {l['towards']}" for l in rides] + ["Walk"]) if rides else "Walk all the way",
            "minutes": round(total_min),
            "walk_metres": sum(l["metres"] for l in merged if l["mode"] == "walk"),
            "transfers": max(0, len(rides) - 1),
            "fare_ghs": round(fare, 2),
            "fare_is_estimate": any(l.get("fare_source") == "estimate" for l in rides),
            "legs": merged,
        }

    def plan(self, o: Place, d: Place) -> dict[str, Any]:
        options: list[dict[str, Any]] = []
        sigs: set[tuple] = set()
        penalty: dict[int, float] = {}
        for _ in range(self.cfg["max_alternatives"] + 2):
            res = self._dijkstra(o, d, penalty)
            if not res:
                break
            _, path = res
            it = self._build(path, o, d)
            rides = [l for l in it["legs"] if l["mode"] == "trotro"]
            used = [q for l in rides for q in self.serving(l["from"]["stop_id"], l["to"]["stop_id"])]
            sig = tuple((l["from"]["stop_id"], l["to"]["stop_id"]) for l in rides)
            if sig not in sigs:
                sigs.add(sig)
                options.append(it)
            if not used or len(options) >= self.cfg["max_alternatives"]:
                break
            for p_idx in used:
                penalty[p_idx] = penalty.get(p_idx, 0.0) + 20.0
        # Drop options that are worse on time, money AND changes than another one.
        def dominated(a, b):
            return (b["minutes"] <= a["minutes"] and b["fare_ghs"] <= a["fare_ghs"]
                    and b["transfers"] <= a["transfers"] and b is not a)
        options = [a for a in options if not any(dominated(a, b) for b in options)]
        options.sort(key=lambda x: x["minutes"] + self.cfg["transfer_penalty_min"] * x["transfers"])
        uber = self.uber_estimate(o, d)
        for it in options:
            it["saves_vs_uber_ghs"] = round(uber["fare_ghs"] - it["fare_ghs"], 2)
        return {"origin": o.__dict__, "destination": d.__dict__, "itineraries": options, "uber": uber}
