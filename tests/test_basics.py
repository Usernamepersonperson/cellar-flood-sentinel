import io

import httpx
import numpy as np
import pandas as pd
import pytest
from PIL import Image

from cellar_flood import hazard, meteo
from cellar_flood.battery import BatteryModel, airtime_s
from cellar_flood.geo import haversine_km, wgs84_to_lv95
from cellar_flood.risk import (
    SiteExposure,
    episodes,
    index_with_forecast,
    level_of,
    lookahead_index,
    persistence_index,
    rain_index,
    risk_score,
)


# ---------- geo ----------
def test_lv95_matches_station_metadata():
    # MeteoSwiss metadata for Bern / Zollikofen: 46.990744, 7.464061 -> 2601934, 1204410
    e, n = wgs84_to_lv95(46.990744, 7.464061)
    assert abs(e - 2601934) < 3 and abs(n - 1204410) < 3


def test_lv95_outside_switzerland_rejected():
    with pytest.raises(ValueError):
        wgs84_to_lv95(52.5, 13.4)


def test_haversine_known_distance():
    assert haversine_km(47.0, 8.0, 47.0, 8.0) == 0
    assert 150 < haversine_km(46.004, 8.960, 46.991, 7.464) < 170  # Lugano - Bern, roughly 158 km


# ---------- hazard ----------
def _rgba(w=40, h=40):
    return np.zeros((h, w, 4), dtype=np.uint8)


def test_classify_exact_legend_colours():
    a = _rgba(5, 1)
    a[0, 0] = (255, 189, 255, 255)
    a[0, 1] = (223, 115, 255, 255)
    a[0, 2] = (132, 0, 168, 255)
    a[0, 3] = (0, 166, 255, 255)
    a[0, 4] = (255, 189, 255, 2)  # near-transparent shading must be ignored
    assert hazard.classify_rgba(a)[0].tolist() == [1, 2, 3, 9, 0]


def test_exposure_empty_tile_is_class_zero():
    e = hazard.exposure_from_classes(np.zeros((40, 40), dtype=np.uint8))
    assert e.hazard_class == 0 and e.point_class == 0


def test_exposure_takes_worst_class_above_min_share():
    cls = np.zeros((41, 41), dtype=np.uint8)
    cls[:, :] = 1
    cls[20, 15:30] = 3  # a thin line of class 3 through the centre
    e = hazard.exposure_from_classes(cls, radius_m=15, min_share=0.01)
    assert e.hazard_class == 3
    e2 = hazard.exposure_from_classes(cls, radius_m=15, min_share=0.2)
    assert e2.hazard_class == 1


def test_exposure_ignores_water_body_as_hazard():
    cls = np.full((41, 41), 9, dtype=np.uint8)
    e = hazard.exposure_from_classes(cls)
    assert e.hazard_class == 0 and e.shares[9] == 1.0 and e.point_class == 0


def test_wms_url_has_layer_and_bbox():
    u = hazard.wms_url(2683000, 1247000)
    assert hazard.LAYER in u and "BBOX=2682940,1246940,2683060,1247060" in u and "EPSG:2056" in u


def _png(color):
    buf = io.BytesIO()
    Image.new("RGBA", (120, 120), color).save(buf, format="PNG")
    return buf.getvalue()


def test_fetch_tile_and_lookup_cache(tmp_path):
    calls = []

    def handler(req):
        calls.append(str(req.url))
        return httpx.Response(200, content=_png((132, 0, 168, 255)), headers={"content-type": "image/png"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    cache = tmp_path / "t.png"
    e = hazard.lookup(46.2, 6.15, cache, client=client)
    assert e.hazard_class == 3 and cache.exists()
    e2 = hazard.lookup(46.2, 6.15, cache, client=client)  # second call served from cache
    assert e2.hazard_class == 3 and len(calls) == 1


def test_fetch_tile_rejects_non_png():
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, text="<xml/>", headers={"content-type": "text/xml"})))
    with pytest.raises(ValueError):
        hazard.fetch_tile(46.2, 6.15, client)


def test_offline_without_cache_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        hazard.lookup(46.2, 6.15, tmp_path / "missing.png", offline=True)


# ---------- meteo ----------
OGD = [
    "station_abbr;reference_timestamp;tre200s0;rre150z0",
    "BER;01.07.2023 12:00;20.1;0.4",
    "BER;01.07.2023 12:10;20.0;",
    "BER;01.07.2023 12:20;19.5;12.5",
    "BER;01.07.2023 12:20;19.5;12.5",  # duplicate
    "short;line",
]


def test_parse_ogd_lines_handles_blank_duplicate_and_short():
    s = meteo.parse_ogd_lines(OGD)
    assert len(s) == 3 and s.iloc[0] == 0.4 and np.isnan(s.iloc[1]) and s.iloc[2] == 12.5
    assert s.index[0] == pd.Timestamp("2023-07-01 12:00")


def test_parse_ogd_missing_column_raises():
    with pytest.raises(ValueError):
        meteo.parse_ogd_lines(["a;b", "1;2"])


def test_season_only_filters_months():
    idx = pd.to_datetime(["2023-01-05", "2023-04-05", "2023-10-31", "2023-11-01"])
    s = pd.Series([1, 2, 3, 4], index=idx)
    assert meteo.season_only(s).tolist() == [2, 3]


def test_save_load_roundtrip(tmp_path):
    s = meteo.parse_ogd_lines(OGD)
    meteo.save_series(s, tmp_path / "x.csv.gz")
    r = meteo.load_series(tmp_path / "x.csv.gz")
    assert r.index.equals(s.index) and np.allclose(r.fillna(-1), s.fillna(-1))


def test_parse_openmeteo_with_nulls():
    s = meteo.parse_openmeteo({"minutely_15": {"time": ["2026-07-01T12:00", "2026-07-01T12:15"], "precipitation": [1.5, None]}})
    assert s.iloc[0] == 1.5 and np.isnan(s.iloc[1])


def test_mm15_to_mm10_preserves_total():
    s15 = pd.Series([3.0, 0.0, 6.0], index=pd.date_range("2026-07-01 12:00", periods=3, freq="15min"))
    s10 = meteo.mm15_to_mm10(s15)
    assert s10.sum() == pytest.approx(9.0)


# ---------- risk ----------
def _series(vals, start="2023-06-29 20:00"):
    return pd.Series(vals, index=pd.date_range(start, periods=len(vals), freq="10min"), dtype=float)


def test_rain_index_reference_values():
    assert rain_index(_series([8.0])).iloc[0] == pytest.approx(1.0)  # 8 mm in 10 min
    assert rain_index(_series([5, 5, 5])).iloc[-1] == pytest.approx(1.0)  # 15 mm in 30 min
    assert rain_index(_series([0, 0, 0])).iloc[-1] == 0.0


def test_rain_index_nan_is_zero():
    assert rain_index(_series([np.nan, 4.0])).iloc[-1] == pytest.approx(0.5)


def test_site_factor_by_class_and_sill():
    assert SiteExposure(3).factor == pytest.approx(1.0)
    assert SiteExposure(0).factor == pytest.approx(0.35)
    assert SiteExposure(3, sill_mm=0).factor == pytest.approx(1.5)  # opening at ground level
    assert SiteExposure(3, sill_mm=400).factor == pytest.approx(0.6)  # clipped
    with pytest.raises(ValueError):
        _ = SiteExposure(5).factor


def test_risk_scales_with_exposure():
    r = _series([8.0])
    assert risk_score(r, SiteExposure(3)).iloc[0] > risk_score(r, SiteExposure(1)).iloc[0]


def test_level_names():
    assert [level_of(x) for x in (0.1, 0.8, 1.0, 2.0)] == ["ok", "watch", "pre-alert", "severe"]


def test_episodes_merge_within_gap():
    sc = _series([0, 1.2, 0, 1.3, 0] + [0] * 30 + [1.1])
    eps = episodes(sc, 1.0, merge_gap_min=60)
    assert len(eps) == 2 and eps[0]["peak"] == pytest.approx(1.3)


def test_lookahead_is_earlier_than_observed():
    obs = _series([0, 0, 0, 10, 10])
    idx = rain_index(obs)
    la = lookahead_index(idx, 30)
    assert idx[idx >= 1].index[0] > la[la >= 1].index[0]


def test_persistence_is_causal_and_earlier_than_observed():
    obs = _series([0, 0, 5, 5, 5, 5])
    p = persistence_index(obs, 30)
    o = rain_index(obs)
    assert p[p >= 1].index[0] < o[o >= 1].index[0]
    # causal: changing the future must not change earlier values
    obs2 = obs.copy()
    obs2.iloc[-1] = 50.0
    assert persistence_index(obs2, 30).iloc[:-1].equals(p.iloc[:-1])


def test_persistence_zero_horizon_equals_observed():
    obs = _series([0, 3, 6, 2])
    assert np.allclose(persistence_index(obs, 0), rain_index(obs))


def test_index_with_forecast_uses_future_rain():
    obs = _series([0, 0])
    fc = pd.Series([8.0], index=[obs.index[-1] + pd.Timedelta(minutes=10)])
    assert index_with_forecast(obs, fc) == pytest.approx(1.0)
    assert index_with_forecast(obs, fc.iloc[:0]) == 0.0


# ---------- battery ----------
def test_airtime_sf7_8_byte_payload():
    assert airtime_s(8, sf=7) == pytest.approx(0.0566, abs=0.001)


def test_airtime_sf10_and_monotonic():
    assert airtime_s(8, sf=10) == pytest.approx(0.3707, abs=0.002)
    assert airtime_s(8, 7) < airtime_s(8, 9) < airtime_s(8, 12)


def test_airtime_invalid_inputs():
    with pytest.raises(ValueError):
        airtime_s(8, sf=6)
    with pytest.raises(ValueError):
        airtime_s(-1)


def test_battery_life_plausible_and_sf_sensitive():
    y7 = BatteryModel(sf=7).life_years()
    y10 = BatteryModel(sf=10).life_years()
    assert 5 < y7 < 40 and y10 < y7


def test_battery_shorter_heartbeat_shortens_life():
    assert BatteryModel(heartbeat_period_min=10).life_years() < BatteryModel(heartbeat_period_min=60).life_years()
