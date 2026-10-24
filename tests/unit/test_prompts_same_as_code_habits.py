"""config/prompts.yaml holds exactly the wording the habits code has today. (Task 7 owns it.)

Until Tasks 5-7 of the cleanup plan switch the code over to config.prompts, the same words are in both places.
Each check here compares one text from prompts.yaml with what the code says. When a task moves a part of the
code over to config.prompts, it deletes that part's checks here (they would compare the file with itself).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from desktalk.config import load_config
from desktalk.core.chat import tracker_actions
from desktalk.core.habits import habit_rules as rules
from desktalk.util import clock_text

ROOT = Path(__file__).resolve().parents[2]
CONFIG = load_config(root=ROOT, environ={})
P = CONFIG.prompts
AGENT: Any = SimpleNamespace(name='Cup', object='cup', personality='Calm and kind.', habits=('hydration', 'breaks'),
                        tracker='hydration')
MIN = 60


# ── trackers ──
def test_tracker_actions():
    assert {k: v for k, v in tracker_actions.ACTIONS.items()} == P.trackers.actions.model_dump()


def _today(**changes):
    h = {'breaks': {'count': 2, 'last_break_ts': None, 'sitting_sec': 610, 'sit_limit_min': 30},
         'hydration': {'water_ml': 720, 'goal_ml': 2400, 'sips': 9, 'pace_ml': 1200, 'last_sip_ts': None},
         'food': {'entries': []},
         'phone_free': {'counted': True, 'phone_free_sec': 3840, 'goal_min': 120, 'streak_sec': 1320,
                        'phone_on_desk': False},
         'light': {'lux': None, 'brightness': None, 'sunlight_ts': None}}
    for key, value in changes.items():
        h[key] = {**h[key], **value}
    return h


def _tracker(tracker, h=None, todo=None, views=()):
    return tracker_actions.tracker_prompt(tracker, h or _today(), todo, list(views), 10_000, CONFIG.chat)


def test_tracker_texts():
    t = P.trackers
    views = [{'ts': 9_900, 'action': 'view'}] * 3
    assert _tracker('todo', todo={'items': [{'text': 'Send it', 'done': True}]}, views=views) == \
        t.todo_list.format(items='- [x] Send it') + t.behavior.format(count=3)
    assert _tracker('todo') == ''
    assert _tracker('breaks') == t.breaks.format(count=2, last=t.breaks_none_yet, sitting_min=10)
    last = 1_792_569_600
    assert _tracker('breaks', _today(breaks={'last_break_ts': last})) == \
        t.breaks.format(count=2, last=clock_text(last), sitting_min=10)
    assert _tracker('hydration') == t.water.format(water_ml=720, goal_ml=2400, sips=9, pace_ml=1200,
                                                    last=t.water_none_yet)
    assert _tracker('food') == t.food_empty
    entries = [{'label': 'apple', 'healthy': True}, {'label': 'chips', 'healthy': False}]
    assert _tracker('food', _today(food={'entries': entries})) == t.food.format(
        count=2, parts='; '.join([t.food_healthy.format(labels='apple'), t.food_treats.format(labels='chips')]))
    assert _tracker('phone_free') == t.phone.format(free_min=64, goal_min=120, streak_min=22, where=t.phone_off_desk)
    assert _tracker('phone_free', _today(phone_free={'phone_on_desk': True})).endswith(f'{t.phone_on_desk}.')
    assert _tracker('phone_free', _today(phone_free={'counted': False})) == t.phone_not_counted
    assert _tracker('light') == t.light.format(sun=t.sunlight_not_logged, level=t.light_unknown)
    assert _tracker('light', _today(light={'lux': 310.6, 'sunlight_ts': last})) == t.light.format(
        sun=t.sunlight_logged, level=t.light_lux.format(lux=310))
    assert _tracker('light', _today(light={'brightness': 120.4})) == t.light.format(
        sun=t.sunlight_not_logged, level=t.light_brightness.format(brightness=120))


# ── the habit nudges ──
WAKE = 1_792_569_600.0          # any time works: the rules only compare times
BED = WAKE + 15.5 * 3600


def _state(**values):
    return rules.RuleState(wake_ts=WAKE, bed_ts=BED, at_desk=True, **values)


def _nudge(key, nudges):
    found = [n for n in nudges if n.key == key]
    assert len(found) == 1, [n.key for n in nudges]
    return found[0]


def _same(nudge, words, **blanks):
    assert nudge.evidence == words.evidence.format(**blanks)
    assert nudge.text == words.text.format(**blanks)
    if hasattr(words, 'tip'):
        assert nudge.tip == words.tip.format(**blanks)


def test_hydration_nudges():
    h, cfg = P.habits, CONFIG.protocols.hydration
    now = WAKE + 20 * MIN
    _same(_nudge('hydration.morning', rules.hydration_nudges(_state(), cfg, now)), h.hydration_morning, minutes=20)
    now = WAKE + 3 * 3600
    progress = h.water_progress.format(drunk='0.0 L', pace='0.7 L')
    pace = _nudge('hydration.pace', rules.hydration_nudges(_state(session_start=WAKE), cfg, now))
    _same(pace, h.hydration_pace, minutes=180, progress=progress)
    cup = _nudge('hydration.pace', rules.hydration_nudges(_state(session_start=WAKE, drink_on_desk=True), cfg, now))
    _same(cup, h.hydration_pace_cup, minutes=180, progress=progress)
    assert cup.tip == h.hydration_pace.tip


def test_break_nudges():
    h, cfg = P.habits, CONFIG.protocols.breaks
    now = WAKE + 3 * 3600
    sit = _nudge('breaks.sit', rules.break_nudges(_state(sit_since=now - 40 * MIN), cfg, now))
    _same(sit, h.breaks_sit, minutes=40, phone_note=h.phone_note)
    quiet_phone = cfg.model_copy(update={'no_phone_on_breaks': False})
    assert rules.break_nudges(_state(sit_since=now - 40 * MIN), quiet_phone, now)[0].text == \
        h.breaks_sit.text.format(minutes=40, phone_note='')
    meal = rules.break_nudges(_state(meal_done_ts=now - 60, meal_name='banana'), cfg, now)[0]
    _same(meal, h.breaks_meal, food='banana', phone_note=h.phone_note)
    unnamed = rules.break_nudges(_state(meal_done_ts=now - 60), cfg, now)[0]
    _same(unnamed, h.breaks_meal, food=h.unknown_meal, phone_note=h.phone_note)


def test_light_nudges():
    h, cfg = P.habits, CONFIG.protocols.light
    now = WAKE + 30 * MIN
    _same(_nudge('light.morning', rules.light_nudges(_state(), cfg, now)), h.light_morning, minutes=30)
    now = WAKE + 8 * 3600
    sunset = now + 60 * MIN
    afternoon = _nudge('light.afternoon', rules.light_nudges(_state(sunset_ts=sunset, sunlight_ts=WAKE), cfg, now))
    _same(afternoon, h.light_afternoon, sunset=clock_text(sunset), minutes=60)
    now = BED - 60 * MIN
    evening = _nudge('light.evening', rules.light_nudges(
        _state(sunset_ts=now - 10 * MIN, bright_since=now - 10 * MIN, lux=500, sunlight_ts=WAKE), cfg, now))
    _same(evening, h.light_evening, when=h.bed_soon.format(minutes=60), light=h.light_lux.format(lux=500))
    now = BED - 3 * 3600
    late = _nudge('light.evening', rules.light_nudges(
        _state(sunset_ts=now - 10 * MIN, bright_since=now - 10 * MIN, brightness=200, sunlight_ts=WAKE), cfg, now))
    _same(late, h.light_evening, when=h.sun_has_set, light=h.light_brightness.format(brightness=200))
    now = WAKE + 4 * 3600
    dim = _nudge('light.daytime_dim', rules.light_nudges(
        _state(sunset_ts=WAKE + 12 * 3600, dim_since=now - 15 * MIN, lux=20, sunlight_ts=WAKE), cfg, now))
    _same(dim, h.light_daytime_dim, light=h.light_lux.format(lux=20), minutes=15)
    assert rules._light_words(_state()) == h.light_bright


def test_screen_and_phone_nudges():
    h, light, phone = P.habits, CONFIG.protocols.light, CONFIG.protocols.phone_free
    now = BED - 30 * MIN
    screens = rules.screen_nudges(_state(screen_on_desk=True), light, now)[0]
    _same(screens, h.screens_bedtime, lead=h.bed_at.format(bed=clock_text(BED), minutes=30))
    late = rules.screen_nudges(_state(screen_on_desk=True), light, BED + 10 * MIN)[0]
    _same(late, h.screens_bedtime, lead=h.past_bedtime)
    bedtime = rules.phone_nudges(_state(phone_on_desk_since=now - 5 * MIN), phone, now)[0]
    _same(bedtime, h.phone_bedtime, bed=clock_text(BED), minutes=5)
    now = WAKE + 3 * 3600
    focus = rules.phone_nudges(_state(phone_on_desk_since=now - 10 * MIN), phone, now)[0]
    _same(focus, h.phone_focus, minutes=10)


def test_food_and_task_nudges():
    h, protocols = P.habits, CONFIG.protocols
    now = WAKE + 3 * 3600
    food = rules.food_nudges(_state(food_since=now - 25 * MIN, food_name='apple'), protocols.food, now)[0]
    _same(food, h.food_sitting, food='apple', minutes=25)
    snack = rules.food_nudges(_state(food_since=now - 25 * MIN), protocols.food, now)[0]
    _same(snack, h.food_sitting, food=h.unknown_snack, minutes=25)
    one = rules.task_nudges(_state(open_todos=('Send it',), session_start=now - 50 * MIN), protocols.tasks, now)[0]
    _same(one, h.tasks_open, count=1, plural='', first='Send it')
    two = rules.task_nudges(_state(open_todos=('A', 'B'), session_start=now - 50 * MIN), protocols.tasks, now)[0]
    _same(two, h.tasks_open, count=2, plural='s', first='A')
