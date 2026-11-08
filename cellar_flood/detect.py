"""Sensor-confirmed alarm with rate-of-rise, and sump-pump health rules (device-reading side)."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

NOMINAL_PUMP_A = 4.2


@dataclass
class AlarmConfig:
    wet_mm: float = 3.0  # first water on the floor
    alarm_mm: float = 20.0  # confirmed alarm
    critical_mm: float = 100.0  # level at which stored goods / electrics are hit
    fast_rise_mm_min: float = 1.0  # rate that raises the alarm early
    window: int = 3  # samples used for the rate


class LevelDetector:
    """Feeds on (t_minutes, level_mm); returns state OK / WET / ALARM plus rate and ETA to critical."""

    def __init__(self, cfg: AlarmConfig | None = None):
        self.cfg = cfg or AlarmConfig()
        self.buf: deque[tuple[float, float]] = deque(maxlen=self.cfg.window)

    def update(self, t_min: float, level_mm: float | None) -> dict:
        if level_mm is None:
            return {"state": "FAULT", "rate": 0.0, "eta_min": None}
        self.buf.append((t_min, level_mm))
        rate = 0.0
        if len(self.buf) >= 2:
            (t0, l0), (t1, l1) = self.buf[0], self.buf[-1]
            if t1 > t0:
                rate = (l1 - l0) / (t1 - t0)
        c = self.cfg
        state = "OK"
        if level_mm >= c.wet_mm:
            state = "WET"
        if level_mm >= c.alarm_mm or (level_mm >= c.wet_mm and rate >= c.fast_rise_mm_min):
            state = "ALARM"
        eta = None
        if rate > 0 and level_mm < c.critical_mm:
            eta = (c.critical_mm - level_mm) / rate
        return {"state": state, "rate": rate, "eta_min": eta}


def pump_health(
    current_a: float,
    run_s: int,
    starts_1h: int,
    level_mm: float | None,
    rising: bool,
    pump_running: bool,
    nominal_a: float = NOMINAL_PUMP_A,
) -> list[str]:
    """Return a list of findings (empty = healthy)."""
    f: list[str] = []
    if pump_running and current_a < 0.35 * nominal_a:
        f.append("dry_run_or_broken_coupling")
    if pump_running and current_a > 1.3 * nominal_a:
        f.append("overcurrent")
    if run_s >= 240:
        f.append("long_run")
    if starts_1h >= 12:
        f.append("short_cycling")
    if level_mm is not None and level_mm > 50 and rising and not pump_running:
        f.append("pump_not_starting")
    return f
