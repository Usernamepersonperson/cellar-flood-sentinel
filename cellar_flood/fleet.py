"""Simulated device fleet: hourly heartbeats, daily pump self-test, injected failures (SIMULATED)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import payload
from .detect import NOMINAL_PUMP_A, pump_health
from .heartbeat import FleetMonitor


@dataclass
class FleetResult:
    devices: int
    days: int
    uplinks_sent: int
    uplinks_lost: int
    dead_devices: int
    dead_detected: int
    detect_delay_min_median: float
    detect_delay_min_max: float
    false_silent_flags: int
    pump_faults: int
    pump_faults_detected: int
    pump_detect_delay_h_median: float


def simulate_fleet(
    n: int = 40,
    days: int = 30,
    seed: int = 7,
    loss: float = 0.02,
    dead_frac: float = 0.10,
    pump_fault_frac: float = 0.15,
) -> FleetResult:
    rng = np.random.default_rng(seed)
    hb = 60
    total_min = days * 1440
    mon = FleetMonitor(heartbeat_min=hb)
    dead_at: dict[str, int] = {}
    fault_at: dict[str, tuple[int, str]] = {}
    selftest_min: dict[str, int] = {}
    for i in range(n):
        dev = f"{i:016x}"
        mon.register(dev, 0.0)
        selftest_min[dev] = int(rng.integers(0, 1440))
        if rng.random() < dead_frac:
            dead_at[dev] = int(rng.integers(2 * 1440, total_min - 3 * 1440))
        if rng.random() < pump_fault_frac:
            fault_at[dev] = (int(rng.integers(1440, total_min - 3 * 1440)), str(rng.choice(["dry_run", "overcurrent"])))
    sent = lost = false_flags = 0
    flagged_at: dict[str, float] = {}
    last_heard = {d: 0.0 for d in mon.devices}
    pump_found: dict[str, float] = {}
    for t in range(0, total_min, hb):
        for dev in list(mon.devices):
            tt = t + int(rng.integers(0, 3))
            if dev in dead_at and tt >= dead_at[dev]:
                continue
            sent += 1
            if rng.random() < loss:
                lost += 1
                continue
            is_selftest = (tt // 1440 * 1440 + selftest_min[dev]) // hb * hb == t
            current, running, run_s = 0.0, False, 0
            fa = fault_at.get(dev)
            if is_selftest:
                running, run_s = True, 5
                current = NOMINAL_PUMP_A + rng.normal(0, 0.15)
                if fa and tt >= fa[0]:
                    current = 0.8 if fa[1] == "dry_run" else NOMINAL_PUMP_A * 1.8
            r = payload.Reading(
                msg_type=payload.MSG_HEARTBEAT if is_selftest else payload.MSG_PERIODIC,
                pump_running=running,
                pump_current_a=current,
                pump_run_s=run_s,
                level_mm=0,
            )
            r = payload.decode(payload.encode(r))  # through the real codec
            mon.on_uplink(dev, float(tt), r.msg_type, r.selftest_ok)
            last_heard[dev] = float(tt)
            if is_selftest and pump_health(r.pump_current_a, r.pump_run_s, 0, 0, False, r.pump_running):
                pump_found.setdefault(dev, float(tt))
        for dev, reason in mon.check(float(t + hb)):
            if reason == "silent":
                if dev in dead_at and t + hb > dead_at[dev]:
                    flagged_at.setdefault(dev, float(t + hb))
                else:
                    false_flags += 1
    delays = [flagged_at[d] - last_heard[d] for d in flagged_at]
    pump_delays = [
        (pump_found[d] - fault_at[d][0]) / 60.0 for d in fault_at if d in pump_found and pump_found[d] >= fault_at[d][0]
    ]
    return FleetResult(
        devices=n,
        days=days,
        uplinks_sent=sent,
        uplinks_lost=lost,
        dead_devices=len(dead_at),
        dead_detected=len(flagged_at),
        detect_delay_min_median=float(np.median(delays)) if delays else float("nan"),
        detect_delay_min_max=float(np.max(delays)) if delays else float("nan"),
        false_silent_flags=false_flags,
        pump_faults=len(fault_at),
        pump_faults_detected=len(pump_delays),
        pump_detect_delay_h_median=float(np.median(pump_delays)) if pump_delays else float("nan"),
    )
