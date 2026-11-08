"""Measured evaluation on real MeteoSwiss 10-min rain; the cellar water level is SIMULATED."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
import pandas as pd

from . import hazard, meteo
from .risk import SiteExposure, Thresholds, lookahead_index, persistence_index, rain_index
from .sim import CellarParams, first_time_above, simulate

ROOT = Path(__file__).resolve().parent.parent
WET_MM = 3.0  # sensor sees water on the floor
ALARM_MM = 20.0
DATA_LATENCY_MIN = 10  # a 10-min bucket is usable only after it closes (+ publication delay, assumed)


@dataclass
class Site:
    id: str
    name: str
    station: str
    hazard_class: int
    sill_mm: float
    lat: float
    lon: float
    shares: dict


def load_config(root: Path = ROOT) -> dict:
    return json.loads((root / "data" / "sites.json").read_text(encoding="utf-8"))


def load_sites(root: Path = ROOT, offline: bool = True) -> list[Site]:
    cfg = load_config(root)
    out = []
    for s in cfg["sites"]:
        e = hazard.lookup(s["lat"], s["lon"], root / "data" / "hazard" / f"{s['id']}.png", offline=offline)
        out.append(Site(s["id"], s["name"], s["station"], e.hazard_class, s["sill_mm"], s["lat"], s["lon"], e.shares))
    return out


def load_rain(station: str, root: Path = ROOT) -> pd.Series:
    return meteo.load_series(root / "data" / "precip" / f"{station}.csv.gz")


def wet_spells(mm10: pd.Series, min_mm: float = 0.5, merge_gap_min: int = 180, pad_min: int = 240) -> list[tuple]:
    """(start, end) windows of rain, merged across short gaps, padded after for the water to arrive."""
    x = mm10.fillna(0.0)
    wet = x.index[(x >= min_mm).to_numpy()]
    spells: list[list] = []
    for t in wet:
        if spells and (t - spells[-1][1]) <= pd.Timedelta(minutes=merge_gap_min):
            spells[-1][1] = t
        else:
            spells.append([t, t])
    return [(a, b) for a, b in spells]


def mode_index(window: pd.Series, th: Thresholds, mode: str, horizon_min: int = 30) -> pd.Series:
    """Rain index of a trigger mode. Independent of exposure, so it can be cached per rain spell."""
    if mode == "observed":
        return rain_index(window, th.ref_depth_mm)
    if mode == "persistence":
        return persistence_index(window, horizon_min, th.ref_depth_mm)
    if mode == "oracle":
        return lookahead_index(rain_index(window, th.ref_depth_mm), horizon_min)
    raise ValueError(f"unknown mode {mode}")


def trigger_from_index(idx: pd.Series, site: SiteExposure, th: Thresholds, latency_min: int = DATA_LATENCY_MIN):
    score = site.factor * idx
    hit = score[score >= th.pre_alert]
    if hit.empty:
        return None, score
    return hit.index[0] + pd.Timedelta(minutes=latency_min), score


def trigger_time(
    window: pd.Series,
    site: SiteExposure,
    th: Thresholds,
    mode: str = "observed",
    horizon_min: int = 30,
    latency_min: int = DATA_LATENCY_MIN,
):
    """First time the pre-alert reaches the user (bucket end + latency), or None.

    mode: "observed" (rain so far), "persistence" (causal 30-min extrapolation) or
    "oracle" (perfect nowcast bound, not achievable).
    """
    return trigger_from_index(mode_index(window, th, mode, horizon_min), site, th, latency_min)


def analyse_window(
    window: pd.Series, p: CellarParams, th: Thresholds, mode: str = "observed", latency_min: int = DATA_LATENCY_MIN
) -> dict:
    site = SiteExposure(p.hazard_class, p.sill_mm)
    t_pre, score = trigger_time(window, site, th, mode, latency_min=latency_min)
    sim = simulate(window, p)
    t_wet = first_time_above(sim["level_mm"], WET_MM)
    t_alarm = first_time_above(sim["level_mm"], ALARM_MM)
    lead = None
    if t_pre is not None and t_wet is not None:
        lead = (t_wet - t_pre).total_seconds() / 60.0
    return {
        "t_pre": t_pre,
        "t_wet": t_wet,
        "t_alarm": t_alarm,
        "lead_min": lead,
        "peak_level_mm": float(sim["level_mm"].max()),
        "peak_score": float(score.max()),
        "score": score,
        "sim": sim,
    }


def heaviest_hour(mm10: pd.Series, date: str, hours: int = 30) -> tuple[pd.Timestamp, float]:
    w = mm10[pd.Timestamp(date) : pd.Timestamp(date) + pd.Timedelta(hours=hours)].fillna(0.0)
    r = w.rolling(6).sum()
    return r.idxmax(), float(r.max())


def event_window(mm10: pd.Series, date: str, before_h: int = 6, after_h: int = 3) -> pd.Series:
    peak, _ = heaviest_hour(mm10, date)
    return mm10[peak - pd.Timedelta(hours=before_h) : peak + pd.Timedelta(hours=after_h)]


def storm_table(root: Path = ROOT, th: Thresholds | None = None, tau: float = 10.0) -> list[dict]:
    th = th or Thresholds()
    cfg = load_config(root)
    sites = load_sites(root)
    rows = []
    for storm in cfg["storms"]:
        rain = load_rain(storm["station"], root)
        peak, hour_mm = heaviest_hour(rain, storm["date"])
        win = event_window(rain, storm["date"])
        for s in [x for x in sites if x.station == storm["station"]]:
            p = CellarParams(s.hazard_class, s.sill_mm, tau_min=tau)
            a = analyse_window(win, p, th)
            c = analyse_window(win, p, th, "persistence")
            b = analyse_window(win, p, th, "oracle")
            rows.append(
                {
                    "site": s.id,
                    "station": s.station,
                    "date": storm["date"],
                    "peak_1h_end_utc": f"{peak:%Y-%m-%d %H:%M}",
                    "rain_1h_mm": hour_mm,
                    "max_10min_mm": float(win.max()),
                    "hazard_class": s.hazard_class,
                    "peak_score": a["peak_score"],
                    "pre_alert_utc": None if a["t_pre"] is None else f"{a['t_pre']:%H:%M}",
                    "sensor_wet_utc": None if a["t_wet"] is None else f"{a['t_wet']:%H:%M}",
                    "lead_min": a["lead_min"],
                    "lead_min_persistence_30": c["lead_min"],
                    "lead_min_oracle_30": b["lead_min"],
                    "peak_level_mm": a["peak_level_mm"],
                }
            )
    return rows


def spell_waters(root: Path, site: Site, tau: float = 10.0, sill_mm: float | None = None) -> tuple:
    """Per rain spell: (window, first simulated water time). Independent of alert thresholds, so cacheable."""
    rain = load_rain(site.station, root)
    p = CellarParams(site.hazard_class, sill_mm if sill_mm is not None else site.sill_mm, tau_min=tau)
    out = []
    for a, b in wet_spells(rain):
        win = rain[a - pd.Timedelta(minutes=10) : b]
        sim = simulate(win, p)
        out.append([win, first_time_above(sim["level_mm"], WET_MM), {}])
    return p, rain, out


def season_stats(
    root: Path = ROOT,
    th: Thresholds | None = None,
    tau: float = 10.0,
    sill_mm: float | None = None,
    mode: str = "observed",
    cache: dict | None = None,
    only: set | None = None,
) -> list[dict]:
    """Per site, all Apr-Oct rain spells 2020-2026: pre-alert vs simulated water arrival."""
    th = th or Thresholds()
    cache = cache if cache is not None else {}
    out = []
    for s in [x for x in load_sites(root) if only is None or x.id in only]:
        key = (s.id, tau, sill_mm)
        if key not in cache:
            cache[key] = spell_waters(root, s, tau, sill_mm)
        p, rain, spells = cache[key]
        site = SiteExposure(p.hazard_class, p.sill_mm)
        tp = fp = fn = late = 0
        leads: list[float] = []
        for win, t_wet, idx_cache in spells:
            if mode not in idx_cache:
                idx_cache[mode] = mode_index(win, th, mode)
            t_pre, _ = trigger_from_index(idx_cache[mode], site, th)
            if t_pre is not None and t_wet is None:
                fp += 1
            elif t_pre is None and t_wet is not None:
                fn += 1
            elif t_pre is not None and t_wet is not None:
                lead = (t_wet - t_pre).total_seconds() / 60
                leads.append(lead)
                if lead > 0:
                    tp += 1
                else:
                    late += 1
        n_seasons = len(set(rain.index.year))
        n_alert = tp + fp + late
        out.append(
            {
                "site": s.id,
                "hazard_class": s.hazard_class,
                "station": s.station,
                "seasons": n_seasons,
                "rain_spells": len(spells),
                "pre_alerts": n_alert,
                "pre_alerts_per_season": n_alert / n_seasons,
                "in_time": tp,
                "followed_by_simulated_water": tp + late,
                "water_events": tp + late + fn,
                "no_water_in_model": fp,
                "false_pre_alert_share": (fp / n_alert) if n_alert else 0.0,
                "water_without_pre_alert": fn,
                "late_pre_alerts": late,
                "median_lead_min": float(np.median(leads)) if leads else None,
                "data_missing_share": float(rain.isna().mean()),
            }
        )
    return out


def sensitivity(root: Path = ROOT) -> list[dict]:
    """Lead time of the storm/site pairs across assumed cellar parameters (tau, sill), per trigger mode."""
    th = Thresholds()
    cfg = load_config(root)
    sites = load_sites(root)
    rows = []
    for tau in (5.0, 10.0, 20.0):
        for sill in (50.0, 100.0, 150.0):
            leads: dict[str, list[float]] = {"observed": [], "persistence": [], "oracle": []}
            floods = 0
            for storm in cfg["storms"]:
                rain = load_rain(storm["station"], root)
                win = event_window(rain, storm["date"])
                for s in [x for x in sites if x.station == storm["station"]]:
                    p = CellarParams(s.hazard_class, sill, tau_min=tau)
                    for mode, bucket in leads.items():
                        r = analyse_window(win, p, th, mode)
                        if mode == "observed" and r["t_wet"] is not None:
                            floods += 1
                        if r["lead_min"] is not None:
                            bucket.append(r["lead_min"])
            row = {"tau_min": tau, "sill_mm": sill, "storm_site_pairs_with_water": floods}
            for mode, v in leads.items():
                row[f"median_lead_{mode}_min"] = float(np.median(v)) if v else None
            rows.append(row)
    return rows


def threshold_sweep(
    root: Path = ROOT, mode: str = "observed", levels=(0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 1.0), cache: dict | None = None
) -> list[dict]:
    """Trade-off: pre-alert threshold vs lead on the storms and false pre-alerts over all seasons."""
    cfg = load_config(root)
    sites = load_sites(root)
    rows = []
    cache = cache if cache is not None else {}
    for lvl in levels:
        th = Thresholds(pre_alert=lvl)
        leads = []
        for storm in cfg["storms"]:
            rain = load_rain(storm["station"], root)
            win = event_window(rain, storm["date"])
            for s in [x for x in sites if x.station == storm["station"]]:
                r = analyse_window(win, CellarParams(s.hazard_class, s.sill_mm), th, mode)
                if r["lead_min"] is not None:
                    leads.append(r["lead_min"])
        ss = season_stats(root, th, mode=mode, cache=cache)
        alerts = sum(x["pre_alerts"] for x in ss)
        fps = sum(x["no_water_in_model"] for x in ss)
        events = sum(x["water_events"] for x in ss)
        in_time = sum(x["in_time"] for x in ss)
        rows.append(
            {
                "pre_alert_threshold": lvl,
                "mode": mode,
                "storm_median_lead_min": float(np.median(leads)) if leads else None,
                "storm_min_lead_min": float(min(leads)) if leads else None,
                "pre_alerts_per_site_season": alerts / (len(ss) * ss[0]["seasons"]),
                "false_pre_alert_share": fps / alerts if alerts else 0.0,
                "water_events": events,
                "water_events_warned_in_time": in_time,
            }
        )
    return rows


def with_pump_failure(p: CellarParams) -> CellarParams:
    return replace(p, pump_ok=False)
