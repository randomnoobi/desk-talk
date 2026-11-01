"""Turns a batch of events from a Pi into what the Store keeps.

Each event goes through to_cloud_event() again, so the server keeps only what the send table allows whatever the
sender did. One bad event is refused by itself and never fails the batch."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from desktalk_cloud.config import IngestSettings, Messages
from desktalk_cloud.domain import StateChange
from desktalk_cloud.ports import Clock, Store
from desktalk_shared.cloud_api import MAX_ID_CHARS, IngestReply, Refusal, to_cloud_event

log = logging.getLogger(__name__)


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


def _state_change(event: Mapping[str, Any]) -> StateChange | None:
    label, state = event.get('object'), event.get('new_state')
    if isinstance(label, str) and isinstance(state, str):
        return StateChange(label=label, state=state, since=event['ts'])
    return None


def _refused(raw: Any, why: str) -> Refusal:
    event_id = raw.get('id') if isinstance(raw, Mapping) else None
    return Refusal(id=event_id[:MAX_ID_CHARS] if isinstance(event_id, str) else '', why=why)


def ingest(store: Store, clock: Clock, settings: IngestSettings, messages: Messages, device_id: str,
           raw_events: Sequence[Any], caught_up: bool) -> IngestReply:
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
        except Exception:                                   # the filter must never fail a batch
            log.warning('The filter could not read an event (it was refused)', exc_info=False)
            refused.append(_refused(raw, messages.refused_failed))
            continue
        if clean is None:
            refused.append(_refused(raw, messages.refused_not_kept))
        elif _has_big_number(clean, settings.max_number):
            refused.append(_refused(raw, messages.refused_number_too_big))
        elif clean['type'] == 'daily_totals':
            totals.append(clean)
        else:
            events.append(clean)
            change = _state_change(clean) if clean['type'] == 'object_state' else None
            if change is not None:
                changes.append(change)
    accepted, repeats = store.save_batch(device_id, events, totals, changes, caught_up, clock.now())
    return IngestReply(accepted=accepted, repeats=repeats, refused=tuple(refused))
