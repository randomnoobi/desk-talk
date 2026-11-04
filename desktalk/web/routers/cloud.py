"""
The cloud server link, as the Settings page shows it:

    GET /api/cloud/status   {linked, stopped, last_sent_ts, waiting}: whether this computer is linked to a cloud
                            server, whether the server said it doesn't know us any more, when something was last
                            sent (null: never) and how many events wait to go. Never the token or the address.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from desktalk.web.deps import App

router = APIRouter()


@router.get('/api/cloud/status')
def cloud_status(app: App) -> dict[str, Any]:
    return app.cloud.status()
