"""Coordinate conversion WGS84 -> Swiss LV95 (swisstopo approximate formulas, about 1 m accuracy)."""

from __future__ import annotations

import math


def wgs84_to_lv95(lat: float, lon: float) -> tuple[float, float]:
    """Return (east, north) in EPSG:2056 for a WGS84 latitude/longitude in degrees."""
    if not (45.0 <= lat <= 48.5 and 5.5 <= lon <= 11.0):
        raise ValueError(f"coordinate outside Switzerland: {lat}, {lon}")
    p = (lat * 3600 - 169028.66) / 10000
    lam = (lon * 3600 - 26782.5) / 10000
    east = 2600072.37 + 211455.93 * lam - 10938.51 * lam * p - 0.36 * lam * p**2 - 44.54 * lam**3
    north = (
        1200147.07 + 308807.95 * p + 3745.25 * lam**2 + 76.63 * p**2 - 194.56 * lam**2 * p + 119.79 * p**3
    )
    return east, north


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0088
    a = math.sin(math.radians(lat2 - lat1) / 2) ** 2
    a += math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))
