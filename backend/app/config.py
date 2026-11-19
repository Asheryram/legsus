"""Settings + personal overrides, loaded from data/overrides.yaml.

The YAML file is YOUR data layer: real fares you paid, where the trotro
actually loads, what the mate shouts, routes that no longer run. It is
merged on top of the (2017-era) open GTFS feed at load time.
"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DATA_DIR = Path(os.environ.get("LEGSUS_DATA_DIR", Path(__file__).resolve().parents[2] / "data"))
GTFS_DIR = Path(os.environ.get("LEGSUS_GTFS_DIR", DATA_DIR / "gtfs"))
OVERRIDES_PATH = Path(os.environ.get("LEGSUS_OVERRIDES", DATA_DIR / "overrides.yaml"))

DEFAULT_SETTINGS: dict[str, Any] = {
    # Movement assumptions (tune to your own experience)
    "walk_speed_kmh": 4.5,
    "walk_detour_factor": 1.3,       # straight line -> real street distance
    "max_access_walk_m": 1200,       # max walk from origin to first stop
    "max_transfer_walk_m": 400,      # max walk between stops when changing
    "trotro_speed_kmh": 14.0,        # average incl. traffic in Accra
    "dwell_secs_per_stop": 20,
    "board_wait_min": 8.0,           # waiting / loading time per boarding
    "transfer_penalty_min": 6.0,     # extra 'hassle' cost per change
    "walk_reluctance": 1.6,          # 1 min walking 'feels like' N min riding
    "max_alternatives": 3,
    # Fare model — official schedule from 26 Sept 2026 starts at GH¢5.50.
    # Distance bands are NOT published, so these are estimates. Real fares
    # you log in overrides.yaml always win.
    "fare_min_ghs": 5.50,
    "fare_base_km": 5.0,             # min fare covers first N km
    "fare_per_km_ghs": 0.55,
    "fare_steps_ghs": [5.50, 6.50, 7.60, 8.70, 9.80, 11.00, 12.00, 13.00, 14.10,
                       15.20, 16.20, 17.30, 18.40, 19.50, 20.60, 21.60],
    # Uber estimate — PLACEHOLDERS. Calibrate from your own receipts.
    "uber_base_ghs": 8.0,
    "uber_per_km_ghs": 3.0,
    "uber_per_min_ghs": 0.4,
    "uber_min_fare_ghs": 15.0,
    "car_speed_kmh": 20.0,
    "road_detour_factor": 1.35,
}


@dataclass
class Overrides:
    settings: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_SETTINGS))
    routes: dict[str, dict[str, Any]] = field(default_factory=dict)   # key: route ref ("31") or route_id
    stops: dict[str, dict[str, Any]] = field(default_factory=dict)    # key: stop_id
    places: dict[str, dict[str, Any]] = field(default_factory=dict)   # saved places: name -> {lat, lon}
    fare_log: list[dict[str, Any]] = field(default_factory=list)

    def route(self, route_id: str, ref: str) -> dict[str, Any]:
        merged: dict[str, Any] = {}
        merged.update(self.routes.get(str(ref), {}) or {})
        merged.update(self.routes.get(str(route_id), {}) or {})
        return merged

    def logged_fare(self, ref: str, from_stop: str, to_stop: str) -> float | None:
        """Most recent fare you logged for this exact leg, else for this route."""
        exact = [f for f in self.fare_log if str(f.get("route")) == str(ref)
                 and f.get("from_stop") == from_stop and f.get("to_stop") == to_stop]
        if exact:
            return float(exact[-1]["fare"])
        return None


_lock = threading.Lock()


def load_overrides(path: Path = OVERRIDES_PATH) -> Overrides:
    if not path.exists():
        return Overrides()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    settings = dict(DEFAULT_SETTINGS)
    settings.update(raw.get("settings") or {})
    return Overrides(
        settings=settings,
        routes={str(k): v or {} for k, v in (raw.get("routes") or {}).items()},
        stops={str(k): v or {} for k, v in (raw.get("stops") or {}).items()},
        places={str(k): v for k, v in (raw.get("places") or {}).items()},
        fare_log=list(raw.get("fare_log") or []),
    )


def append_fare_log(entry: dict[str, Any], path: Path = OVERRIDES_PATH) -> None:
    """Append a fare you actually paid. Keeps the rest of the YAML intact."""
    with _lock:
        raw = {}
        if path.exists():
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        raw.setdefault("fare_log", [])
        raw["fare_log"] = (raw["fare_log"] or []) + [entry]
        tmp = path.with_suffix(".tmp")
        tmp.write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8")
        tmp.replace(path)
