"""
The system numbers of the machine the app runs on (on the Pi: the Pi's), for the developer page's Pi tab:

    GET /api/system/stats?since=<ts>   {collecting, every_sec, keep_min, info, samples}: the samples newer than
                                       `since` (all those kept, when left out), oldest first. collecting is false
                                       when settings → system_stats.enabled is off (on a computer)

Like /api/health, any device on your Wi-Fi may ask: the developer page only runs on a computer, which fetches
these from the Pi (GET /api/dev/pi_stats). Command lines are cut to system_stats.command_chars.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from desktalk.web.deps import App

router = APIRouter()


@router.get('/api/system/stats')
def system_stats(app: App, since: float | None = None) -> dict[str, Any]:
    return app.system_stats.history(since)
