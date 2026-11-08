"""Heartbeat / daily self-test supervision for a device fleet."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DeviceState:
    first_seen: float = 0.0
    last_seen: float = 0.0
    last_selftest: float | None = None
    selftest_ok: bool = True
    silent: bool = False


@dataclass
class FleetMonitor:
    """Flags a device silent when nothing arrived for miss_factor x heartbeat interval (minutes)."""

    heartbeat_min: float = 60.0
    miss_factor: float = 3.5
    selftest_period_min: float = 1440.0
    devices: dict = field(default_factory=dict)

    def register(self, dev: str, t: float) -> None:
        self.devices[dev] = DeviceState(first_seen=t, last_seen=t)

    def on_uplink(self, dev: str, t: float, msg_type: int, selftest_ok: bool) -> None:
        d = self.devices.setdefault(dev, DeviceState(first_seen=t))
        d.last_seen, d.silent = t, False
        d.selftest_ok = selftest_ok
        if msg_type == 2:
            d.last_selftest = t

    def check(self, t: float) -> list[tuple[str, str]]:
        """Return findings (device, reason). 'silent' fires once until the device is heard again."""
        out = []
        for dev, d in self.devices.items():
            if not d.silent and t - d.last_seen > self.miss_factor * self.heartbeat_min:
                d.silent = True
                out.append((dev, "silent"))
            if not d.selftest_ok:
                out.append((dev, "selftest_failed"))
            ref = d.last_selftest if d.last_selftest is not None else d.first_seen
            if not d.silent and t - ref > 1.5 * self.selftest_period_min:
                out.append((dev, "selftest_overdue"))
        return out
