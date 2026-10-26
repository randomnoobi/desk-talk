"""
The event log on this computer (settings → event_log.folder), for the Log page. ?day=2026-10-21 picks a
day (today when left out, or when it isn't a date like that).

    GET  /api/log/days         the days with a log (at most event_log.keep_days), newest first, and today
    GET  /api/log/events       what matters to you, newest first: sips, breaks, food, finished tasks, sunlight,
                               reminders, warnings and Lens's camera notes; ?types= (habit, nudge, alert, camera)
                               and ?limit=
    GET  /api/log/status       the log's folder and today's counts, the timed jobs, the event stream, the camera
                               (the developer page uses it)
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

# The only kinds of event the page lists. Joins, leaves, pictures, objects coming and going, how a reminder
# worked out and the log's own housekeeping stay in the log but are never sent.
SHOWN_TYPES = ('habit', 'nudge', 'alert', 'camera')
SHOWN_HABITS = ('hydration', 'breaks', 'food', 'tasks', 'light', 'phone_free')
LIGHT_YOU_LOGGED = 'sunlight'     # the only 'light' habit that is something you did
PROXIMITY = 'proximity'


def day_of(asked: str | None, app: DeskTalk) -> str:
    return asked if asked and is_day(asked) else today(app)


def is_shown(event: dict[str, Any]) -> bool:
    if event.get('type') != 'habit':
        return True
    habit, action = event.get('habit'), event.get('action')
    return habit in SHOWN_HABITS and bool(action) and (habit != 'light' or action == LIGHT_YOU_LOGGED)


def plain_alert(event: dict[str, Any], app: DeskTalk) -> dict[str, Any]:
    """An alert with a short plain `subtitle` ("near Cup", or '') in place of the raw `feature` word."""
    out = {k: v for k, v in event.items() if k != 'feature'}
    near = app.profiles.get(str(event.get('near') or '')) if event.get('kind') == PROXIMITY else None
    out['subtitle'] = app.config.prompts.live_context.recent_near.format(name=near.name).strip() if near else ''
    return out


def shown_events(app: DeskTalk, day: str, wanted: set[str], limit: int) -> list[dict[str, Any]]:
    # Asks for every event of those types, because some are left out below and the page should get `limit`
    # that matter.
    every = app.event_log.events(day, wanted, None)
    shown = [e for e in every if is_shown(e)][:limit]
    return [plain_alert(e, app) if e.get('type') == 'alert' else e for e in shown]


@router.get('/api/log/days')
def log_days(app: App) -> dict[str, Any]:
    return {'days': app.event_log.days()[:app.config.event_log.keep_days], 'today': today(app)}


@router.get('/api/log/events')
def log_events(app: App, day: str | None = None, types: str | None = None,
               limit: str | None = None) -> dict[str, Any]:
    asked = {t for t in (types or '').split(',') if t in SHOWN_TYPES}
    size = list_size(limit, app.config.web.list_sizes.log_events)
    day = day_of(day, app)
    return {'day': day, 'events': shown_events(app, day, asked or set(SHOWN_TYPES), size)}


@router.get('/api/log/status')
def log_status(app: App) -> dict[str, Any]:
    log = app.event_log
    return {'folder': str(log.folder), 'today': log.counts(today(app)), 'last_compaction': log.last_compaction,
            'scheduler': [{k: job[k] for k in SCHEDULER_KEYS} for job in app.scheduler.status()],
            'stream': app.bus.stats(), 'camera': camera_info(app)}
