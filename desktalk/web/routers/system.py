"""
The system numbers of the machine the app runs on (on the Pi: the Pi's), for the developer page's Pi tab:

    GET /api/system/stats?since=<ts>   {collecting, every_sec, keep_min, info, samples}: the samples newer than
                                       `since` (all those kept, when left out or newer than all of them: the
                                       clock went back), oldest first. collecting is false when settings →
                                       system_stats.enabled is off (on a computer)

Like /api/health, any device on your Wi-Fi may ask: the developer page only runs on a computer, which fetches
these from the Pi (GET /api/dev/pi_stats). So a program's command line is never sent whole (it can hold a
password): only the program and the script or module it runs, cut to system_stats.command_chars.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from desktalk.web.deps import App

router = APIRouter()


@router.get('/api/system/stats')
def system_stats(app: App, since: float | None = None) -> dict[str, Any]:
    return app.system_stats.history(since)
