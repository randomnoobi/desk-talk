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
from desktalk.web.deps import Desk, list_size, today
from desktalk.web.routers.live import camera_info

router = APIRouter()

SCHEDULER_KEYS = ('name', 'every_sec', 'runs', 'last_error', 'next_in_sec')   # what the Log page shows


def day_of(asked: str | None, desk: DeskTalk) -> str:
    return asked if asked and is_day(asked) else today(desk)


@router.get('/api/log/days')
def log_days(desk: Desk) -> dict[str, Any]:
    return {'days': desk.event_log.days(), 'today': today(desk)}


@router.get('/api/log/events')
def log_events(desk: Desk, day: str | None = None, types: str | None = None,
               limit: str | None = None) -> dict[str, Any]:
    wanted = {t for t in (types or '').split(',') if t} or None
    size = list_size(limit, desk.config.web.list_sizes.log_events)
    day = day_of(day, desk)
    return {'day': day, 'events': desk.event_log.events(day, wanted, size)}


@router.get('/api/log/presence')
def log_presence(desk: Desk, day: str | None = None) -> dict[str, Any]:
    day = day_of(day, desk)
    return {'day': day, 'intervals': desk.event_log.presence(day)}


@router.get('/api/log/detections')
def log_detections(desk: Desk, day: str | None = None, limit: str | None = None) -> dict[str, Any]:
    size = list_size(limit, desk.config.web.list_sizes.log_detections)
    day = day_of(day, desk)
    return {'day': day, 'detections': desk.event_log.recent_detections(day, size)}


@router.get('/api/log/status')
def log_status(desk: Desk) -> dict[str, Any]:
    log = desk.event_log
    return {'folder': str(log.folder), 'today': log.counts(today(desk)), 'last_compaction': log.last_compaction,
            'scheduler': [{k: job[k] for k in SCHEDULER_KEYS} for job in desk.scheduler.status()],
            'stream': desk.bus.stats(), 'camera': camera_info(desk)}


@router.post('/api/log/compact')
def log_compact(desk: Desk) -> dict[str, Any]:
    return desk.event_log.compact(desk.clock.now())
