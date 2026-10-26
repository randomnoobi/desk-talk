"""
Trackers: the panel on top of an agent's 1-on-1 chat (config/agents.yaml → tracker).

    todo        your to-do list, with optional due dates (the server keeps it: core/habits/todos.py)
    hydration   sips and water today              ┐
    breaks      breaks, minutes sitting           │ kept by the habit tracker
    food        what you ate                      │ on the server
    phone_free  phone-free desk time              │
    light       sunlight logged, room brightness  ┘

The AI sees the tracker's numbers, and can change them with action tags like
[LOG_SIP] or [ADD_TODO:Buy milk] (or [ADD_TODO:Essay|2026-10-30] with a due date) in its reply.
The wording is in config/prompts.yaml (trackers:).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from desktalk.config import ChatSettings
from desktalk.config.prompts import TrackerPrompts
from desktalk.core.habits.todos import clean_due
from desktalk.ports import HabitLog, TodoList
from desktalk.util import clock_text


def todo_panel(todos: TodoList, panel: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    """The to-do list the AI is told about: the server's (TodoList) once it has one, so it is there after a
    restart, when the page loads it and doesn't send it back; until then the panel the page sent."""
    if not todos.has_list():
        return panel
    return {'items': [{'text': t.text, 'done': t.done, 'due': t.due} for t in todos.items()]}


def normalize_todo(raw: Any, chat: ChatSettings) -> dict | None:
    """The to-do list sent by the app, cleaned up and cut to size (None if it isn't one). A due date is kept only
    when it's a real day like 2026-10-30."""
    if not isinstance(raw, dict) or raw.get('type') != 'todo':
        return None
    items = []
    for item in (raw.get('items') or [])[:chat.todo_max_items]:
        if isinstance(item, dict):
            text = str(item.get('text', ''))[:chat.todo_max_chars].strip()
            if text:
                kept = {'text': text, 'done': bool(item.get('done'))}
                due = clean_due(item.get('due'))
                if due:
                    kept['due'] = due
                items.append(kept)
    return {'type': 'todo', 'items': items}


def behavior_summary(views: Sequence[Mapping[str, Any]], now: float, chat: ChatSettings, *,
                     words: TrackerPrompts) -> str:
    """A note when the user keeps opening the tracker panel."""
    recent = [e for e in views if e['ts'] > now - chat.panel_views_window_sec]
    count = sum(1 for e in recent if e.get('action') == 'view')
    if count < chat.panel_views_min:
        return ''
    return words.behavior.format(count=count)


def _weekday(day: str) -> str:
    """'Sunday' for '2026-10-25'; '' for a day that doesn't read."""
    try:
        return date.fromisoformat(day).strftime('%A')
    except ValueError:
        return ''


def tracker_prompt(tracker: str | None, habits_today: Mapping[str, Any], todo: Mapping[str, Any] | None,
                   views: Sequence[Mapping[str, Any]], now: float, chat: ChatSettings, *,
                   words: TrackerPrompts, utc_offset_sec: float | None = None, today: str | None = None) -> str:
    """The tracker's data as a block for the AI. `words` is config.prompts.trackers. `utc_offset_sec`: the local
    time's offset from UTC for the times in it (the clock's; None = this computer's). `today`: the clock's local
    day ('2026-10-25'), which the to-do tracker tells the AI so it can turn "due Friday" into a date."""
    if not tracker:
        return ''
    t = words
    extra = behavior_summary(views, now, chat, words=t)
    if tracker == 'todo':
        weekday = _weekday(today) if today else ''
        head = t.todo_today.format(weekday=weekday, day=today) if weekday else ''
        items = (todo or {}).get('items') or []
        if not items:
            return head + extra
        lines = [f"- [{'x' if i.get('done') else ' '}] {i.get('text', '')}"
                 + (t.todo_due.format(due=i['due']) if i.get('due') else '') for i in items]
        return head + t.todo_list.format(items='\n'.join(lines)) + extra
    h = habits_today
    if tracker == 'breaks':
        b = h['breaks']
        last = (clock_text(b['last_break_ts'], utc_offset_sec=utc_offset_sec) if b['last_break_ts']
                else t.breaks_none_yet)
        return t.breaks.format(count=b['count'], last=last, sitting_min=b['sitting_sec'] // 60) + extra
    if tracker == 'hydration':
        w = h['hydration']
        last = clock_text(w['last_sip_ts'], utc_offset_sec=utc_offset_sec) if w['last_sip_ts'] else t.water_none_yet
        return t.water.format(water_ml=w['water_ml'], goal_ml=w['goal_ml'], sips=w['sips'], pace_ml=w['pace_ml'],
                              last=last) + extra
    if tracker == 'food':
        entries = h['food']['entries']
        if not entries:
            return t.food_empty + extra
        healthy = [e['label'] for e in entries if e.get('healthy')]
        junk = [e['label'] for e in entries if not e.get('healthy')]
        parts = ([t.food_healthy.format(labels=', '.join(healthy))] if healthy else []) + \
            ([t.food_treats.format(labels=', '.join(junk))] if junk else [])
        return t.food.format(count=len(entries), parts='; '.join(parts)) + extra
    if tracker == 'phone_free':
        p = h['phone_free']
        if not p['counted']:
            return t.phone_not_counted
        where = t.phone_on_desk if p['phone_on_desk'] else t.phone_off_desk
        return t.phone.format(free_min=p['phone_free_sec'] // 60, goal_min=p['goal_min'],
                              streak_min=p['streak_sec'] // 60, where=where) + extra
    if tracker == 'light':
        light = h['light']
        if light.get('lux') is not None:
            level = t.light_lux.format(lux=int(light['lux']))
        elif light.get('brightness') is not None:
            level = t.light_brightness.format(brightness=int(light['brightness']))
        else:
            level = t.light_unknown
        sun = t.sunlight_logged if light['sunlight_ts'] else t.sunlight_not_logged
        return t.light.format(sun=sun, level=level) + extra
    return extra


def action_instructions(tracker: str | None, *, words: TrackerPrompts) -> str:
    """How the AI changes this tracker (its action tags); '' for an agent without one. `words` is
    config.prompts.trackers."""
    return words.actions.model_dump().get(tracker or '', '')


def parse_actions(text: str, tracker: str | None, *, sunlight_default_min: float) -> tuple[str, list[dict]]:
    """Pull this tracker's action tags out of the AI's reply → (clean_text, actions).
    Tags for other trackers are removed but not done (e.g. Computer copying Cup's [LOG_SIP])."""
    actions: list[dict] = []
    if tracker == 'todo':
        for m in re.finditer(r'\[ADD_TODO:(.+?)\]', text):
            actions.append(_add_todo(m.group(1)))
        for m in re.finditer(r'\[DONE_TODO:(.+?)\]', text):
            actions.append({'action': 'done_todo', 'text': m.group(1).strip()})
        for m in re.finditer(r'\[DEL_TODO:(.+?)\]', text):
            actions.append({'action': 'del_todo', 'text': m.group(1).strip()})
    elif tracker == 'breaks':
        if '[LOG_BREAK]' in text:
            actions.append({'action': 'log_break'})
    elif tracker == 'hydration':
        if '[LOG_GLASS]' in text:
            actions.append({'action': 'log_glass'})
        if '[LOG_SIP]' in text:
            actions.append({'action': 'log_sip'})
    elif tracker == 'food':
        for m in re.finditer(r'\[LOG_SNACK_HEALTHY:(.+?)\]', text):
            actions.append({'action': 'log_snack', 'label': m.group(1).strip(), 'healthy': True})
        for m in re.finditer(r'\[LOG_SNACK_JUNK:(.+?)\]', text):
            actions.append({'action': 'log_snack', 'label': m.group(1).strip(), 'healthy': False})
    elif tracker == 'light':
        for m in re.finditer(r'\[LOG_SUNLIGHT:?(\d*)\]', text):
            minutes = int(m.group(1)) if m.group(1) else sunlight_default_min
            actions.append({'action': 'log_sunlight', 'minutes': minutes})
    return strip_action_tags(text), actions


def _add_todo(tag: str) -> dict:
    """[ADD_TODO:Essay|2026-10-30] → the to-do, with its due date when that's a real day (else none)."""
    text, _, day = tag.partition('|')
    action = {'action': 'add_todo', 'text': text.strip()}
    due = clean_due(day.strip())
    if due:
        action['due'] = due
    return action


ACTION_TAG = re.compile(r'\[(?:LOG_[A-Z_]+|(?:ADD|DONE|DEL)_TODO)(?::[^\]]*)?\]')


def strip_action_tags(text: str) -> str:
    """Remove any action tag left in a line, so it never shows in the chat."""
    return re.sub(r'\s{2,}', ' ', ACTION_TAG.sub('', text or '')).strip()


def apply_habit_actions(habits: HabitLog, actions: Sequence[Mapping[str, Any]]) -> list[dict]:
    """Do the habit actions on the habit tracker. Returns what's left for the app (to-do changes)."""
    left = []
    for a in actions:
        kind = a.get('action')
        if kind == 'log_sip':
            habits.log('hydration', 'sip', how='chat', quiet=True)
        elif kind == 'log_glass':
            habits.log('hydration', 'glass', how='chat', quiet=True)
        elif kind == 'log_break':
            habits.log('breaks', 'break', quiet=True)
        elif kind == 'log_snack':
            habits.log('food', 'food', label=a.get('label'), healthy=a.get('healthy'), quiet=True)
        elif kind == 'log_sunlight':
            habits.log('light', 'sunlight', minutes=a['minutes'], quiet=True)
        else:
            left.append(dict(a))
    return left
