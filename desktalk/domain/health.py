"""How one part of the system is doing, as the health checks (desktalk/core/health/) see it."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class CheckLevel(StrEnum):
    OK = 'ok'
    WARNING = 'warning'             # worth mentioning
    SERIOUS = 'serious'             # needs a person soon
    UNKNOWN = 'unknown'             # can't tell right now (not measured, or the numbers are too old)


@dataclass(frozen=True, kw_only=True)
class HealthMeasure:
    """One measure: its name, how it's doing, and its number (None when there is none)."""

    key: str                        # camera, detections, pi_temperature, card, pi_heartbeat
    level: CheckLevel
    value: float | None = None
    unit: str = ''                  # s, °C or MB ('' = no number)
