"""Escalation chain with acknowledgement (deterministic, clock passed in)."""

from __future__ import annotations

from dataclasses import dataclass, field

DEFAULT_CHAIN = [("occupant", 0), ("property_manager", 10), ("insurer_emergency_line", 30)]


@dataclass
class Incident:
    kind: str  # "pre-alert" | "alarm" | "heartbeat-loss"
    opened_at: float  # minutes
    acked_by: str | None = None
    acked_at: float | None = None
    notified: list = field(default_factory=list)  # (role, t)


class Escalation:
    """Each step notifies one more role ``delay`` minutes after opening; an ack stops the chain."""

    def __init__(self, chain=None, notify=None):
        self.chain = list(chain or DEFAULT_CHAIN)
        if [d for _, d in self.chain] != sorted(d for _, d in self.chain):
            raise ValueError("chain delays must be non-decreasing")
        self.notify = notify or (lambda role, incident, t: None)
        self.incidents: list[Incident] = []

    def open(self, kind: str, t: float) -> Incident:
        for inc in self.incidents:
            if inc.kind == kind and inc.acked_at is None:
                return inc  # same incident still open: do not duplicate
        inc = Incident(kind, t)
        self.incidents.append(inc)
        self.tick(t)
        return inc

    def ack(self, kind: str, who: str, t: float) -> bool:
        for inc in self.incidents:
            if inc.kind == kind and inc.acked_at is None:
                inc.acked_by, inc.acked_at = who, t
                return True
        return False

    def tick(self, t: float) -> None:
        for inc in self.incidents:
            if inc.acked_at is not None:
                continue
            done = {r for r, _ in inc.notified}
            for role, delay in self.chain:
                if role not in done and t - inc.opened_at >= delay:
                    inc.notified.append((role, t))
                    self.notify(role, inc, t)
