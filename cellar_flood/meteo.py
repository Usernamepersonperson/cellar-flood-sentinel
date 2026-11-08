"""Rain data: MeteoSwiss open data (10-min station precipitation) and an Open-Meteo 15-min nowcast adapter.

MeteoSwiss OGD "SwissMetNet" (collection ``ch.meteoschweiz.ogd-smn``, licence CC-BY), parameter
``rre150z0`` = precipitation, ten-minute total in mm. Timestamps are UTC.
Source: MeteoSwiss, https://data.geo.admin.ch/ch.meteoschweiz.ogd-smn/
Forecast: Open-Meteo (https://open-meteo.com, CC-BY 4.0), ``minutely_15`` precipitation, live use only.
"""

from __future__ import annotations

import gzip
from collections.abc import Iterable
from pathlib import Path

import httpx
import pandas as pd

OGD_BASE = "https://data.geo.admin.ch/ch.meteoschweiz.ogd-smn"
PARAM = "rre150z0"
SEASON_MONTHS = (4, 5, 6, 7, 8, 9, 10)


def parse_ogd_lines(lines: Iterable[str]) -> pd.Series:
    """Parse OGD 10-min CSV lines (';' separated) into a UTC-indexed Series of mm per 10 min."""
    it = iter(lines)
    header = next(it).strip().split(";")
    ti, pi = header.index("reference_timestamp"), header.index(PARAM)
    stamps, vals = [], []
    for line in it:
        parts = line.strip().split(";")
        if len(parts) <= max(ti, pi):
            continue
        stamps.append(parts[ti])
        v = parts[pi]
        vals.append(float(v) if v not in ("", "-") else float("nan"))
    idx = pd.to_datetime(stamps, format="%d.%m.%Y %H:%M")
    s = pd.Series(vals, index=idx, name="mm10", dtype=float)
    return s[~s.index.duplicated()].sort_index()


def season_only(s: pd.Series, months: tuple[int, ...] = SEASON_MONTHS) -> pd.Series:
    return s[s.index.month.isin(months)]


def save_series(s: pd.Series, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", newline="\n") as f:
        f.write("t,mm10\n")
        for t, v in s.items():
            f.write(f"{t:%Y-%m-%dT%H:%M},{'' if pd.isna(v) else f'{v:g}'}\n")


def load_series(path: Path) -> pd.Series:
    df = pd.read_csv(path, parse_dates=["t"])
    return pd.Series(df["mm10"].to_numpy(dtype=float), index=pd.DatetimeIndex(df["t"]), name="mm10")


def fetch_station(abbr: str, first_year: int = 2020, client: httpx.Client | None = None) -> pd.Series:
    """Stream historical + recent 10-min files for a station and keep only the Apr-Oct season."""
    a = abbr.lower()
    c = client or httpx.Client(timeout=120, follow_redirects=True)
    urls = [
        f"{OGD_BASE}/{a}/ogd-smn_{a}_t_historical_2020-2029.csv",
        f"{OGD_BASE}/{a}/ogd-smn_{a}_t_recent.csv",
    ]
    parts = []
    for url in urls:
        with c.stream("GET", url) as r:
            r.raise_for_status()
            lines = (ln for ln in r.iter_lines())
            parts.append(parse_ogd_lines(lines))
    s = pd.concat(parts)
    s = s[~s.index.duplicated()].sort_index()
    return season_only(s[s.index.year >= first_year])


def parse_openmeteo(payload: dict) -> pd.Series:
    """Open-Meteo ``minutely_15`` precipitation -> Series of mm per 15 min (UTC)."""
    m = payload["minutely_15"]
    idx = pd.to_datetime(m["time"])
    return pd.Series([float("nan") if v is None else float(v) for v in m["precipitation"]], index=idx, name="mm15")


def fetch_nowcast(lat: float, lon: float, client: httpx.Client | None = None) -> pd.Series:
    c = client or httpx.Client(timeout=30)
    r = c.get(
        "https://api.open-meteo.com/v1/forecast",
        params={"latitude": lat, "longitude": lon, "minutely_15": "precipitation", "forecast_days": 1, "timezone": "UTC"},
    )
    r.raise_for_status()
    return parse_openmeteo(r.json())


def mm15_to_mm10(s15: pd.Series) -> pd.Series:
    """Spread 15-min totals uniformly onto a 10-min grid (rate preserving)."""
    rate = s15 / 15.0
    idx = pd.date_range(s15.index[0], s15.index[-1] + pd.Timedelta(minutes=14), freq="10min")
    r = rate.reindex(pd.date_range(s15.index[0], s15.index[-1] + pd.Timedelta(minutes=14), freq="1min")).ffill()
    return (r.resample("10min").sum()).reindex(idx).rename("mm10")
