"""Small geo helpers. No heavy GIS deps on purpose — this runs on a $5 VPS."""
from __future__ import annotations

import math
from collections import defaultdict

EARTH_R_M = 6_371_000.0


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R_M * math.asin(math.sqrt(a))


class GridIndex:
    """Uniform grid spatial index for 'stops within R metres' queries.

    ~0.005 deg cells (~550 m at Accra's latitude). Good enough for a few
    thousand points and keeps the dependency list empty.
    """

    def __init__(self, cell_deg: float = 0.005):
        self.cell = cell_deg
        self.cells: dict[tuple[int, int], list[tuple[str, float, float]]] = defaultdict(list)

    def _key(self, lat: float, lon: float) -> tuple[int, int]:
        return (int(math.floor(lat / self.cell)), int(math.floor(lon / self.cell)))

    def add(self, item_id: str, lat: float, lon: float) -> None:
        self.cells[self._key(lat, lon)].append((item_id, lat, lon))

    def within(self, lat: float, lon: float, radius_m: float) -> list[tuple[str, float]]:
        span = int(math.ceil(radius_m / 111_000 / self.cell)) + 1
        ky, kx = self._key(lat, lon)
        out: list[tuple[str, float]] = []
        for dy in range(-span, span + 1):
            for dx in range(-span, span + 1):
                for item_id, ilat, ilon in self.cells.get((ky + dy, kx + dx), ()):
                    d = haversine_m(lat, lon, ilat, ilon)
                    if d <= radius_m:
                        out.append((item_id, d))
        out.sort(key=lambda t: t[1])
        return out
