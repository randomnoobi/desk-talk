"""
What may leave home for the cloud server, and what the Pi and the server say to each other.

to_cloud_event() is the one filter. The sender runs it before an event goes out, and the server runs the same
function again on what arrives, so an old or wrong sender can't make the server keep more. It works from a list
of the fields that may go, never a list of fields that may not: a field it doesn't know is dropped. That is how
to-do text, pictures and anything added later stay home by default.

What goes (the spec's send table, "What gets sent"):
    object_state  change, object, prev_state, new_state, device_id, conf_avg, dwell_sec  (not the box)
    habit         habit, action, the numbers in extra, how (from HOW_WORDS), healthy  (never say)
    nudge         habit, rule, agent, evidence  (never text; for tasks, not evidence either)
    rule_outcome  rule, habit, result, after_sec
                  (a to-do's rule key holds the to-do's text, so for tasks only 'tasks.due' or 'tasks.open' goes)
    alert         kind, agent, scenario, feature  (never text)
    camera        key, kind  (never text)
    goals         wake, bed, the goal numbers
    daily_totals  day and the day's numbers
Every other type (detection, agent_joined, agent_left, compaction ...) stays home, and so does anything the
Demo tab made.

Standard library only: the camera program on the Pi may import this with the Pi's own python3 (3.11 or newer).
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from desktalk_shared._read import WireError

__all__ = ['HOW_WORDS', 'MAX_ID_CHARS', 'MAX_TEXT_CHARS', 'ClaimReply', 'IngestReply', 'Refusal',
           'daily_totals_event', 'goals_event', 'is_demo', 'to_cloud_event']

MAX_ID_CHARS = 100        # the longest event id the server keeps
MAX_TEXT_CHARS = 300      # the longest piece of text in one field; longer text is cut

# The only words a habit's `how` may be, so the report can tell a logged sip from a camera guess, and a real break
# from the camera being off. Found in core/habits/habit_reader.py, core/habits/tracker.py, core/demo/firer.py and
# core/chat/tracker_actions.py; tests/unit/test_shared_cloud_api.py checks this list against them.
HOW_WORDS = frozenset({'camera', 'button', 'chat', 'left the desk', 'camera off'})

# A number whose name starts like this is not sent, whatever its value: it could stand for a picture.
_PICTURE_STEMS = ('picture', 'jpeg', 'jpg', 'image', 'photo', 'frame', 'bbox', 'snapshot')
_KEY = re.compile(r'[a-z][a-z0-9_]{0,39}')
_DAY = re.compile(r'\d{4}-\d{2}-\d{2}', re.ASCII)       # ASCII only: \d would also take other scripts' digits

# Per type: the text fields and the number fields that may go. (A habit's extra numbers are handled separately.)
_OBJECT_STATE = (('change', 'object', 'prev_state', 'new_state', 'device_id'), ('conf_avg', 'dwell_sec'))
_NUDGE = (('habit', 'rule', 'agent', 'evidence'), ())
_RULE_OUTCOME = (('rule', 'habit', 'result'), ('after_sec',))
_ALERT = (('kind', 'agent', 'scenario', 'feature'), ())
_CAMERA = (('key', 'kind'), ())
_HABIT_RESERVED = frozenset({'id', 'ts', 'type', 'habit', 'action', 'say', 'quiet', 'how', 'healthy', 'label',
                             'extra'})


def _is_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return isinstance(value, int) or math.isfinite(value)       # (isfinite can't take a huge whole number)


def _text(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value[:MAX_TEXT_CHARS]
    return None


def _numbers(values: Any) -> dict[str, Any]:
    """The entries of a mapping that are real numbers under a plain name."""
    if not isinstance(values, Mapping):
        return {}
    return {k: v for k, v in values.items()
            if isinstance(k, str) and _KEY.fullmatch(k) and not k.startswith(_PICTURE_STEMS) and _is_number(v)}


def is_demo(event: Mapping[str, Any]) -> bool:
    """Made by the Demo tab: `demo` is true (in extra, which the event dict spreads out) or the rule starts demo."""
    extra = event.get('extra')
    if event.get('demo') is True or (isinstance(extra, Mapping) and extra.get('demo') is True):
        return True
    rule = event.get('rule')
    return isinstance(rule, str) and rule.startswith('demo.')


def _is_tasks(habit: Any, rule: Any) -> bool:
    return habit == 'tasks' or (isinstance(rule, str) and rule.startswith('tasks.'))


def _task_rule(rule: str) -> str:
    """A to-do rule's key without the to-do: its key is 'tasks.due.<the to-do's text>.<when>' (see
    core/habits/habit_rules.py), so only the fixed start of it may go."""
    for fixed in ('tasks.due', 'tasks.open'):
        if rule == fixed or rule.startswith(fixed + '.'):
            return fixed
    return 'tasks'


def _fields(event: Mapping[str, Any], texts: tuple[str, ...], numbers: tuple[str, ...]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name in texts:
        value = _text(event.get(name))
        if value is not None:
            out[name] = value
    for name in numbers:
        if _is_number(event.get(name)):
            out[name] = event[name]
    return out


def to_cloud_event(event: Mapping[str, Any]) -> dict[str, Any] | None:
    """The part of `event` that may go to the cloud server, or None when the whole event stays home.
    Gives the same answer when its own answer is put back in."""
    if not isinstance(event, Mapping):
        return None
    event_id, ts, kind = event.get('id'), event.get('ts'), event.get('type')
    if not isinstance(event_id, str) or not 0 < len(event_id) <= MAX_ID_CHARS or not _is_number(ts):
        return None
    if not isinstance(kind, str) or is_demo(event):
        return None
    out: dict[str, Any] = {'id': event_id, 'ts': ts, 'type': kind}
    if kind == 'object_state':
        out.update(_fields(event, *_OBJECT_STATE))
    elif kind == 'habit':
        out.update(_fields(event, ('habit', 'action'), ()))
        out.update(_numbers({k: v for k, v in event.items() if k not in _HABIT_RESERVED}))
        if isinstance(event.get('how'), str) and event['how'] in HOW_WORDS:
            out['how'] = event['how']
        if isinstance(event.get('healthy'), bool):
            out['healthy'] = event['healthy']
    elif kind == 'nudge':
        out.update(_fields(event, *_NUDGE))
        if _is_tasks(event.get('habit'), event.get('rule')):
            out.pop('evidence', None)          # it quotes the to-do
            if 'rule' in out:
                out['rule'] = _task_rule(out['rule'])
    elif kind == 'rule_outcome':
        out.update(_fields(event, *_RULE_OUTCOME))
        if _is_tasks(event.get('habit'), event.get('rule')) and 'rule' in out:
            out['rule'] = _task_rule(out['rule'])
    elif kind == 'alert':
        out.update(_fields(event, *_ALERT))
    elif kind == 'camera':
        out.update(_fields(event, *_CAMERA))
    elif kind == 'goals':
        out.update(_fields(event, ('wake', 'bed'), ()))
        out['goals'] = _numbers(event.get('goals'))
    elif kind == 'daily_totals':
        day = event.get('day')
        if not isinstance(day, str) or not _DAY.fullmatch(day):
            return None
        out['id'] = f'daily-{day}'             # one row per day: the newest replaces the older ones
        out['day'] = day
        out['totals'] = _numbers(event.get('totals'))
    else:
        return None
    return out


def goals_event(ts: float, *, wake: str, bed: str, goals: Mapping[str, float]) -> dict[str, Any]:
    """The person's wake and bed times and goal numbers, as an event (not yet filtered)."""
    return {'id': f'goals-{int(ts)}', 'ts': ts, 'type': 'goals', 'wake': wake, 'bed': bed, 'goals': dict(goals)}


def daily_totals_event(ts: float, day: str, totals: Mapping[str, Any]) -> dict[str, Any]:
    """One day's numbers (desk minutes, water, breaks ...) as an event (not yet filtered). Its id names the day,
    so the server keeps only the newest numbers for it."""
    return {'id': f'daily-{day}', 'ts': ts, 'type': 'daily_totals', 'day': day, 'totals': dict(totals)}


# ── what the Pi and the server say to each other ───────────────────────────
def _whole_number(data: Mapping[str, Any], key: str) -> int:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise WireError(f'{key} must be a whole number of 0 or more (got {value!r})')
    return value


def _reply_text(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise WireError(f'{key} must be text (got {value!r})')
    return value


def _object(data: Any, what: str) -> Mapping[str, Any]:
    if not isinstance(data, Mapping):
        raise WireError(f'{what} must be a JSON object (got {type(data).__name__})')
    return data


@dataclass(frozen=True, kw_only=True)
class ClaimReply:
    """The server's answer to a Pi that used a pairing code: its id and its upload token (shown once)."""

    device_id: str
    token: str

    def to_dict(self) -> dict[str, Any]:
        return {'device_id': self.device_id, 'token': self.token}

    @classmethod
    def from_dict(cls, data: Any) -> ClaimReply:
        data = _object(data, 'the claim reply')
        return cls(device_id=_reply_text(data, 'device_id'), token=_reply_text(data, 'token'))


@dataclass(frozen=True, kw_only=True)
class Refusal:
    """One event the server would not keep, and why in plain words."""

    id: str
    why: str


def _refusal(data: Any) -> Refusal:
    data = _object(data, 'a refused entry')
    event_id = data.get('id')
    if not isinstance(event_id, str):                 # '' is fine: the event may have had no id
        raise WireError(f'id must be text (got {event_id!r})')
    return Refusal(id=event_id, why=_reply_text(data, 'why'))


@dataclass(frozen=True, kw_only=True)
class IngestReply:
    """The server's answer to a batch of events: how many it kept, how many it already had, and which it refused."""

    accepted: int
    repeats: int
    refused: tuple[Refusal, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {'accepted': self.accepted, 'repeats': self.repeats,
                'refused': [{'id': r.id, 'why': r.why} for r in self.refused]}

    @classmethod
    def from_dict(cls, data: Any) -> IngestReply:
        data = _object(data, 'the events reply')
        refused = data.get('refused', [])
        if not isinstance(refused, list):
            raise WireError(f'refused must be a list (got {refused!r})')
        return cls(accepted=_whole_number(data, 'accepted'), repeats=_whole_number(data, 'repeats'),
                   refused=tuple(_refusal(r) for r in refused))
