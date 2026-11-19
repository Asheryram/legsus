"""LegSus API — trotro journey planner for Accra."""
from __future__ import annotations

import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import GTFS_DIR, OVERRIDES_PATH, append_fare_log, load_overrides
from .network import Network, load_network
from .router import Place, Planner

log = logging.getLogger("legsus")
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")

FRONTEND_DIR = Path(os.environ.get("LEGSUS_FRONTEND_DIR", Path(__file__).resolve().parents[2] / "frontend"))


class State:
    net: Network | None = None
    planner: Planner | None = None
    loaded_at: float = 0.0


state = State()


def reload_planner() -> None:
    ov = load_overrides(OVERRIDES_PATH)
    state.planner = Planner(state.net, ov)
    state.loaded_at = time.time()
    log.info("overrides loaded: %d routes, %d stops, %d fares logged",
             len(ov.routes), len(ov.stops), len(ov.fare_log))


@asynccontextmanager
async def lifespan(_: FastAPI):
    t = time.time()
    state.net = load_network(GTFS_DIR)
    reload_planner()
    log.info("network loaded in %.2fs: %s", time.time() - t, state.net.summary())
    yield


app = FastAPI(title="LegSus", version="0.1.0", lifespan=lifespan)


# ---------- ops ----------
@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/readyz")
def readyz():
    if not state.planner:
        raise HTTPException(503, "network not loaded")
    return {"ok": True, **state.net.summary()}


# ---------- api ----------
@app.get("/api/meta")
def meta():
    p = state.planner
    return {**state.net.summary(), "settings": p.cfg,
            "places": p.ov.places, "fares_logged": len(p.ov.fare_log)}


@app.get("/api/search")
def search(q: str = Query(..., min_length=2), limit: int = 8):
    ql = q.lower().strip()
    p = state.planner
    results = []
    for name, pl in p.ov.places.items():
        if ql in name.lower():
            results.append({"name": name, "lat": pl["lat"], "lon": pl["lon"], "kind": "saved"})
    seen: set[str] = set()
    ranked = []
    for sid in p.stop_ids:
        s = state.net.stops[sid]
        n = s.name.lower()
        if ql in n and s.name not in seen:
            seen.add(s.name)
            ranked.append((0 if n.startswith(ql) else 1, len(n), s))
    ranked.sort(key=lambda t: (t[0], t[1]))
    for _, _, s in ranked[:limit]:
        results.append({"name": s.name, "lat": s.lat, "lon": s.lon, "kind": "stop", "stop_id": s.id})
    return results[:limit]


@app.get("/api/plan")
def plan(from_lat: float, from_lon: float, to_lat: float, to_lon: float,
         from_name: str = "", to_name: str = ""):
    for lat, lon in ((from_lat, from_lon), (to_lat, to_lon)):
        if not (4.5 <= lat <= 11.5 and -3.5 <= lon <= 1.5):
            raise HTTPException(400, "Coordinates must be inside Ghana")
    t = time.time()
    res = state.planner.plan(Place(from_lat, from_lon, from_name), Place(to_lat, to_lon, to_name))
    res["took_ms"] = round((time.time() - t) * 1000)
    if not res["itineraries"]:
        res["message"] = ("No trotro route found within walking distance. The open data mostly covers "
                          "Accra/Tema — add your own route or increase max_access_walk_m.")
    return res


class FareIn(BaseModel):
    route: str = Field(..., description="Trotro ref, e.g. '31'")
    from_stop: str
    to_stop: str
    fare: float = Field(..., gt=0, lt=500)
    note: str | None = None


@app.post("/api/fares", status_code=201)
def log_fare(body: FareIn):
    entry = {**body.model_dump(exclude_none=True), "date": date.today().isoformat()}
    append_fare_log(entry, OVERRIDES_PATH)
    reload_planner()
    return {"saved": entry}


@app.post("/api/reload")
def reload():
    reload_planner()
    return {"ok": True}


# ---------- frontend ----------
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

    @app.get("/")
    def index():
        return FileResponse(FRONTEND_DIR / "index.html")
