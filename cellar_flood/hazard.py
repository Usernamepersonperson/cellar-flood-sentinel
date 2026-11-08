"""Surface-runoff hazard lookup from the FOEN/BAFU overland-flow map.

Layer: ``ch.bafu.gefaehrdungskarte-oberflaechenabfluss`` (published on geo.admin.ch). The layer is
only exposed as a WMS raster, so the class is read from the rendered pixels (exact legend colours).
Legend classes: 1 = modelled water depth 0-0.1 m, 2 = 0.1-0.25 m, 3 = > 0.25 m (event > 100 y).
Source: FOEN/BAFU, geodata via geo.admin.ch (free OGD, source citation required).
"""

from __future__ import annotations

import io
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx
import numpy as np
from PIL import Image

from .geo import wgs84_to_lv95

WMS_URL = "https://wms.geo.admin.ch/"
LAYER = "ch.bafu.gefaehrdungskarte-oberflaechenabfluss"
TILE_HALF_M = 60
TILE_PX = 120  # 1 m per pixel

# Exact legend colours (RGB) -> class code. 9 = water body, 8 = glacier (not a runoff class).
LEGEND = {(255, 189, 255): 1, (223, 115, 255): 2, (132, 0, 168): 3, (0, 166, 255): 9, (222, 245, 255): 8}
CLASS_DEPTH_MM = {0: 0, 1: 50, 2: 175, 3: 300}  # representative modelled depth per class (class mid)
CLASS_LABEL = {
    0: "no mapped runoff",
    1: "class 1: 0-0.1 m",
    2: "class 2: 0.1-0.25 m",
    3: "class 3: >0.25 m",
}


@dataclass
class Exposure:
    hazard_class: int  # worst class covering >= min_share of the disc around the building
    point_class: int  # class exactly at the coordinate
    shares: dict  # class -> share of the disc (0..1)
    radius_m: float

    def to_dict(self) -> dict:
        return asdict(self)


def classify_rgba(arr: np.ndarray) -> np.ndarray:
    """Map an RGBA array (H, W, 4) to class codes (0 = transparent / no hazard)."""
    out = np.zeros(arr.shape[:2], dtype=np.uint8)
    opaque = arr[..., 3] > 200
    for rgb, code in LEGEND.items():
        out[(arr[..., :3] == np.array(rgb, dtype=arr.dtype)).all(-1) & opaque] = code
    return out


def exposure_from_classes(cls: np.ndarray, radius_m: float = 20.0, min_share: float = 0.02) -> Exposure:
    """Exposure of a building at the tile centre: worst runoff class with >= ``min_share`` of the disc."""
    h, w = cls.shape
    yy, xx = np.mgrid[:h, :w]
    cy, cx = (h - 1) / 2, (w - 1) / 2
    disc = (xx - cx) ** 2 + (yy - cy) ** 2 <= radius_m**2
    n = int(disc.sum())
    shares = {k: float((cls[disc] == k).sum()) / n for k in (1, 2, 3, 9)}
    worst = 0
    for k in (1, 2, 3):
        if shares[k] >= min_share:
            worst = k
    point = int(cls[round(cy), round(cx)])
    return Exposure(worst, point if point in (0, 1, 2, 3) else 0, shares, radius_m)


def wms_url(east: float, north: float, half: float = TILE_HALF_M, px: int = TILE_PX) -> str:
    bbox = f"{east - half},{north - half},{east + half},{north + half}"
    return (
        f"{WMS_URL}?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetMap&LAYERS={LAYER}&STYLES=&CRS=EPSG:2056"
        f"&BBOX={bbox}&WIDTH={px}&HEIGHT={px}&FORMAT=image/png&TRANSPARENT=TRUE"
    )


def fetch_tile(lat: float, lon: float, client: httpx.Client | None = None) -> bytes:
    east, north = wgs84_to_lv95(lat, lon)
    c = client or httpx.Client(timeout=30)
    r = c.get(wms_url(east, north))
    r.raise_for_status()
    if not r.headers.get("content-type", "").startswith("image/png"):
        raise ValueError(f"unexpected WMS response: {r.headers.get('content-type')}")
    return r.content


def tile_classes(png: bytes) -> np.ndarray:
    img = Image.open(io.BytesIO(png)).convert("RGBA")
    return classify_rgba(np.array(img))


def lookup(
    lat: float,
    lon: float,
    cache_file: Path | None = None,
    offline: bool = False,
    radius_m: float = 20.0,
    client: httpx.Client | None = None,
) -> Exposure:
    """Exposure for a coordinate; reads/writes a PNG tile cache so tests and demo run offline."""
    if cache_file is not None and cache_file.exists():
        png = cache_file.read_bytes()
    elif offline:
        raise FileNotFoundError(f"no cached hazard tile: {cache_file}")
    else:
        png = fetch_tile(lat, lon, client)
        if cache_file is not None:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_bytes(png)
    return exposure_from_classes(tile_classes(png), radius_m)
