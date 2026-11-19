import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def client(tmp_path_factory):
    import os
    ov = tmp_path_factory.mktemp("data") / "overrides.yaml"
    shutil.copy(ROOT / "data" / "overrides.yaml", ov)
    os.environ["LEGSUS_OVERRIDES"] = str(ov)
    # import after env is set
    import importlib

    from app import config, main
    importlib.reload(config)
    importlib.reload(main)
    from fastapi.testclient import TestClient
    with TestClient(main.app) as c:
        yield c


MADINA = dict(from_lat=5.6760, from_lon=-0.1660)
CIRCLE = dict(to_lat=5.5700, to_lon=-0.2150)


def test_health(client):
    assert client.get("/healthz").json() == {"ok": True}
    r = client.get("/readyz").json()
    assert r["stops"] > 2000 and r["patterns"] > 500


def test_plan_madina_to_circle(client):
    r = client.get("/api/plan", params={**MADINA, **CIRCLE}).json()
    its = r["itineraries"]
    assert its, "expected at least one itinerary"
    best = its[0]
    assert any(l["mode"] == "trotro" for l in best["legs"])
    assert best["fare_ghs"] >= 5.5            # official minimum fare (Sept 2026)
    assert r["uber"]["fare_ghs"] > best["fare_ghs"]
    for it in its:
        for l in it["legs"]:
            assert len(l["geometry"]) >= 1


def test_short_trip_is_walk(client):
    r = client.get("/api/plan", params=dict(from_lat=5.6040, from_lon=-0.1870,
                                             to_lat=5.6050, to_lon=-0.1880)).json()
    assert r["itineraries"][0]["legs"][0]["mode"] == "walk"


def test_rejects_outside_ghana(client):
    assert client.get("/api/plan", params=dict(from_lat=51.5, from_lon=-0.1,
                                                to_lat=5.6, to_lon=-0.2)).status_code == 400


def test_search(client):
    names = [x["name"] for x in client.get("/api/search", params={"q": "circle"}).json()]
    assert any("Circle" in n for n in names)


def test_logged_fare_overrides_estimate(client):
    r = client.get("/api/plan", params={**MADINA, **CIRCLE}).json()
    leg = next(l for l in r["itineraries"][0]["legs"] if l["mode"] == "trotro")
    body = {"route": leg["route_ref"], "from_stop": leg["from"]["stop_id"],
            "to_stop": leg["to"]["stop_id"], "fare": 9.99}
    assert client.post("/api/fares", json=body).status_code == 201
    r2 = client.get("/api/plan", params={**MADINA, **CIRCLE}).json()
    legs = [l for it in r2["itineraries"] for l in it["legs"] if l["mode"] == "trotro"
            and l["from"]["stop_id"] == body["from_stop"] and l["to"]["stop_id"] == body["to_stop"]
            and l["route_ref"] == body["route"]]
    assert legs and legs[0]["fare_ghs"] == 9.99 and legs[0]["fare_source"] == "logged"


def test_fare_estimate_steps():
    from app.config import Overrides
    from app.network import Network
    from app.router import Planner
    p = Planner(Network(stops={}, patterns=[]), Overrides())
    assert p.fare_estimate(1000) == 5.50
    assert p.fare_estimate(8000) in p.cfg["fare_steps_ghs"]
    assert p.fare_estimate(8000) > 5.50


def test_readonly_mode_blocks_fare_logging(client, monkeypatch):
    monkeypatch.setenv("LEGSUS_READONLY", "1")
    assert client.get("/api/meta").json()["writable"] is False
    r = client.post("/api/fares", json={"route": "1", "from_stop": "a", "to_stop": "b", "fare": 6})
    assert r.status_code == 503
