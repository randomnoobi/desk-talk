"""
An object's story, for the app page (click something under "On the desk"):

    GET /api/objects/<label>/story      what happened to it since the camera started, step by step (oldest first),
                                        each step in plain words with the picture taken for it, plus the
                                        pictures of it kept from earlier runs (desktalk/web/stories.py says how
                                        they're put together); 404 when there's nothing to tell: no step this
                                        run and no kept picture (pen/pencil works too: pen%2Fpencil)
    GET /api/objects/photo/<id>         one of its pictures (a JPEG, the page draws the box); 404 once it's gone

Any device on the Wi-Fi may ask, like the rest of the app page's routes.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Response

from desktalk.web.deps import App
from desktalk.web.stories import object_story, story_photo

router = APIRouter()


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
