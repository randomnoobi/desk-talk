"""config/prompts.yaml holds exactly the wording the chat code has today. (Task 6 owns it.)

Until Tasks 5-7 of the cleanup plan switch the code over to config.prompts, the same words are in both places.
Each check here compares one text from prompts.yaml with what the code says. When a task moves a part of the
code over to config.prompts, it deletes that part's checks here (they would compare the file with itself).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from desktalk.adapters.llm import openai_compatible
from desktalk.config import load_config
from desktalk.core.chat import message_generator
from desktalk.core.chat import prompts as old
from desktalk.core.chat.workers import lens as old_lens
from desktalk.core.chat.workers import lost_items, vision_habits
from desktalk.domain import DetectorState, PiHealth, UploadInfo
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
