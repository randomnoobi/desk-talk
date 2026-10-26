"""
Habits and your preferences:

    GET  /api/habits/today        today's habits (water, breaks, light, phone, food)
    POST /api/habits/log          a habit button: a glass of water, a break, some sunlight, a snack
    GET  /api/preferences         wake time, bedtime, quiet hours
    PUT  /api/preferences         change them
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from desktalk.web.deps import App
from desktalk.web.schemas import HabitEntry, PreferenceChanges

router = APIRouter()


@router.get('/api/habits/today')
def habits_today(app: App) -> dict[str, Any]:
    return app.habits.today(app.clock.now())


@router.post('/api/habits/log')
def habits_log(entry: HabitEntry, app: App) -> dict[str, Any]:
    try:
        return app.habits.log(entry.habit, entry.action, **entry.details())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


@router.get('/api/preferences')
def preferences(app: App) -> dict[str, Any]:
    now = app.clock.now()
    return {**app.preferences.all(), 'quiet_now': app.preferences.quiet_now(now),
            'min_gap_sec': app.config.reminders.min_gap_sec, 'sunset_ts': app.habits.day_times(now)['sunset_ts']}


@router.put('/api/preferences')
def change_preferences(changes: PreferenceChanges, app: App) -> dict[str, Any]:
    try:
        return {'ok': True, 'preferences': app.preferences.update(changes.changes())}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
