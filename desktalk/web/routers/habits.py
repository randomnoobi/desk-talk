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
from desktalk.web.deps import Desk, list_size
from desktalk.web.schemas import HabitEntry, PreferenceChanges

ALL = 'all'

router = APIRouter()


@router.get('/api/habits/today')
def habits_today(desk: Desk) -> dict[str, Any]:
    return {**desk.habits.today(), 'vision': desk.vision_habits.summary()}


@router.post('/api/habits/log')
def habits_log(entry: HabitEntry, desk: Desk) -> dict[str, Any]:
    try:
        return desk.habits.log(entry.habit, entry.action, **entry.details())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


@router.get('/api/habit_log')
def habit_log(desk: Desk, category: str | None = None, limit: str | None = None) -> dict[str, Any]:
    category = (category or ALL).strip().lower()
    events = desk.vision_habits.events()
    if category != ALL:
        events = [e for e in events if e.get('category') == category]
    summary = desk.vision_habits.summary()
    summary['breaks_today'] = desk.habits.today()['breaks']['count']
    check, checked_at = desk.vision_habits.latest()
    return {'events': events[:list_size(limit, desk.config.web.list_sizes.habit_log)], 'summary': summary,
            'vlm_state': check, 'vlm_ts': checked_at}


@router.get('/api/preferences')
def preferences(desk: Desk) -> dict[str, Any]:
    now = desk.clock.now()
    return {**desk.preferences.all(), 'quiet_now': quiet_hours_now(desk.preferences, now),
            'min_gap_sec': desk.config.reminders.min_gap_sec, 'sunset_ts': desk.habits.day_times(now)['sunset_ts']}


@router.put('/api/preferences')
def change_preferences(changes: PreferenceChanges, desk: Desk) -> dict[str, Any]:
    try:
        return {'ok': True, 'preferences': desk.preferences.update(changes.changes())}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
