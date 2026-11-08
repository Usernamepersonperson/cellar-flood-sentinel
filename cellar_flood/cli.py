"""Command line: demo (offline), fetch, hazard, nowcast, decode."""

from __future__ import annotations

import argparse
import json
import sys

from . import evaluate as ev
from . import hazard, meteo, payload
from .risk import SiteExposure, index_with_forecast, level_of


def _lead(v) -> str:
    return "no water" if v is None else f"{v:+.0f}"


def cmd_demo(_a) -> int:
    from .report import build

    res = build()
    print("Hazard class per site (FOEN/BAFU overland-flow map, cached tiles):")
    for s in res["sites"]:
        print(f"  {s['id']:<20} class {s['hazard_class']}  station {s['station']}")
    print("\nHeaviest-hour storm per station (MeteoSwiss 10-min rain), lead time in minutes vs simulated sensor:")
    print(f"  {'site':<20} {'date':<11} {'1h mm':>6} {'observed':>9} {'extrap30':>9} {'oracle30':>9}")
    for r in res["storms"]:
        print(f"  {r['site']:<20} {r['date']:<11} {r['rain_1h_mm']:>6.1f} {_lead(r['lead_min']):>9} "
              f"{_lead(r['lead_min_persistence_30']):>9} {_lead(r['lead_min_oracle_30']):>9}")
    print("\nPre-alerts per Apr-Oct season (2020-2026), extrapolation trigger:")
    for r in res["seasons"]["persistence"]:
        print(f"  {r['site']:<20} {r['pre_alerts_per_season']:>5.1f}/season  false share {r['false_pre_alert_share']:.0%}"
              f"  water events {r['water_events']}, warned in time {r['in_time']}")
    b = res["battery"]["SF7"]
    print(f"\nBattery estimate SF7: {b['life_years']:.1f} years ({b['airtime_ms']:.1f} ms airtime/uplink)")
    f = res["fleet"]
    print(f"Fleet sim: {f['dead_detected']}/{f['dead_devices']} dead devices flagged, median {f['detect_delay_min_median']:.0f} min after last uplink")
    print("\nWrote docs/results.json, docs/figure.png, docs/post-figure.png")
    return 0


def cmd_fetch(_a) -> int:
    cfg = ev.load_config()
    for st in cfg["stations"]:
        s = meteo.fetch_station(st)
        meteo.save_series(s, ev.ROOT / "data" / "precip" / f"{st}.csv.gz")
        print(f"{st}: {len(s)} rows {s.index.min()} .. {s.index.max()}")
    for site in cfg["sites"]:
        e = hazard.lookup(site["lat"], site["lon"], ev.ROOT / "data" / "hazard" / f"{site['id']}.png")
        print(f"{site['id']}: hazard class {e.hazard_class}")
    return 0


def cmd_hazard(a) -> int:
    e = hazard.lookup(a.lat, a.lon, radius_m=a.radius)
    print(json.dumps({"label": hazard.CLASS_LABEL[e.hazard_class], **e.to_dict()}, indent=1))
    return 0


def cmd_nowcast(a) -> int:
    """Live: exposure from the map + Open-Meteo 15-min forecast -> risk level now (no history needed)."""
    e = hazard.lookup(a.lat, a.lon)
    fc = meteo.mm15_to_mm10(meteo.fetch_nowcast(a.lat, a.lon))
    now = fc.index[0]
    site = SiteExposure(e.hazard_class, a.sill)
    idx = index_with_forecast(fc.iloc[:1], fc.iloc[1:4])
    score = site.factor * idx
    print(f"hazard class {e.hazard_class}, forecast from {now:%Y-%m-%d %H:%M} UTC, risk {score:.2f} -> {level_of(score)}")
    return 0


def cmd_decode(a) -> int:
    r = payload.decode(bytes.fromhex(a.hex))
    print(json.dumps(r.__dict__, indent=1))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="cellar-flood", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("demo", help="offline: reproduce all README numbers and figures").set_defaults(fn=cmd_demo)
    sub.add_parser("fetch", help="re-download rain data and hazard tiles (network)").set_defaults(fn=cmd_fetch)
    h = sub.add_parser("hazard", help="runoff hazard class at a WGS84 coordinate (network)")
    h.add_argument("lat", type=float)
    h.add_argument("lon", type=float)
    h.add_argument("--radius", type=float, default=20.0)
    h.set_defaults(fn=cmd_hazard)
    n = sub.add_parser("nowcast", help="live risk level from the map and a 15-min forecast (network)")
    n.add_argument("lat", type=float)
    n.add_argument("lon", type=float)
    n.add_argument("--sill", type=float, default=100.0, help="height of lowest basement opening in mm")
    n.set_defaults(fn=cmd_nowcast)
    d = sub.add_parser("decode", help="decode an 8-byte uplink given as hex")
    d.add_argument("hex")
    d.set_defaults(fn=cmd_decode)
    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except (ValueError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

