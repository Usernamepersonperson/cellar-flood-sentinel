"""Risk score: site exposure x rain intensity (intensity-duration), and pre-alert episodes.

risk = exposure_factor * rain_index,   rain_index = max over D of (rain depth in D minutes / reference depth(D)).
Reference depths are tunable engineering thresholds (not return periods). Observed rain gives a
lower bound on warning time; adding a nowcast (``forecast``) pushes the trigger earlier.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

HAZARD_FACTOR = {0: 0.35, 1: 0.6, 2: 0.8, 3: 1.0}
# Reference depth (mm) per duration (min): rain at this depth in this time scores index 1.0.
REF_DEPTH_MM = {10: 8.0, 30: 15.0, 60: 22.0}

WATCH, PRE_ALERT, SEVERE = 0.75, 1.0, 1.5


@dataclass(frozen=True)
class SiteExposure:
    hazard_class: int
    sill_mm: float = 100.0  # height of the lowest basement opening above the surrounding ground

    @property
    def opening_factor(self) -> float:
        return float(np.clip(1.0 + (100.0 - self.sill_mm) / 200.0, 0.6, 1.5))

    @property
    def factor(self) -> float:
        if self.hazard_class not in HAZARD_FACTOR:
            raise ValueError(f"hazard class must be 0-3, got {self.hazard_class}")
        return HAZARD_FACTOR[self.hazard_class] * self.opening_factor


@dataclass
class Thresholds:
    ref_depth_mm: dict = field(default_factory=lambda: dict(REF_DEPTH_MM))
    watch: float = WATCH
    pre_alert: float = PRE_ALERT
    severe: float = SEVERE


def _rolling_sum(x: np.ndarray, n: int) -> np.ndarray:
    """Trailing sum over n samples; at the start the window is shorter (same as min_periods=1)."""
    if n <= 0:
        return np.zeros_like(x)
    c = np.cumsum(x)
    out = c.copy()
    out[n:] = c[n:] - c[:-n]
    return out


def rain_index(mm10: pd.Series, ref_depth_mm: dict | None = None) -> pd.Series:
    """Intensity-duration index on a 10-min grid; NaN is treated as 0 mm."""
    ref = ref_depth_mm or REF_DEPTH_MM
    x = mm10.fillna(0.0).to_numpy(dtype=float)
    out = np.zeros_like(x)
    for dur, depth in ref.items():
        out = np.maximum(out, _rolling_sum(x, max(1, int(dur // 10))) / depth)
    return pd.Series(out, index=mm10.index)


def risk_score(mm10: pd.Series, site: SiteExposure, th: Thresholds | None = None) -> pd.Series:
    th = th or Thresholds()
    return site.factor * rain_index(mm10, th.ref_depth_mm)


def lookahead_index(idx: pd.Series, horizon_min: int) -> pd.Series:
    """ORACLE bound: index a perfect nowcast would show, i.e. the max index within the next horizon."""
    n = max(1, horizon_min // 10)
    return idx[::-1].rolling(n + 1, min_periods=1).max()[::-1]


def persistence_index(mm10: pd.Series, horizon_min: int = 30, ref_depth_mm: dict | None = None) -> pd.Series:
    """Causal nowcast: assume the last 10-min rate persists for the next ``horizon_min``.

    At time t the depth over each duration D is the observed rain in the last D-k min plus k buckets at the
    current rate, for k = 0..horizon/10; the index is the maximum over D and k. Uses only data up to t.
    """
    ref = ref_depth_mm or REF_DEPTH_MM
    x = mm10.fillna(0.0).to_numpy(dtype=float)
    steps = max(0, horizon_min // 10)
    out = np.zeros_like(x)
    for dur, depth in ref.items():
        n = max(1, int(dur // 10))
        for k in range(steps + 1):
            kk = min(k, n)
            out = np.maximum(out, (_rolling_sum(x, n - kk) + kk * x) / depth)
    return pd.Series(out, index=mm10.index)


def index_with_forecast(observed: pd.Series, forecast_mm10: pd.Series, ref_depth_mm: dict | None = None) -> float:
    """Live use: rain index at the last observed time when a real forecast series is appended."""
    combined = pd.concat([observed.fillna(0.0), forecast_mm10.fillna(0.0)])
    combined = combined[~combined.index.duplicated()].sort_index()
    idx = rain_index(combined, ref_depth_mm)
    return float(idx[observed.index[-1] :].max())


def level_of(score: float, th: Thresholds | None = None) -> str:
    th = th or Thresholds()
    if score >= th.severe:
        return "severe"
    if score >= th.pre_alert:
        return "pre-alert"
    if score >= th.watch:
        return "watch"
    return "ok"


def episodes(score: pd.Series, threshold: float, merge_gap_min: int = 180) -> list[dict]:
    """Contiguous spells where score >= threshold, merged if the gap is below ``merge_gap_min``."""
    above = score >= threshold
    out: list[dict] = []
    for t in score.index[above.to_numpy()]:
        if out and (t - out[-1]["end"]) <= pd.Timedelta(minutes=merge_gap_min):
            out[-1]["end"] = t
            out[-1]["peak"] = max(out[-1]["peak"], float(score[t]))
        else:
            out.append({"start": t, "end": t, "peak": float(score[t])})
    return out
