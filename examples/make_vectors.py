"""Generate examples/vectors.json: hex payloads with the expected decoded values (shared by Python and JS tests)."""

import json
from pathlib import Path

from cellar_flood import payload as P

CASES = [
    ("dry heartbeat", P.Reading(msg_type=P.MSG_HEARTBEAT, level_mm=0, battery_v=3.30)),
    ("first water, rising", P.Reading(wet=True, level_mm=12, rate_mm_min=1.5, msg_type=P.MSG_ALARM, battery_v=3.29)),
    ("pump running normal", P.Reading(wet=True, pump_running=True, level_mm=45, rate_mm_min=-0.5, pump_current_a=4.2, pump_run_s=60, pump_starts_1h=3, msg_type=P.MSG_ALARM)),
    ("pump dry run", P.Reading(pump_running=True, selftest_ok=False, pump_current_a=0.8, pump_run_s=5, msg_type=P.MSG_HEARTBEAT)),
    ("sensor fault no level", P.Reading(sensor_fault=True, level_mm=None, low_battery=True, battery_v=2.45)),
    ("fast fall clamp", P.Reading(level_mm=300, rate_mm_min=-64.0)),
    ("max values clamp", P.Reading(wet=True, level_mm=70000, rate_mm_min=100, pump_current_a=99, pump_run_s=9999, pump_starts_1h=999, battery_v=9)),
]


def js_view(r: P.Reading) -> dict:
    return {
        "wet": r.wet,
        "pump_running": r.pump_running,
        "selftest_ok": r.selftest_ok,
        "low_battery": r.low_battery,
        "sensor_fault": r.sensor_fault,
        "msg_type": ["periodic", "alarm", "heartbeat"][r.msg_type],
        "level_mm": r.level_mm,
        "rate_mm_min": r.rate_mm_min,
        "pump_current_a": r.pump_current_a,
        "pump_run_s": r.pump_run_s,
        "pump_starts_1h": r.pump_starts_1h,
        "battery_v": r.battery_v,
    }


def main() -> None:
    out = []
    for name, r in CASES:
        raw = P.encode(r)
        out.append({"name": name, "hex": raw.hex(), "js": js_view(P.decode(raw))})
    out.append({"name": "too short", "hex": "0000", "error": True})
    out.append({"name": "reserved bit", "hex": "2000000000000000", "error": True})
    out.append({"name": "message type 3", "hex": "c000000000000000", "error": True})
    Path(__file__).with_name("vectors.json").write_text(json.dumps(out, indent=1) + "\n")


if __name__ == "__main__":
    main()
