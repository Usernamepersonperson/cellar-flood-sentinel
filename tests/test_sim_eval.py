import pandas as pd
import pytest

from cellar_flood import evaluate as ev
from cellar_flood.risk import SiteExposure, Thresholds
from cellar_flood.sim import CellarParams, first_time_above, simulate


def _storm(vals, start="2023-06-29 20:00"):
    return pd.Series(vals, index=pd.date_range(start, periods=len(vals), freq="10min"), dtype=float)


# ---------- simulation ----------
def test_no_rain_no_water():
    sim = simulate(_storm([0, 0, 0]), CellarParams(3))
    assert sim["level_mm"].max() == 0.0


def test_class_zero_never_floods():
    sim = simulate(_storm([20, 20, 20, 20]), CellarParams(0))
    assert sim["level_mm"].max() == 0.0


def test_heavy_rain_floods_class_3_and_not_light_rain():
    assert simulate(_storm([20, 20, 20, 20]), CellarParams(3))["level_mm"].max() > 20
    assert simulate(_storm([1, 1, 1, 1]), CellarParams(3))["level_mm"].max() == 0.0


def test_higher_sill_lowers_peak():
    s = _storm([15, 15, 15, 15])
    low = simulate(s, CellarParams(3, sill_mm=50))["level_mm"].max()
    high = simulate(s, CellarParams(3, sill_mm=150))["level_mm"].max()
    assert low > high


def test_failed_pump_gives_higher_level():
    s = _storm([20, 20, 20, 20, 5, 0])
    ok = simulate(s, CellarParams(3))["level_mm"].max()
    bad = simulate(s, CellarParams(3, pump_ok=False))["level_mm"].max()
    assert bad > ok


def test_pump_runs_when_level_high():
    sim = simulate(_storm([20, 20, 20, 20]), CellarParams(3))
    assert sim["pump_on"].any() and (sim.loc[sim["pump_on"], "pump_current_a"] > 0).all()


def test_first_time_above_none_when_never():
    assert first_time_above(pd.Series([0.0, 1.0], index=pd.date_range("2020-01-01", periods=2, freq="1min")), 5) is None


def test_empty_series_rejected():
    with pytest.raises(ValueError):
        simulate(pd.Series(dtype=float), CellarParams(3))


# ---------- evaluation on the shipped real-data cache ----------
def test_sites_load_offline_with_expected_classes():
    classes = {s.id: s.hazard_class for s in ev.load_sites()}
    assert classes["lug-palazzo-civico"] == 3 and classes["zrh-stadthaus"] == 1
    assert set(classes.values()) == {1, 2, 3}


def test_rain_cache_covers_seven_seasons_without_gaps():
    r = ev.load_rain("GVE")
    assert r.index.min().year == 2020 and r.index.max().year == 2026
    assert r.isna().mean() < 0.01 and r.min() >= 0


def test_heaviest_hour_matches_known_archive_value():
    # MeteoSwiss Geneve/Cointrin, 2023-06-29: 35.9 mm in the heaviest hour (read from the 10-min archive)
    peak, mm = ev.heaviest_hour(ev.load_rain("GVE"), "2023-06-29")
    assert mm == pytest.approx(35.9, abs=0.05) and peak.strftime("%H:%M") == "23:50"


def test_wet_spells_merge_and_pad():
    s = _storm([0, 2, 0, 0, 3, 0] + [0] * 30 + [4])
    spells = ev.wet_spells(s, merge_gap_min=60)
    assert len(spells) == 2


def test_trigger_modes_are_ordered_observed_late_persistence_earlier_oracle_earliest():
    s = _storm([0, 0, 4, 8, 12, 18, 10, 2, 0, 0])
    site = SiteExposure(3)
    th = Thresholds()
    t_obs, _ = ev.trigger_time(s, site, th, "observed")
    t_per, _ = ev.trigger_time(s, site, th, "persistence")
    t_ora, _ = ev.trigger_time(s, site, th, "oracle")
    assert t_ora <= t_per <= t_obs
    with pytest.raises(ValueError):
        ev.trigger_time(s, site, th, "magic")


def test_trigger_none_for_dry_series():
    assert ev.trigger_time(_storm([0, 0, 0]), SiteExposure(3), Thresholds())[0] is None


def test_storm_table_control_site_stays_dry_and_oracle_beats_observed():
    rows = ev.storm_table()
    assert len(rows) == 6
    control = next(r for r in rows if r["site"] == "zrh-stadthaus")
    assert control["sensor_wet_utc"] is None
    flooded = [r for r in rows if r["lead_min"] is not None]
    assert len(flooded) >= 4
    for r in flooded:
        assert r["lead_min_oracle_30"] >= r["lead_min_persistence_30"] >= r["lead_min"]


def test_season_stats_single_site_consistent():
    (row,) = ev.season_stats(only={"zrh-stadthaus"})
    assert row["seasons"] == 7 and row["rain_spells"] > 100
    assert row["pre_alerts"] == row["in_time"] + row["no_water_in_model"] + row["late_pre_alerts"]
    assert 0.0 <= row["false_pre_alert_share"] <= 1.0
