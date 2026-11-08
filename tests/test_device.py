import json
from pathlib import Path

import pytest

from cellar_flood import payload as P
from cellar_flood.detect import AlarmConfig, LevelDetector, pump_health
from cellar_flood.escalation import Escalation
from cellar_flood.fleet import simulate_fleet
from cellar_flood.heartbeat import FleetMonitor

VECTORS = Path(__file__).resolve().parent.parent / "examples" / "vectors.json"


# ---------- payload ----------
def test_payload_length_is_8():
    assert len(P.encode(P.Reading())) == 8


def test_payload_roundtrip_values():
    r = P.Reading(wet=True, pump_running=True, level_mm=123, rate_mm_min=-2.5, pump_current_a=4.2,
                  pump_run_s=60, pump_starts_1h=3, battery_v=3.27, msg_type=P.MSG_ALARM, low_battery=True)
    d = P.decode(P.encode(r))
    assert d.level_mm == 123 and d.rate_mm_min == -2.5 and d.pump_current_a == 4.2
    assert d.pump_run_s == 60 and d.battery_v == 3.27 and d.msg_type == P.MSG_ALARM
    assert d.wet and d.pump_running and d.low_battery and d.selftest_ok and not d.sensor_fault


def test_payload_known_bytes():
    raw = P.encode(P.Reading(wet=True, level_mm=12, rate_mm_min=1.5, msg_type=P.MSG_ALARM, battery_v=3.29))
    assert raw.hex() == "45000c0300000081"


def test_payload_clamps_extremes():
    d = P.decode(P.encode(P.Reading(level_mm=10**6, rate_mm_min=999, pump_current_a=99, battery_v=9, pump_run_s=9999)))
    assert d.level_mm == 0xFFFE and d.rate_mm_min == 63.5 and d.pump_current_a == 25.5
    assert d.pump_run_s == 510 and d.battery_v == 4.55


def test_payload_no_level_reading():
    assert P.decode(P.encode(P.Reading(level_mm=None, sensor_fault=True))).level_mm is None


def test_payload_decode_rejects_bad_input():
    for bad in (b"", b"\x00" * 7, b"\x00" * 9, bytes([0x20]) + b"\x00" * 7, bytes([0xC0]) + b"\x00" * 7):
        with pytest.raises(ValueError):
            P.decode(bad)


def test_payload_invalid_msg_type_encode():
    with pytest.raises(ValueError):
        P.encode(P.Reading(msg_type=3))


def test_envelope_roundtrip_and_topic():
    raw = P.encode(P.Reading(level_mm=77))
    msg = P.uplink_envelope("0102030405060708", 42, raw, "2026-07-01T12:00:00Z")
    dev, fcnt, _t, r = P.parse_envelope(msg)
    assert dev == "0102030405060708" and fcnt == 42 and r.level_mm == 77
    assert P.mqtt_topic("cellar", "ABCD") == "application/cellar/device/abcd/event/up"


def test_envelope_wrong_port_rejected():
    msg = json_with_port(2)
    with pytest.raises(ValueError):
        P.parse_envelope(msg)


def json_with_port(port):
    d = json.loads(P.uplink_envelope("aa", 1, P.encode(P.Reading()), "t"))
    d["fPort"] = port
    return json.dumps(d)


def test_vectors_file_matches_python_codec():
    for v in json.loads(VECTORS.read_text()):
        raw = bytes.fromhex(v["hex"])
        if v.get("error"):
            with pytest.raises(ValueError):
                P.decode(raw)
        else:
            r = P.decode(raw)
            assert r.level_mm == v["js"]["level_mm"] and r.battery_v == v["js"]["battery_v"]


# ---------- detector ----------
def test_detector_states_and_alarm_threshold():
    d = LevelDetector()
    assert d.update(0, 0)["state"] == "OK"
    assert d.update(1, 5)["state"] in ("WET", "ALARM")
    assert LevelDetector().update(0, 25)["state"] == "ALARM"


def test_detector_rate_and_eta_numbers():
    d = LevelDetector(AlarmConfig(window=2))
    d.update(0, 10)
    out = d.update(5, 30)  # 4 mm/min
    assert out["rate"] == pytest.approx(4.0) and out["eta_min"] == pytest.approx((100 - 30) / 4.0)
    assert out["state"] == "ALARM"


def test_detector_fast_rise_alarms_below_alarm_level():
    d = LevelDetector(AlarmConfig(window=2))
    d.update(0, 3)
    out = d.update(1, 9)  # 6 mm/min but only 9 mm
    assert out["state"] == "ALARM"


def test_detector_falling_level_has_no_eta():
    d = LevelDetector(AlarmConfig(window=2))
    d.update(0, 30)
    assert d.update(5, 20)["eta_min"] is None


def test_detector_fault_on_missing_reading():
    assert LevelDetector().update(0, None)["state"] == "FAULT"


# ---------- pump health ----------
def test_pump_health_rules():
    assert pump_health(4.2, 60, 3, 40, False, True) == []
    assert "dry_run_or_broken_coupling" in pump_health(0.8, 5, 0, 5, False, True)
    assert "overcurrent" in pump_health(8.0, 20, 0, 40, True, True)
    assert "long_run" in pump_health(4.2, 300, 1, 40, False, True)
    assert "short_cycling" in pump_health(4.2, 10, 15, 20, False, False)
    assert "pump_not_starting" in pump_health(0.0, 0, 0, 80, True, False)


def test_pump_idle_is_not_dry_run():
    assert pump_health(0.0, 0, 0, 0, False, False) == []


# ---------- escalation ----------
def test_escalation_follows_chain_and_ack_stops_it():
    log = []
    esc = Escalation(notify=lambda role, inc, t: log.append((role, t)))
    esc.open("alarm", 0)
    esc.tick(5)
    esc.tick(10)
    assert [r for r, _ in log] == ["occupant", "property_manager"]
    assert esc.ack("alarm", "property_manager", 12)
    esc.tick(60)
    assert [r for r, _ in log] == ["occupant", "property_manager"]


def test_escalation_reaches_last_step_without_ack():
    log = []
    esc = Escalation(notify=lambda role, inc, t: log.append(role))
    esc.open("alarm", 0)
    esc.tick(31)
    assert log == ["occupant", "property_manager", "insurer_emergency_line"]


def test_escalation_no_duplicate_open_and_unknown_ack():
    esc = Escalation()
    a = esc.open("alarm", 0)
    assert esc.open("alarm", 3) is a
    assert not esc.ack("pre-alert", "x", 1)


def test_escalation_rejects_unsorted_chain():
    with pytest.raises(ValueError):
        Escalation(chain=[("a", 10), ("b", 5)])


# ---------- heartbeat ----------
def test_heartbeat_silent_after_missed_beats_and_recovers():
    m = FleetMonitor(heartbeat_min=60)
    m.register("d1", 0)
    assert m.check(200) == []
    assert ("d1", "silent") in m.check(211)  # 3.5 x 60 min without an uplink
    assert m.check(250) == []  # reported once
    m.on_uplink("d1", 300, 0, True)
    assert ("d1", "silent") in m.check(300 + 211)


def test_heartbeat_selftest_failed_and_overdue():
    m = FleetMonitor(heartbeat_min=60)
    m.register("d1", 0)
    m.on_uplink("d1", 10, 2, False)
    assert ("d1", "selftest_failed") in m.check(20)
    m2 = FleetMonitor(heartbeat_min=60)
    m2.register("d2", 0)
    for t in range(60, 3000, 60):
        m2.on_uplink("d2", t, 0, True)
    assert ("d2", "selftest_overdue") in m2.check(3000)


# ---------- fleet ----------
def test_fleet_sim_catches_every_dead_device_with_few_false_flags():
    r = simulate_fleet(n=40, days=30, seed=7)
    assert r.dead_devices > 0 and r.dead_detected == r.dead_devices
    assert r.false_silent_flags <= 2  # 3 lost beats in a row is rare but possible at 2% loss
    assert 210 <= r.detect_delay_min_median <= 270


def test_fleet_sim_is_deterministic_and_finds_pump_faults():
    a, b = simulate_fleet(seed=3), simulate_fleet(seed=3)
    assert a == b
    assert a.pump_faults > 0 and a.pump_faults_detected == a.pump_faults
