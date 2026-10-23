"""
Events: what happened, as typed data.

Producers publish events (a picture was analysed, an agent joined, a sip was logged ...) on the
EventBus; consumers subscribe by event class. to_dict() gives the same flat JSON the event log
and the web API have always used, e.g.

    {"id": "1a-3f9c2e", "ts": 1760000000.0, "type": "agent_joined", "agent": "cup", "name": "Cup", ...}

and event_from_dict() reads it back. Fields an event doesn't know (older or newer extras) are kept
in `extra` and written back out unchanged.
"""

from __future__ import annotations

import itertools
import uuid
from collections.abc import Mapping
from dataclasses import MISSING, dataclass, field, fields
from enum import Enum
from typing import Any, ClassVar

from desktalk_shared.detection import Detection

_seq = itertools.count(1)
_COMMON = ('id', 'ts', 'extra')


def new_event_id() -> str:
    return f'{next(_seq):x}-{uuid.uuid4().hex[:6]}'


def _plain(value: Any) -> Any:
    """A field value as plain JSON-ready data."""
    if hasattr(value, 'to_dict'):
        return value.to_dict()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (tuple, list)):
        return [_plain(v) for v in value]
    return value


@dataclass(frozen=True, kw_only=True)
class Event:
    TYPE: ClassVar[str] = ''
    KEYS: ClassVar[Mapping[str, str]] = {}      # field name → its key in the dict, where they differ

    ts: float                                   # when it happened (this computer's clock)
    id: str = field(default_factory=new_event_id)
    extra: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {'id': self.id, 'ts': self.ts, 'type': self.TYPE}
        for f in fields(self):
            if f.name not in _COMMON:
                out[self.KEYS.get(f.name, f.name)] = _plain(getattr(self, f.name))
        out.update(self.extra)
        return out

    @classmethod
    def _decode(cls, name: str, value: Any) -> Any:
        """Turn one plain dict value back into the field's type (subclasses override for nested types)."""
        return value

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Event:
        d = dict(data)
        d.pop('type', None)
        if 'ts' not in d:
            raise ValueError(f'{cls.TYPE} event has no ts')
        kwargs: dict[str, Any] = {'ts': d.pop('ts'), 'id': str(d.pop('id', '') or new_event_id())}
        for f in fields(cls):
            if f.name in _COMMON:
                continue
            key = cls.KEYS.get(f.name, f.name)
            if key in d:
                kwargs[f.name] = cls._decode(f.name, d.pop(key))
            elif f.default is MISSING and f.default_factory is MISSING:
                raise ValueError(f'{cls.TYPE} event has no {key}')
        return cls(**kwargs, extra=d)


@dataclass(frozen=True, kw_only=True)
class Light:
    brightness: float              # 0-255, average of the picture
    lux: float | None = None       # from the Pi camera module, if it has one

    def to_dict(self) -> dict[str, Any]:
        return {'brightness': self.brightness, 'lux': self.lux}


@dataclass(frozen=True, kw_only=True)
class DetectionEvent(Event):
    """One analysed picture: what was seen and where (about 3 a second)."""

    TYPE: ClassVar[str] = 'detection'
    KEYS: ClassVar[Mapping[str, str]] = {'detections': 'detected_obj'}

    frame_id: str
    source: str                         # pi, phone or webcam
    size: tuple[int, int]               # picture width, height
    detections: tuple[Detection, ...]
    person: bool
    light: Light
    moved: bool = False                 # Lens saw the camera move
    taken_at: float | None = None       # the camera's own clock
    detected_on: str = 'computer'       # where YOLO ran: pi or computer

    @classmethod
    def _decode(cls, name: str, value: Any) -> Any:
        if name == 'detections':
            return tuple(Detection.from_dict(d) for d in value)
        if name == 'size':
            return tuple(value)
        if name == 'light':
            return Light(**value)
        return value


@dataclass(frozen=True, kw_only=True)
class AgentJoined(Event):
    """"Cup joined the chat"."""

    TYPE: ClassVar[str] = 'agent_joined'
    agent: str          # agent id from config/agents.yaml
    name: str
    label: str          # the detector's word for it
    conf: float


@dataclass(frozen=True, kw_only=True)
class AgentLeft(Event):
    TYPE: ClassVar[str] = 'agent_left'
    agent: str
    name: str
    label: str
    stayed_sec: float


@dataclass(frozen=True, kw_only=True)
class Alert(Event):
    """Something risky on the desk (cup near the laptop, near the edge, a pet) or a scenario
    nudge (lost object, vision habit)."""

    TYPE: ClassVar[str] = 'alert'
    kind: str                # proximity or scenario
    agent: str = ''          # who should say it
    text: str = ''
    scenario: str = ''       # risk_detection, lost_object, reminders
    feature: str = ''


@dataclass(frozen=True, kw_only=True)
class HabitEvent(Event):
    """A habit happened (a sip, a break, sunlight, a phone-free block)."""

    TYPE: ClassVar[str] = 'habit'
    habit: str
    action: str
    say: str = ''            # what an agent says about it ('' = nothing)
    quiet: bool = False      # log it, but don't say it in the chat


@dataclass(frozen=True, kw_only=True)
class NudgeSent(Event):
    """A habit reminder went out."""

    TYPE: ClassVar[str] = 'nudge'
    habit: str
    rule: str
    agent: str
    evidence: str            # what the camera saw
    text: str


@dataclass(frozen=True, kw_only=True)
class CameraNotice(Event):
    """Lens noticed a camera problem, or that it's fixed."""

    TYPE: ClassVar[str] = 'camera'
    key: str
    kind: str
    text: str


@dataclass(frozen=True, kw_only=True)
class Compaction(Event):
    """The event log turned per-picture lines into intervals."""

    TYPE: ClassVar[str] = 'compaction'
    KEYS: ClassVar[Mapping[str, str]] = {'from_ts': 'from', 'to_ts': 'to'}
    frames: int
    intervals: int
    from_ts: float | None = None
    to_ts: float | None = None


EVENT_TYPES: dict[str, type[Event]] = {cls.TYPE: cls for cls in (
    DetectionEvent, AgentJoined, AgentLeft, Alert, HabitEvent, NudgeSent, CameraNotice, Compaction)}


def event_from_dict(data: Mapping[str, Any]) -> Event:
    """Read an event dict (from the event log, say). Raises ValueError for unknown types or missing fields."""
    cls = EVENT_TYPES.get(data.get('type'))
    if cls is None:
        raise ValueError(f'unknown event type {data.get("type")!r}')
    return cls.from_dict(data)
