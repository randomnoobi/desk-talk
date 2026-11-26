"""
An object's story, for the app page (click something under "On the desk"):

    GET /api/objects/<label>/story      what happened to it since the camera started, step by step (oldest first),
                                        each step in plain words with the picture taken for it, plus the
                                        pictures of it kept from earlier runs (desktalk/web/stories.py says how
                                        they're put together); 404 when there's nothing to tell: no step this
                                        run and no kept picture (pen/pencil works too: pen%2Fpencil)
    GET /api/objects/photo/<id>         one of its pictures (a JPEG, the page draws the box); 404 once it's gone

What the camera sees, for the developer page on a computer whose camera is the Pi (it asks the Pi for these):

    GET /api/objects/states?since=<ts>  each object's state now, and the changes after `since`
    GET /api/objects/smoothing          the objects with a smoothing trace
    GET /api/objects/smoothing/trace?label=
                                        one object's last pictures of smoothing, oldest first ([] for one never
                                        seen; the label is a query parameter, so pen/pencil works)

They carry only names, states, confidence numbers and times: no pictures, no to-do text.

Any device on the Wi-Fi may ask, like the rest of the app page's routes.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, HTTPException, Response

from desktalk.web.deps import App
from desktalk.web.stories import object_story, story_photo

router = APIRouter()


@router.get('/api/objects/states')
def states(app: App, since: float = 0.0) -> dict[str, Any]:
    snapshot = app.object_states.snapshot()
    return {
        'objects': {label: {'state': seen.state, 'since': seen.since} for label, seen in snapshot.objects.items()},
        'watched_since': snapshot.watched_since,
        'changes': [asdict(change) for change in app.object_states.changes(since)],
    }


@router.get('/api/objects/smoothing')
def smoothing(app: App) -> dict[str, Any]:
    return {'labels': app.object_states.traced_labels()}


@router.get('/api/objects/smoothing/trace')
def smoothing_trace(app: App, label: str) -> dict[str, Any]:
    return {'label': label, 'steps': [asdict(step) for step in app.object_states.trace(label)]}


# Before the photo route, so an object called "photo" still has a story; a picture's id is never "story".
@router.get('/api/objects/{label:path}/story')
def story(app: App, label: str) -> dict[str, Any]:
    found = object_story(app, label)
    if not found['timeline']:
        raise HTTPException(404, app.config.prompts.object_story.never_seen.format(object=label))
    return found


@router.get('/api/objects/photo/{photo_id}')
def photo(app: App, photo_id: str) -> Response:
    return story_photo(app, photo_id)
