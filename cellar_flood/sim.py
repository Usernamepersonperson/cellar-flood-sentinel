"""Simulated cellar water level driven by real 10-min rain (SIMULATED sensor side).

Model (1-min step, rain spread uniformly inside each 10-min bucket):
  excess  = max(0, i - capacity)                  # i in mm/min; capacity = sewer + infiltration
  S'      = excess - S / tau                       # linear reservoir of surface runoff
  depth   = D_class * S / S_ref                    # D_class: map depth class, S_ref: reference storm
  inflow  = k_in * max(0, depth - sill) / 100      # mm of cellar level per minute
  L'      = inflow - pump_rate (pump on above on_mm, off below off_mm; may be failed)
The hazard map's modelled depth for its reference event anchors the class depth; the rest are assumptions.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .hazard import CLASS_DEPTH_MM


@dataclass(frozen=True)
class CellarParams:
    hazard_class: int
    sill_mm: float = 100.0
    tau_min: float = 10.0
    capacity_mm10: float = 2.0  # drainage capacity, mm per 10 min (12 mm/h)
    ref_rate_mm_h: float = 80.0  # reference storm of the hazard map (assumed 80 mm/h)
    k_in: float = 2.0  # mm of cellar level per minute per 100 mm of head over the sill
    pump_mm_min: float = 1.5
    pump_on_mm: float = 30.0
    pump_off_mm: float = 10.0
    pump_ok: bool = True


def _s_ref(p: CellarParams) -> float:
    return (p.ref_rate_mm_h / 60.0 - p.capacity_mm10 / 10.0) * p.tau_min


def simulate(mm10: pd.Series, p: CellarParams, pad_min: int = 240) -> pd.DataFrame:
    """Per-minute DataFrame with columns rain_mm_min, depth_mm, level_mm, pump_on, pump_current_a."""
    x = mm10.fillna(0.0)
    if x.empty:
        raise ValueError("empty rain series")
    end = x.index[-1] + pd.Timedelta(minutes=pad_min)
    start = x.index[0] - pd.Timedelta(minutes=10)
    idx = pd.date_range(start + pd.Timedelta(minutes=1), end, freq="1min")
    rate = pd.Series(x.reindex(idx.ceil("10min")).to_numpy() / 10.0, index=idx).fillna(0.0).to_numpy()
    d_class = CLASS_DEPTH_MM[p.hazard_class]
    s_ref = _s_ref(p)
    cap = p.capacity_mm10 / 10.0
    s = lvl = 0.0
    pump = False
    rows = []
    for r in rate:
        s += max(0.0, r - cap) - s / p.tau_min
        s = max(s, 0.0)
        depth = d_class * s / s_ref
        inflow = p.k_in * max(0.0, depth - p.sill_mm) / 100.0
        if p.pump_ok:
            if lvl > p.pump_on_mm:
                pump = True
            elif lvl < p.pump_off_mm:
                pump = False
        else:
            pump = False
        lvl = max(0.0, lvl + inflow - (p.pump_mm_min if pump else 0.0))
        rows.append((r, depth, lvl, pump, 4.2 if pump else 0.0))
    return pd.DataFrame(rows, index=idx, columns=["rain_mm_min", "depth_mm", "level_mm", "pump_on", "pump_current_a"])


def first_time_above(level: pd.Series, mm: float):
    hit = level[level >= mm]
    return hit.index[0] if len(hit) else None
