"""LoRa airtime (Semtech SX127x/SX126x formula) and a battery-life ESTIMATE from assumed currents.

All currents below are assumed datasheet-typical values, not bench measurements.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

LORAWAN_OVERHEAD = 13  # MHDR + DevAddr + FCtrl + FCnt + FPort + MIC


def airtime_s(payload_bytes: int, sf: int = 7, bw_hz: int = 125_000, cr: int = 1, preamble: int = 8) -> float:
    """Time on air of one LoRaWAN uplink (explicit header, CRC on); ``payload_bytes`` is the app payload."""
    if not 7 <= sf <= 12:
        raise ValueError("sf must be 7..12")
    if payload_bytes < 0:
        raise ValueError("payload must be >= 0")
    pl = payload_bytes + LORAWAN_OVERHEAD
    t_sym = (2**sf) / bw_hz
    de = 1 if t_sym > 0.016 else 0
    num = 8 * pl - 4 * sf + 28 + 16
    n_payload = 8 + max(math.ceil(num / (4 * (sf - 2 * de))) * (cr + 4), 0)
    return (preamble + 4.25) * t_sym + n_payload * t_sym


@dataclass
class BatteryModel:
    capacity_mah: float = 3000.0  # 2 x AA lithium primary, nominal
    usable: float = 0.8  # derating for cut-off voltage and temperature
    self_discharge_per_year: float = 0.01
    sleep_ua: float = 8.0  # MCU + radio + ultrasonic board leakage
    tx_ma: float = 44.0  # +14 dBm
    rx_ma: float = 12.0
    rx_s_per_uplink: float = 0.2  # two short class A receive windows
    level_meas_ma: float = 15.0  # ultrasonic ranging
    level_meas_s: float = 0.1
    wet_probe_ma: float = 0.5
    wet_probe_s: float = 0.005
    sf: int = 7
    payload_bytes: int = 8
    heartbeat_period_min: float = 60.0
    level_period_min: float = 10.0
    wet_probe_period_s: float = 60.0
    wet_events_per_year: float = 4.0
    uplinks_per_wet_event: int = 40
    selftest_extra_ma_s: float = 15.0  # one pump-current + sensor self-test per day, mA*s

    def mah_per_day(self) -> dict:
        tx_s = airtime_s(self.payload_bytes, self.sf)
        uplinks_dry = 24 * 60 / self.heartbeat_period_min
        uplinks_wet = self.wet_events_per_year * self.uplinks_per_wet_event / 365.0
        n_up = uplinks_dry + uplinks_wet
        tx = n_up * tx_s * self.tx_ma / 3600
        rx = n_up * self.rx_s_per_uplink * self.rx_ma / 3600
        level = (24 * 60 / self.level_period_min) * self.level_meas_ma * self.level_meas_s / 3600
        probe = (86400 / self.wet_probe_period_s) * self.wet_probe_ma * self.wet_probe_s / 3600
        sleep = self.sleep_ua / 1000 * 24
        selftest = self.selftest_extra_ma_s / 3600
        parts = {"tx": tx, "rx": rx, "level": level, "wet_probe": probe, "sleep": sleep, "selftest": selftest}
        parts["total"] = sum(parts.values())
        parts["airtime_s"] = tx_s
        return parts

    def life_years(self) -> float:
        per_day = self.mah_per_day()["total"]
        per_day += self.capacity_mah * self.self_discharge_per_year / 365.0
        return self.capacity_mah * self.usable / per_day / 365.0
