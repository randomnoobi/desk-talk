"""
An object's story, read the same way for both pages that show it: the app page's story panel (routers/objects.py)
and the developer page's trace (routers/dev.py).

    object_story(app, label)     {"label", "timeline": [step, ...] (oldest first), "photos": [photo, ...]}
    story_photo(app, photo_id)   one picture (a JPEG); 404 once it's gone

The timeline mixes the object's state changes since the camera's current run of pictures began (the newest
settings → object_story.events_kept of them) with its chat joins, leaves and refusals (the chat keeps that many
already). Each step has the plain words for it (config/prompts.yaml → object_story) and the picture taken for it:
the step picture of the same kind (appeared, joined, moved, removed) taken nearest to it, within
object_story.photo_match_sec. A picture that goes with no step (one taken every object_story.photo_every_sec
while it's there, or one whose step is from an earlier run) is a step of its own, with source "photo".
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Response

from desktalk.bootstrap import DeskTalk
from desktalk.config.prompts import ObjectStoryWords
from desktalk.domain import ChatJoin, Phase, StateRecord, TracePhoto

# Which steps a step picture can go with: its `why` → the source of the step (a state change or a chat join).
PHOTO_STEPS = {'appeared': 'state', 'moved': 'state', 'removed': 'state', 'joined': 'chat'}


def _what(words: ObjectStoryWords, kind: str) -> str:
    """The plain words for a step, e.g. 'removed' → 'went away'."""
    return getattr(words, kind, kind)


def _photo(photo: TracePhoto) -> dict[str, Any]:
    return {'id': photo.id, 'ts': photo.ts, 'why': photo.why, 'confidence': photo.confidence, 'box': list(photo.box),
            'size': list(photo.size)}


def _state_row(record: StateRecord) -> dict[str, Any]:
    """One object state change. `seen_for_sec` is how long it had been seen, so only a change out of PRESENT has
    one (its dwell_sec is the time it was PRESENT)."""
    seen_for = record.dwell_sec if record.prev_state == Phase.PRESENT else None
    return {'ts': record.ts, 'source': 'state', 'kind': record.type, 'confidence': record.conf_avg,
            'seen_for_sec': seen_for, 'on_desk': None, 'step': None, 'before': str(record.prev_state),
            'after': str(record.new_state), 'photo': None}


def _chat_row(join: ChatJoin) -> dict[str, Any]:
    """One chat join, leave or refusal."""
    return {'ts': join.ts, 'source': 'chat', 'kind': join.kind, 'confidence': join.confidence,
            'seen_for_sec': join.seen_for_sec, 'on_desk': join.on_desk, 'step': join.step, 'before': None,
            'after': None, 'photo': None}


def _photo_row(photo: TracePhoto) -> dict[str, Any]:
    """A picture that goes with no other step."""
    return {'ts': photo.ts, 'source': 'photo', 'kind': photo.why, 'confidence': photo.confidence,
            'seen_for_sec': None, 'on_desk': None, 'step': None, 'before': None, 'after': None,
            'photo': _photo(photo)}


def _place_photos(rows: list[dict[str, Any]], photos: list[TracePhoto], match_sec: float) -> list[dict[str, Any]]:
    """Put each step picture next to the step of its kind nearest in time (one picture per step); the rest become
    steps of their own."""
    loose = []
    for photo in photos:
        source = PHOTO_STEPS.get(photo.why)
        free = [row for row in rows if row['photo'] is None and row['source'] == source and row['kind'] == photo.why
                and abs(row['ts'] - photo.ts) <= match_sec]
        if free:
            min(free, key=lambda row: abs(row['ts'] - photo.ts))['photo'] = _photo(photo)
        else:
            loose.append(_photo_row(photo))
    return loose


def object_story(app: DeskTalk, label: str) -> dict[str, Any]:
    """Before the first picture since the app started there are no state changes: the store's changes are all
    from earlier runs."""
    settings = app.config.object_story
    since = app.object_states.snapshot().watched_since
    changes = [] if since is None else app.state_store.changes(since, label=label)[-settings.events_kept:]
    joins = app.chat_joins.joins(label) if app.chat_joins is not None else []
    photos = app.trace_photos.photos(label) if app.trace_photos is not None else []
    rows = [*map(_state_row, changes), *map(_chat_row, joins)]
    rows += _place_photos(rows, photos, settings.photo_match_sec)
    words = app.config.prompts.object_story
    timeline = [{**row, 'what': _what(words, row['kind'])} for row in sorted(rows, key=lambda row: row['ts'])]
    return {'label': label, 'timeline': timeline, 'photos': [_photo(p) for p in photos]}


def story_photo(app: DeskTalk, photo_id: str) -> Response:
    jpeg = app.trace_photos.jpeg(photo_id) if app.trace_photos is not None else None
    if jpeg is None:
        raise HTTPException(404)                 # only the newest are kept
    return Response(jpeg, media_type='image/jpeg')
