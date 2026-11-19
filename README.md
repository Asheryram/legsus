# LegSus — trotro journey planner (Accra)

Tells you **which trotro to board, where to board it, where to get down, what to walk** — and how much you save versus Uber.

```
walk → board trotro (any of 221 / 422 …) → change at Spanner → get down → walk
```

## Run it

```bash
# Docker (recommended)
make up                 # http://localhost:8000
# or locally
pip install -r backend/requirements-dev.txt
make dev                # http://localhost:8000
make test lint
```

## How it works

| Piece | What it does |
|---|---|
| `data/gtfs/` | Open Accra trotro feed (AccraMobile3 → Trufi, OSM). 282 lines, 563 directions, 2,666 stops. |
| `backend/app/network.py` | Loads GTFS, snaps stops onto route shapes, computes distances along the road. |
| `backend/app/router.py` | Dijkstra over stop/vehicle states: walk → board → ride → alight → transfer-walk → … Returns up to 3 non-dominated options. Groups trotros that serve the same boarding/alighting stops. |
| `data/overrides.yaml` | **Your data layer.** Real fares, real boarding points, mate calls, dead routes, speed assumptions, Uber rates. Wins over the open data. |
| `frontend/` | Vanilla JS + Leaflet (vendored). Search stops, tap the map, or use GPS. Log the fare you paid in one tap. |

### Deliberate design decisions
- **No timetables.** The feed says "every 5 min, 06:00–23:00" for every route — fiction. Trotros leave when full, so cost = loading wait + ride time from distance along the shape at a configurable traffic speed.
- **No OpenTripPlanner.** OTP needs a JVM, an OSM extract and a graph build, all to compute timetable routing that doesn't apply here. This router answers in ~20–90 ms in pure Python with three dependencies.
- **Fares are estimates until you log them.** The official schedule (from 26 Sept 2026) starts at GH¢5.50 but has no distance bands. Logged fares (exact leg) → route override → distance estimate snapped to official fare steps.
- **Uber rates are placeholders.** Put real numbers from your receipts into `overrides.yaml`.

## API
- `GET /api/plan?from_lat&from_lon&to_lat&to_lon` — itineraries + Uber comparison
- `GET /api/search?q=` — saved places + stops
- `POST /api/fares` `{route, from_stop, to_stop, fare}` — log what you paid
- `POST /api/reload` — reload overrides after editing
- `GET /healthz`, `/readyz` — probes

## Deploying (cheap VPS)
CI (`.github/workflows/ci.yml`) lints, tests, builds and pushes `ghcr.io/<you>/legsus`, then smoke-tests the container.
On the VPS: `docker compose up -d`, then put Caddy in front for HTTPS (`legsus.yourdomain.com { reverse_proxy localhost:8000 }`).
The container is non-root and read-only; only `overrides.yaml` is writable (bind-mounted).

**Back up `data/overrides.yaml`** — it's the only data that's yours. Commit it to a private repo.

## Known limits (be honest with yourself)
- Route data is 2017–2019. Expect missing/changed routes; fix them in `overrides.yaml` as you ride.
- Accra/Tema only.
- Walking is straight-line × 1.3, not street routing. Fine for <1 km, misleading across a highway or a gutter.
- Map tiles come from openstreetmap.org — fine for personal use, not for heavy traffic.

## Next steps
1. Ride your 5 regular routes and log fares + boarding notes.
2. Add `routes:` entries for lines you know are wrong or missing.
3. Add your own routes as GTFS rows (or a small `custom_routes` loader).
4. Swap straight-line walking for OSRM foot routing if walks start lying to you.
