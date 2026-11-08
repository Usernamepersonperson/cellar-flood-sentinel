"""Compact 8-byte LoRaWAN uplink (port 1) and ChirpStack-style MQTT envelope.

byte 0   flags: bit0 wet, bit1 pump_running, bit2 selftest_ok, bit3 low_battery, bit4 level_sensor_fault,
                bit5 reserved (0), bits6-7 message type (0 periodic, 1 alarm event, 2 heartbeat/self-test)
byte 1-2 water level above floor, mm, uint16 big endian (0xFFFF = no reading)
byte 3   rate of rise, int8, units of 0.5 mm/min
byte 4   sump-pump current, uint8, 0.1 A units (255 = 25.5 A or more)
byte 5   last pump run time, uint8, 2 s units (255 = 510 s or more)
byte 6   pump starts in the last hour, uint8
byte 7   battery, uint8, V = 2.00 + 0.01 * value
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass

PORT = 1
LENGTH = 8
MSG_PERIODIC, MSG_ALARM, MSG_HEARTBEAT = 0, 1, 2
NO_LEVEL = 0xFFFF


@dataclass
class Reading:
    wet: bool = False
    pump_running: bool = False
    selftest_ok: bool = True
    low_battery: bool = False
    sensor_fault: bool = False
    msg_type: int = MSG_PERIODIC
    level_mm: int | None = 0
    rate_mm_min: float = 0.0
    pump_current_a: float = 0.0
    pump_run_s: int = 0
    pump_starts_1h: int = 0
    battery_v: float = 3.0


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def encode(r: Reading) -> bytes:
    if r.msg_type not in (0, 1, 2):
        raise ValueError("msg_type must be 0, 1 or 2")
    b0 = (
        int(r.wet)
        | int(r.pump_running) << 1
        | int(r.selftest_ok) << 2
        | int(r.low_battery) << 3
        | int(r.sensor_fault) << 4
        | r.msg_type << 6
    )
    level = NO_LEVEL if r.level_mm is None else int(_clamp(round(r.level_mm), 0, NO_LEVEL - 1))
    rate = int(_clamp(round(r.rate_mm_min / 0.5), -128, 127))
    cur = int(_clamp(round(r.pump_current_a / 0.1), 0, 255))
    run = int(_clamp(round(r.pump_run_s / 2), 0, 255))
    starts = int(_clamp(r.pump_starts_1h, 0, 255))
    batt = int(_clamp(round((r.battery_v - 2.0) / 0.01), 0, 255))
    return bytes([b0, level >> 8, level & 0xFF, rate & 0xFF, cur, run, starts, batt])


def decode(data: bytes) -> Reading:
    if len(data) != LENGTH:
        raise ValueError(f"payload must be {LENGTH} bytes, got {len(data)}")
    b0 = data[0]
    if b0 & 0x20:
        raise ValueError("reserved flag bit set")
    if b0 >> 6 == 3:
        raise ValueError("unknown message type 3")
    level = data[1] << 8 | data[2]
    rate = data[3] - 256 if data[3] > 127 else data[3]
    return Reading(
        wet=bool(b0 & 1),
        pump_running=bool(b0 & 2),
        selftest_ok=bool(b0 & 4),
        low_battery=bool(b0 & 8),
        sensor_fault=bool(b0 & 16),
        msg_type=b0 >> 6,
        level_mm=None if level == NO_LEVEL else level,
        rate_mm_min=rate * 0.5,
        pump_current_a=round(data[4] * 0.1, 1),
        pump_run_s=data[5] * 2,
        pump_starts_1h=data[6],
        battery_v=round(2.0 + data[7] * 0.01, 2),
    )


def mqtt_topic(app_id: str, dev_eui: str) -> str:
    return f"application/{app_id}/device/{dev_eui.lower()}/event/up"


def uplink_envelope(dev_eui: str, fcnt: int, data: bytes, time_iso: str) -> str:
    """JSON like an LNS uplink event: base64 payload, port and counters."""
    return json.dumps(
        {
            "deviceInfo": {"devEui": dev_eui.lower()},
            "fCnt": fcnt,
            "fPort": PORT,
            "time": time_iso,
            "data": base64.b64encode(data).decode(),
        }
    )


def parse_envelope(msg: str) -> tuple[str, int, str, Reading]:
    d = json.loads(msg)
    if d.get("fPort") != PORT:
        raise ValueError(f"unexpected fPort {d.get('fPort')}")
    return d["deviceInfo"]["devEui"], int(d["fCnt"]), d["time"], decode(base64.b64decode(d["data"]))
