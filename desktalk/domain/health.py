"""How one part of the system is doing, as the health checks (desktalk/core/health/) see it."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class CheckLevel(StrEnum):
    OK = 'ok'
    WARNING = 'warning'             # worth mentioning
    SERIOUS = 'serious'             # needs a person soon
    UNKNOWN = 'unknown'             # can't tell right now (not measured, or the numbers are too old)


class Saving(StrEnum):
    """Where Desk Talk keeps what it saves."""

    PLAIN = 'plain'                 # the data folder on the SD card's whole space, as always
    ROOM = 'room'                   # the data folder in Desk Talk's own fixed space on the card
    OFF = 'off'                     # nothing is saved: the data folder is in memory


@dataclass(frozen=True, kw_only=True)
class HealthMeasure:
    """One measure: its name, how it's doing, and its number (None when there is none)."""

    key: str                        # camera, detections, pi_temperature, card, pi_heartbeat, card_rest, saving
    level: CheckLevel
    value: float | None = None
    unit: str = ''                  # s, °C or MB ('' = no number)
