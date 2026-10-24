"""
The event log on this computer (settings → event_log.folder), for the Log page. ?day=2026-10-21 picks a
day (today when left out, or when it isn't a date like that).

    GET  /api/log/days         days with a log, newest first
    GET  /api/log/events       joins, leaves, alerts, habits ... newest first; ?types=alert,habit and ?limit=
    GET  /api/log/presence     what was on the desk when, as intervals
    GET  /api/log/detections   the per-picture lines not compacted yet, newest first; ?limit=
    GET  /api/log/status       the log's folder and today's counts, the timed jobs, the event stream, the camera
    POST /api/log/compact      compact the log now
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from desktalk.bootstrap import DeskTalk
from desktalk.domain import is_day
from desktalk.web.deps import App, list_size, today
from desktalk.web.routers.live import camera_info

router = APIRouter()

SCHEDULER_KEYS = ('name', 'every_sec', 'runs', 'last_error', 'next_in_sec')   # what the Log page shows


def day_of(asked: str | None, app: DeskTalk) -> str:
    return asked if asked and is_day(asked) else today(app)


@router.get('/api/log/days')
def log_days(app: App) -> dict[str, Any]:
    return {'days': app.event_log.days(), 'today': today(app)}


@router.get('/api/log/events')
def log_events(app: App, day: str | None = None, types: str | None = None,
               limit: str | None = None) -> dict[str, Any]:
    wanted = {t for t in (types or '').split(',') if t} or None
    size = list_size(limit, app.config.web.list_sizes.log_events)
    day = day_of(day, app)
    return {'day': day, 'events': app.event_log.events(day, wanted, size)}


@router.get('/api/log/presence')
def log_presence(app: App, day: str | None = None) -> dict[str, Any]:
    day = day_of(day, app)
    return {'day': day, 'intervals': app.event_log.presence(day)}


@router.get('/api/log/detections')
def log_detections(app: App, day: str | None = None, limit: str | None = None) -> dict[str, Any]:
    size = list_size(limit, app.config.web.list_sizes.log_detections)
    day = day_of(day, app)
    return {'day': day, 'detections': app.event_log.recent_detections(day, size)}


@router.get('/api/log/status')
def log_status(app: App) -> dict[str, Any]:
    log = app.event_log
    return {'folder': str(log.folder), 'today': log.counts(today(app)), 'last_compaction': log.last_compaction,
            'scheduler': [{k: job[k] for k in SCHEDULER_KEYS} for job in app.scheduler.status()],
            'stream': app.bus.stats(), 'camera': camera_info(app)}


@router.post('/api/log/compact')
def log_compact(app: App) -> dict[str, Any]:
    return app.event_log.compact(app.clock.now())
