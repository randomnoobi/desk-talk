"""
Trackers: the panel on top of an agent's 1-on-1 chat (config/agents.yaml → tracker).

    todo        your to-do list (kept in the browser, sent to the server for context)
    hydration   sips and water today              ┐
    breaks      breaks, minutes sitting           │ kept by the habit tracker
    food        what you ate                      │ on the server
    phone_free  phone-free desk time              │
    light       sunlight logged, room brightness  ┘

The AI sees the tracker's numbers, and can change them with action tags like
[LOG_SIP] or [ADD_TODO:Buy milk] in its reply.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from desktalk.config import ChatSettings
from desktalk.ports import HabitLog
from desktalk.util import clock_text


def normalize_todo(raw: Any, chat: ChatSettings) -> dict | None:
    """The to-do list sent by the app, cleaned up and cut to size (None if it isn't one)."""
    if not isinstance(raw, dict) or raw.get('type') != 'todo':
        return None
    items = []
    for item in (raw.get('items') or [])[:chat.todo_max_items]:
        if isinstance(item, dict):
            text = str(item.get('text', ''))[:chat.todo_max_chars].strip()
            if text:
                items.append({'text': text, 'done': bool(item.get('done'))})
    return {'type': 'todo', 'items': items}


def behavior_summary(views: Sequence[Mapping[str, Any]], now: float, chat: ChatSettings) -> str:
    """A note when the user keeps opening the tracker panel."""
    recent = [e for e in views if e['ts'] > now - chat.panel_views_window_sec]
    count = sum(1 for e in recent if e.get('action') == 'view')
    if count < chat.panel_views_min:
        return ''
    return (f'\nBEHAVIOR: The user opened this panel {count} time(s) in the last hour. '
            'If nothing was completed or changed, gently point that out.')


def tracker_prompt(tracker: str | None, habits_today: Mapping[str, Any], todo: Mapping[str, Any] | None,
                   views: Sequence[Mapping[str, Any]], now: float, chat: ChatSettings) -> str:
    """The tracker's data as a block for the AI."""
    if not tracker:
        return ''
    extra = behavior_summary(views, now, chat)
    if tracker == 'todo':
        items = (todo or {}).get('items') or []
        if not items:
            return extra
        lines = [f"- [{'x' if t.get('done') else ' '}] {t.get('text', '')}" for t in items]
        return "\nUSER'S TO-DO LIST (prioritize open items, celebrate completed ones):\n" + '\n'.join(lines) + extra
    h = habits_today
    if tracker == 'breaks':
        b = h['breaks']
        last = clock_text(b['last_break_ts']) if b['last_break_ts'] else 'none yet today'
        return (f"\nUSER'S BREAKS: {b['count']} break(s) today, last one {last}. Sitting for "
                f"{b['sitting_sec'] // 60} min right now. The book suggests a 5-minute walk every "
                f"{b['sit_limit_min']} minutes of sitting." + extra)
    if tracker == 'hydration':
        w = h['hydration']
        last = clock_text(w['last_sip_ts']) if w['last_sip_ts'] else 'none yet'
        return (f"\nUSER'S WATER: about {w['water_ml']} ml of a {w['goal_ml']} ml goal ({w['sips']} sips), "
                f"pace for now {w['pace_ml']} ml. Last sip: {last}. Encourage drinking water, earlier in the day "
                f"is better. Celebrate hitting the goal." + extra)
    if tracker == 'food':
        entries = h['food']['entries']
        if not entries:
            return "\nUSER'S FOOD LOG: Empty today." + extra
        healthy = [e['label'] for e in entries if e.get('healthy')]
        junk = [e['label'] for e in entries if not e.get('healthy')]
        parts = ([f"Healthy: {', '.join(healthy)}"] if healthy else []) + \
            ([f"Treats: {', '.join(junk)}"] if junk else [])
        return (f"\nUSER'S FOOD LOG TODAY ({len(entries)} items): " + '; '.join(parts)
                + '. Comment on their choices — praise healthy picks, gently tease treats.' + extra)
    if tracker == 'phone_free':
        p = h['phone_free']
        if not p['counted']:
            return '\n(The phone is the camera today, so phone-free time is not counted.)'
        on = 'ON the desk right now' if p['phone_on_desk'] else 'not on the desk right now'
        return (f"\nPHONE-FREE TIME: {p['phone_free_sec'] // 60} min of desk time with no phone in sight today "
                f"(goal {p['goal_min']} min), current streak {p['streak_sec'] // 60} min. The phone is {on}." + extra)
    if tracker == 'light':
        light = h['light']
        if light.get('lux') is not None:
            level = f"about {int(light['lux'])} lux"
        elif light.get('brightness') is not None:
            level = f"picture brightness {int(light['brightness'])}/255"
        else:
            level = 'unknown'
        sun = 'logged' if light['sunlight_ts'] else 'NOT logged yet'
        return (f"\nLIGHT: morning sunlight {sun}; the room is {level} right now. Book: sunlight within an hour of "
                f"waking, late-afternoon light before sunset, dim lights in the hour before bed." + extra)
    return extra


ACTIONS = {
    'todo': """
ACTIONS — you MUST use these whenever the conversation implies it:
- User wants to add/create/remember/track anything → you MUST include [ADD_TODO:task text] in your reply
- User says a task is done/finished/completed → you MUST include [DONE_TODO:exact task text from the list] in your reply
- User wants to remove/delete a task → you MUST include [DEL_TODO:exact task text] in your reply
You can include multiple action tags in one reply.
IMPORTANT: Write a short conversational reply AND the tag(s). Example: "Done, adding that! [ADD_TODO:Buy groceries]"
If the user asks you to add something, you MUST include the [ADD_TODO:...] tag. Do NOT just talk about it.""",
    'breaks': """
ACTIONS — you MUST use these whenever the conversation implies it:
- User says they took/are taking a break, stepped away, walked, stretched → you MUST include [LOG_BREAK] in your reply
IMPORTANT: Write a short conversational reply AND the tag. Example: "Nice walk! [LOG_BREAK]"
If the user mentions taking a break, you MUST include [LOG_BREAK]. Do NOT just talk about it.""",
    'hydration': """
ACTIONS — you MUST use these whenever the conversation implies it:
- User says they drank water, had a sip → you MUST include [LOG_SIP] in your reply
- User says they drank a full glass or bottle → you MUST include [LOG_GLASS] in your reply
IMPORTANT: Write a short conversational reply AND the tag. Example: "Splash! That's the spirit. [LOG_SIP]"
If the user mentions drinking water, you MUST include a tag. Do NOT just talk about it.""",
    'food': """
ACTIONS — you MUST use these whenever the conversation implies it:
- User mentions eating something healthy (fruit, nuts, veggies, etc.) → you MUST include [LOG_SNACK_HEALTHY:food name]
- User mentions eating something unhealthy (chips, candy, soda, etc.) → you MUST include [LOG_SNACK_JUNK:food name]
IMPORTANT: Write a short conversational reply AND the tag. Example: "Ooh almonds, solid pick! [LOG_SNACK_HEALTHY:almonds]"
If the user mentions eating something, you MUST include the tag. Do NOT just talk about it.""",  # noqa: E501
    'light': """
ACTIONS — you MUST use these whenever the conversation implies it:
- User says they went outside / got sunlight / took a walk outside → you MUST include [LOG_SUNLIGHT:minutes] (a number; use 10 if unsure)
IMPORTANT: Write a short conversational reply AND the tag. Example: "Sunshine! [LOG_SUNLIGHT:15]"
""",  # noqa: E501
}


def action_instructions(tracker: str | None) -> str:
    return ACTIONS.get(tracker or '', '')


def parse_actions(text: str, tracker: str | None, *, sunlight_default_min: float) -> tuple[str, list[dict]]:
    """Pull this tracker's action tags out of the AI's reply → (clean_text, actions).
    Tags for other trackers are removed but not done (e.g. Computer copying Cup's [LOG_SIP])."""
    actions: list[dict] = []
    if tracker == 'todo':
        for m in re.finditer(r'\[ADD_TODO:(.+?)\]', text):
            actions.append({'action': 'add_todo', 'text': m.group(1).strip()})
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
