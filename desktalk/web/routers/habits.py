"""
Habits and your preferences:

    GET  /api/habits/today        today's habits (water, breaks, light, phone, food) and the vision checks
    POST /api/habits/log          a habit button: a glass of water, a break, some sunlight, a snack
    GET  /api/habit_log           the vision habit timeline (posture, nail biting), ?category= and ?limit=
    GET  /api/preferences         wake time, bedtime, quiet hours
    PUT  /api/preferences         change them
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from desktalk.core.habits.preferences import quiet_hours_now
from desktalk.web.deps import App, list_size
from desktalk.web.schemas import HabitEntry, PreferenceChanges

ALL = 'all'

router = APIRouter()


@router.get('/api/habits/today')
def habits_today(app: App) -> dict[str, Any]:
    return {**app.habits.today(app.clock.now()), 'vision': app.vision_habits.summary()}


@router.post('/api/habits/log')
def habits_log(entry: HabitEntry, app: App) -> dict[str, Any]:
    try:
        return app.habits.log(entry.habit, entry.action, **entry.details())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


@router.get('/api/habit_log')
def habit_log(app: App, category: str | None = None, limit: str | None = None) -> dict[str, Any]:
    category = (category or ALL).strip().lower()
    events = app.vision_habits.events()
    if category != ALL:
        events = [e for e in events if e.get('category') == category]
    summary = app.vision_habits.summary()
    summary['breaks_today'] = app.habits.today(app.clock.now())['breaks']['count']
    check, checked_at = app.vision_habits.latest()
    return {'events': events[:list_size(limit, app.config.web.list_sizes.habit_log)], 'summary': summary,
            'vlm_state': check, 'vlm_ts': checked_at}


@router.get('/api/preferences')
def preferences(app: App) -> dict[str, Any]:
    now = app.clock.now()
    return {**app.preferences.all(), 'quiet_now': quiet_hours_now(app.preferences, now, app.clock),
            'min_gap_sec': app.config.reminders.min_gap_sec, 'sunset_ts': app.habits.day_times(now)['sunset_ts']}


@router.put('/api/preferences')
def change_preferences(changes: PreferenceChanges, app: App) -> dict[str, Any]:
    try:
        return {'ok': True, 'preferences': app.preferences.update(changes.changes())}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
