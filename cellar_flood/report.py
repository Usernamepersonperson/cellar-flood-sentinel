"""Run the full offline evaluation and write docs/results.json plus the figures."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import numpy as np

from . import evaluate as ev
from .battery import BatteryModel
from .fleet import simulate_fleet
from .risk import Thresholds
from .sim import CellarParams

HOOK_SITE = "lug-palazzo-civico"  # figure example; chosen for a class-3 site with a documented heavy-rain day


def _clean(o):
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items() if k not in ("score", "sim")}
    if isinstance(o, list):
        return [_clean(v) for v in o]
    if isinstance(o, (np.floating, float)):
        return None if np.isnan(o) else float(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    return o


def build(root: Path = ev.ROOT, with_figures: bool = True, hook_site: str = HOOK_SITE) -> dict:
    th = Thresholds()
    cache: dict = {}
    sites = ev.load_sites(root)
    storms = ev.storm_table(root, th)
    seasons = {m: ev.season_stats(root, th, mode=m, cache=cache) for m in ("observed", "persistence", "oracle")}
    sens = ev.sensitivity(root)
    sweep = [r for m in ("observed", "persistence", "oracle")
             for r in ev.threshold_sweep(root, m, levels=(0.5, 0.7, 1.0, 1.3), cache=cache)]
    batt = {}
    for sf in (7, 9, 10):
        m = BatteryModel(sf=sf)
        batt[f"SF{sf}"] = {"airtime_ms": m.mah_per_day()["airtime_s"] * 1000, "mah_per_day": m.mah_per_day()["total"],
                           "life_years": m.life_years()}
    fleet = asdict(simulate_fleet())
    res = {
        "thresholds": {"pre_alert": th.pre_alert, "ref_depth_mm": th.ref_depth_mm, "data_latency_min": ev.DATA_LATENCY_MIN},
        "sites": [{"id": s.id, "name": s.name, "station": s.station, "hazard_class": s.hazard_class,
                   "shares": s.shares} for s in sites],
        "storms": storms,
        "seasons": seasons,
        "sensitivity": sens,
        "threshold_sweep": sweep,
        "battery": batt,
        "fleet": fleet,
    }
    res = _clean(res)
    (root / "docs").mkdir(exist_ok=True)
    (root / "docs" / "results.json").write_text(json.dumps(res, indent=1) + "\n", encoding="utf-8")
    if with_figures:
        make_figures(root, res, th, hook_site)
    return res


def make_figures(root: Path, res: dict, th: Thresholds, hook_site: str = HOOK_SITE) -> None:
    from . import figures
    from .figures import AMBER, GREEN, VIOLET

    cfg = ev.load_config(root)
    sites = {s.id: s for s in ev.load_sites(root)}
    s = sites[hook_site]
    storm = next(x for x in cfg["storms"] if x["station"] == s.station)
    rain = ev.load_rain(s.station, root)
    win = ev.event_window(rain, storm["date"])
    p = CellarParams(s.hazard_class, s.sill_mm)
    base = ev.analyse_window(win, p, th, "observed")
    pers = ev.analyse_window(win, p, th, "persistence")
    ora = ev.analyse_window(win, p, th, "oracle")
    label = f"{s.name} | storm {storm['date']}"
    marks = [
        (ora["t_pre"], GREEN, "--", "pre-alert, 30-min rain nowcast (perfect-forecast bound)"),
        (base["t_pre"], AMBER, "--", "pre-alert from observed rain only"),
    ]
    figures.post_figure(
        root / "docs" / "post-figure.png", ora, win, label, s.hazard_class,
        "A rain nowcast can warn before the cellar sensor does",
        f"open data: MeteoSwiss rain, {s.station}, {storm['date']} | water level simulated",
        marks,
    )
    marks.insert(1, (pers["t_pre"], VIOLET, ":", "pre-alert, 30-min extrapolation of observed rain"))
    figures.main_figure(root / "docs" / "figure.png", ora, win, label, s.hazard_class, marks, res["storms"],
                        res["seasons"]["persistence"])
