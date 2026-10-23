"""
Object states: is each kind of object at the desk right now?

    Phase          UNKNOWN (no answer yet), PRESENT or ABSENT
    StateRecord    one row of the state history: a change, or a snapshot of where things stand
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Phase(StrEnum):
    UNKNOWN = 'UNKNOWN'
    PRESENT = 'PRESENT'
    ABSENT = 'ABSENT'


# The kinds of row that record a change. A 'snapshot' row only says where things stood at that moment.
CHANGE_TYPES = ('appeared', 'removed', 'moved')


@dataclass(frozen=True)
class StateRecord:
    ts: float                                               # when it happened (this computer's clock)
    timezone: str                                           # the desk's UTC offset at that moment, e.g. '+08:00'
    device_id: str                                          # which camera saw it
    type: str                                               # 'appeared', 'removed', 'moved' or 'snapshot'
    label: str                                              # what kind of object (the `object` column)
    prev_state: Phase
    new_state: Phase
    conf_avg: float | None = None                           # average confidence while it was seen
    bbox: tuple[float, float, float, float] | None = None   # where it was last seen
    dwell_sec: float | None = None                          # how long it stayed in the old state
