# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY backend/requirements.txt ./requirements.txt
RUN pip install -r requirements.txt

COPY backend/app ./backend/app
COPY frontend ./frontend
COPY data/gtfs ./data/gtfs
COPY data/overrides.yaml ./data/overrides.default.yaml

ENV LEGSUS_DATA_DIR=/app/data \
    LEGSUS_GTFS_DIR=/app/data/gtfs \
    LEGSUS_OVERRIDES=/app/state/overrides.yaml \
    LEGSUS_FRONTEND_DIR=/app/frontend

# Non-root; /app/state is the only writable path (mount a volume there)
RUN useradd -r -u 10001 legsus && mkdir -p /app/state && chown legsus /app/state
COPY --chmod=755 <<'SH' /app/entrypoint.sh
#!/bin/sh
set -e
[ -f "$LEGSUS_OVERRIDES" ] || cp /app/data/overrides.default.yaml "$LEGSUS_OVERRIDES"
exec uvicorn backend.app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers
SH
USER legsus
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s \
  CMD python -c "import urllib.request,os;urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/readyz')" || exit 1
ENTRYPOINT ["/app/entrypoint.sh"]
