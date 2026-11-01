"""Turns a batch of events from a Pi into what the Store keeps.

Each event goes through to_cloud_event() again, so the server keeps only what the send table allows whatever the
sender did. One bad event is refused by itself and never fails the batch: that includes text a database can't keep,
a number beyond the limit, a day that isn't a date and a time that is 0 or far in the future."""

from __future__ import annotations

import logging
import math
from collections.abc import Callable, Mapping, Sequence
from datetime import date
from typing import Any

from desktalk_cloud.config import IngestSettings, Messages
from desktalk_cloud.domain import StateChange, is_clean_text
from desktalk_cloud.ports import Clock, Store
from desktalk_shared.cloud_api import MAX_ID_CHARS, IngestReply, Refusal, to_cloud_event

log = logging.getLogger(__name__)


def number_reader(settings: IngestSettings) -> Callable[[str], int | float]:
    """For json.loads(parse_int=...). Python refuses a whole number with thousands of digits (so the whole batch
    would fail); this turns one with more digits than max_number has into infinity, which refuses just its event."""
    max_digits = len(str(int(settings.max_number)))

    def read(text: str) -> int | float:
        if len(text.lstrip('-')) > max_digits:
            return -math.inf if text.startswith('-') else math.inf
        return int(text)

    return read


def _has_big_number(value: Any, limit: float) -> bool:
    """True when a number anywhere inside `value` is beyond +/- limit (the filter lets a huge number through)."""
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return abs(value) > limit
    if isinstance(value, Mapping):
        return any(_has_big_number(v, limit) for v in value.values())
    if isinstance(value, (list, tuple)):
        return any(_has_big_number(v, limit) for v in value)
    return False


def _has_bad_text(value: Any) -> bool:
    """True when any text inside `value`, a name too, is text a store can't keep."""
    if isinstance(value, str):
        return not is_clean_text(value)
    if isinstance(value, Mapping):
        return any(_has_bad_text(k) or _has_bad_text(v) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return any(_has_bad_text(v) for v in value)
    return False


def _state_change(event: Mapping[str, Any]) -> StateChange | None:
    label, state = event.get('object'), event.get('new_state')
    if isinstance(label, str) and isinstance(state, str):
        return StateChange(label=label, state=state, since=event['ts'], event_id=event['id'])
    return None


def _refused(raw: Any, why: str) -> Refusal:
    """The refusal for `raw`, with its id only when that id is text that is safe to send back."""
    event_id = raw.get('id') if isinstance(raw, Mapping) else None
    safe = event_id[:MAX_ID_CHARS] if isinstance(event_id, str) else ''
    return Refusal(id=safe if is_clean_text(safe) else '', why=why)


def _problem(raw: Mapping[str, Any], clean: Mapping[str, Any] | None, now: float, settings: IngestSettings,
             messages: Messages) -> str | None:
    """Why this event can't be kept (plain words), or None when it can."""
    if clean is None:
        return messages.refused_not_kept
    if _has_big_number(raw, settings.max_number):          # (raw, because the filter drops an infinite number)
        return messages.refused_number_too_big
    if _has_bad_text(clean):
        return messages.refused_bad_text
    if clean['ts'] <= 0 or clean['ts'] > now + settings.max_ahead_sec:
        return messages.refused_bad_time
    if clean['type'] == 'daily_totals':
        try:
            date.fromisoformat(clean['day'])
        except ValueError:
            return messages.refused_bad_day
    return None


def ingest(store: Store, clock: Clock, settings: IngestSettings, messages: Messages, device_id: str,
           raw_events: Sequence[Any], caught_up: bool) -> IngestReply:
    now = clock.now()
    events: list[dict[str, Any]] = []
    totals: list[dict[str, Any]] = []
    changes: list[StateChange] = []
    refused: list[Refusal] = []
    for raw in raw_events:
        if not isinstance(raw, Mapping):
            refused.append(_refused(raw, messages.refused_not_an_object))
            continue
        try:
            clean = to_cloud_event(raw)
            problem = _problem(raw, clean, now, settings, messages)
        except Exception:                                   # the filter must never fail a batch
            log.warning('The filter could not read an event (it was refused)', exc_info=False)
            refused.append(_refused(raw, messages.refused_failed))
            continue
        if problem is not None or clean is None:
            refused.append(_refused(raw, problem or messages.refused_not_kept))
        elif clean['type'] == 'daily_totals':
            totals.append(clean)
        else:
            events.append(clean)
            change = _state_change(clean) if clean['type'] == 'object_state' else None
            if change is not None:
                changes.append(change)
    accepted, repeats = store.save_batch(device_id, events, totals, changes, caught_up, now)
    return IngestReply(accepted=accepted, repeats=repeats, refused=tuple(refused))
