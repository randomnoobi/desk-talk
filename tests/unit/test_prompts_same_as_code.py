"""config/prompts.yaml holds exactly the wording the code has today.

Until Tasks 5-7 of the cleanup plan switch the code over to config.prompts, the same words are in both places.
Each check here compares one text from prompts.yaml with what the code says. When a task moves a part of the
code over to config.prompts, it deletes that part's checks here (they would compare the file with itself).
"""

from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from desktalk.adapters.llm import openai_compatible
from desktalk.config import load_config
from desktalk.core.chat import message_generator, tracker_actions
from desktalk.core.chat import prompts as old
from desktalk.core.chat.workers import lens as old_lens
from desktalk.core.chat.workers import lost_items, vision_habits
from desktalk.core.habits import habit_rules as rules
from desktalk.core.vision import camera_health
from desktalk.domain import DetectorState, PiHealth, UploadInfo
from desktalk.util import clock_text
from desktalk.web.routers import chat as chat_routes

ROOT = Path(__file__).resolve().parents[2]
CONFIG = load_config(root=ROOT, environ={})
P = CONFIG.prompts
AGENT: Any = SimpleNamespace(name='Cup', object='cup', personality='Calm and kind.', habits=('hydration', 'breaks'),
                        tracker='hydration')
MIN = 60


# ── fixed texts ──
def test_fixed_replies():
    assert P.replies.needs_an_ai == message_generator.NEEDS_AN_AI == openai_compatible.NO_AI_REASON
    assert P.replies.empty_reply == message_generator.EMPTY_REPLY == openai_compatible.EMPTY_REPLY_REASON
    assert P.replies.nobody_here == message_generator.NOBODY_HERE
    assert P.replies.no_ai_label == openai_compatible.NO_AI_LABEL
    assert P.replies.no_picture == openai_compatible.NO_PICTURE_REASON
    assert P.replies.ask_for_a_sentence == openai_compatible.ASK_FOR_A_SENTENCE


def test_habit_areas():
    assert {k: v for k, v in P.habit_areas.model_dump().items() if k != 'no_habits'} == old.HABIT_AREAS
    nobody: Any = SimpleNamespace(habits=())
    assert old.habit_area(nobody) == P.habit_areas.no_habits


# ── prompts.py ──
def test_alert_prompts():
    a = AGENT
    assert P.alert.system.format(name=a.name, object=a.object, personality=a.personality) == old.event_system(a)
    assert P.alert.first.format(situation='cup near laptop', name=a.name, object=a.object) == \
        old.event_user(a, 'cup near laptop')
    for times, urgency in ((1, P.alert.more_worried), (2, P.alert.most_worried)):
        assert P.alert.follow_up.format(situation='s', previous='p', name=a.name, urgency=urgency) == \
            old.event_user(a, 's', 'p', times)


@pytest.mark.parametrize('short', [False, True])
def test_chat_prompts(short):
    a = AGENT
    text = P.chat.identity_short if short else P.chat.identity
    assert text.format(name=a.name, object=a.object, personality=a.personality, habit_area='Hydration',
                       action_instructions='ACTIONS') == old.chat_identity(a, 'Hydration', 'ACTIONS', short)
    assert P.chat.reanchor.format(name=a.name, object=a.object) == old.reanchor(a)


@pytest.mark.parametrize('short', [False, True])
def test_habit_reminder_prompts(short):
    a = AGENT
    values = dict(name=a.name, object=a.object, personality=a.personality, habit_area='Hydration',
                  widget_block='WIDGET', evidence='seen', book_tip='TIP')
    if short:
        assert P.habit_reminder.system_short.format(**values) == \
            old.habit_system(a, 'Hydration', 'WIDGET', 'SIGNALS', 'seen', 'TIP', True)
        assert P.habit_reminder.system_short.format(**{**values, 'evidence': P.habit_reminder.no_evidence}) == \
            old.habit_system(a, 'Hydration', 'WIDGET', 'SIGNALS', '', 'TIP', True)
    else:
        assert P.habit_reminder.system.format(**values, signals='SIGNALS') == \
            old.habit_system(a, 'Hydration', 'WIDGET', 'SIGNALS', 'seen', 'TIP', False)


def test_personality_prompts():
    user = P.personality.user.format(object='stapler')
    assert old.invent_personality('stapler') == [{'role': 'system', 'content': P.personality.system},
                                                 {'role': 'user', 'content': user}]


def test_lost_item_prompts():
    lost = P.lost_items
    assert lost.answer_system.format(name='Lens', object='camera') == \
        old.LOST_ITEM_SYSTEM.format(name='Lens', obj='camera')
    assert old.lost_alert('Keys', 15, ' Near Cup.') == [
        {'role': 'system', 'content': lost.alert_system},
        {'role': 'user', 'content': lost.alert_user.format(item='Keys', minutes=15, near_hint=' Near Cup.')}]
    assert lost.scan.format(item_names='keys, phone') == old.lost_scan('keys, phone')
    assert lost.in_saved_photo.format(photo='Photo 3.', item='keys') == old.lost_in_saved_photo('Photo 3.', 'keys')
    assert lost.in_live_picture.format(item='wallet') == old.lost_in_live_picture('wallet')
    assert lost.anything.format(question='where is my pen?') == old.lost_anything('where is my pen?')
    assert lost.triggers == lost_items.TRIGGERS
    assert lost.not_spotted == lost_items.NOT_SPOTTED
    assert lost.captures_button == lost_items.CTA['label']


def test_small_talk_recap_and_vision_prompts():
    a = AGENT
    assert P.small_talk.topic_system == old.TOPIC_SYSTEM
    assert P.small_talk.director_system == old.DIRECTOR_SYSTEM
    assert P.small_talk.line_system.format(name=a.name, object=a.object, personality=a.personality,
                                           desk_summary='- laptop') == old.idle_line_system(a, '- laptop')
    assert P.daily_recap.system == old.DAILY_SUMMARY_SYSTEM
    assert P.daily_recap.user.format(facts='2 L water') == old.daily_summary_user('2 L water')
    assert P.vision.habit_scene == old.HABIT_SCENE_VLM_PROMPT
    assert {k: (habit, getattr(P.vision.nudges, k)) for k, (habit, _) in vision_habits.NUDGES.items()} == \
        vision_habits.NUDGES


# ── Lens ──
def test_lens_card():
    assert {'id': chat_routes.LENS_CARD['id'], **P.lens.card.model_dump()} == chat_routes.LENS_CARD


def test_lens_pi_texts():
    lens = P.lens
    me: Any = SimpleNamespace(_pi_settings=CONFIG.pi)      # stands in for a Lens
    assert old_lens.Lens._hot_text(me, PiHealth(cpu_temp_c=81.4)) == lens.pi_hot.format(temp=81.4)
    assert old_lens.Lens._hot_text(me, PiHealth(cpu_temp_c=50, throttled='0x4')) == lens.pi_slowed
    assert old_lens.Lens._power_text(PiHealth(throttled='0x1')) == lens.pi_power
    assert old_lens.Lens._clock_text(me, -12.4) == lens.pi_clock.format(seconds=12, way=lens.pi_clock_behind)
    assert old_lens.Lens._clock_text(me, 30) == lens.pi_clock.format(seconds=30, way=lens.pi_clock_ahead)
    off = UploadInfo(frame_id='pi-1', taken_at=None, detector=DetectorState.OFF, detector_reason='it stopped')
    assert old_lens.Lens._detector_text(off) == lens.pi_detector.format(why=lens.pi_detector_why.format(
        reason='it stopped'))
    assert lens.pi_fixed.model_dump() == old_lens.PI_FIXED


# ── the camera checks ──
def _health(source_kind='pi'):
    return camera_health.CameraHealth(CONFIG.lens, source_kind=source_kind, moved_within_sec=1.5, ignore_labels=())


def test_camera_tables():
    camera = P.camera
    assert {k: (v['title'], v['fix']) for k, v in camera.titles.model_dump().items()} == camera_health.TITLES
    assert camera.fixed.model_dump() == camera_health.FIXED_TEXT
    assert camera.side_hints.model_dump() == camera_health.SIDE_HINT
    assert camera.lost.model_dump() == camera_health.LOST_TEXT
    assert camera.lost_fix.model_dump() == camera_health.LOST_FIX
    assert camera.edge_tip == camera_health.EDGE_TIP


@pytest.mark.parametrize('key, detail, blanks', [
    ('covered', {}, {}), ('dark', {}, {}), ('glare', {}, {}), ('blurry', {}, {}), ('empty', {}, {}),
    ('bumped', {'degrees': 17}, {'degrees': 17}),
    ('cutoff', {'object': 'cup', 'side': 'top'}, {'object': 'cup', 'side': 'top', 'hint': 'tilt me up a little'}),
    ('cutoff', {'object': 'cup', 'side': 'middle'}, {'object': 'cup', 'side': 'middle',
                                                    'hint': P.camera.other_side_hint}),
    ('cutoff', {}, {'object': P.camera.unknown_object, 'side': 'left', 'hint': 'point me a little more to the left'}),
    ('slow', {'fps': 1.234}, {'fps': 1.234}),
])
def test_camera_problem_texts(key, detail, blanks):
    assert _health()._problem_text(key, detail) == getattr(P.camera.problems, key).format(**blanks)


@pytest.mark.parametrize('source_kind', ['pi', 'phone', 'webcam'])
def test_lost_camera_text(source_kind):
    assert _health(source_kind)._problem_text('lost', {}) == getattr(P.camera.lost, source_kind)


def test_camera_panel_titles():
    health = _health('phone')
    health._lock = threading.RLock()
    health._active = {'lost': {'detail': {}}, 'cutoff': {'detail': {'object': 'cup', 'side': 'left'}}}
    assert health.issues() == [
        {'key': 'lost', 'title': P.camera.lost_title, 'fix': P.camera.lost_fix.phone},
        {'key': 'cutoff', 'title': P.camera.cutoff_title.format(object='Cup', side='left'),
         'fix': P.camera.titles.cutoff.fix}]


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
