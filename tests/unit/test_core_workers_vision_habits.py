"""Vision habit checks: a vision AI looks at the desk picture for posture, screen distance and a phone in hand."""

from __future__ import annotations

import calendar
import json
from pathlib import Path

from desktalk.config import VisionHabitsSettings, VisionNudgeRepeats, load_config
from desktalk.core.chat.workers.vision_habits import VisionDesk, VisionHabits
from desktalk.domain import AgentProfile, Alert, Size
from tests.fakes import (
    FakeCharacterMemory,
    FakeChatModel,
    FakeClock,
    FakeDeskStatus,
    FakeLivePictures,
    FakePhotoTaker,
    FakeProfiles,
    StaticRoster,
    SyncEventBus,
)

SETTINGS = VisionHabitsSettings(every_sec=42, slouch_log_min_sec=90, nail_log_cooldown_sec=50, max_log_events=120,
                                start_delay_sec=12, min_every_sec=18,
                                repeat_after_sec=VisionNudgeRepeats(too_close=1200, slouch=1500, phone=1500))
WORDS = load_config(environ={}, dotenv=Path('/nonexistent')).prompts.vision
GOOD = {'posture': 'good', 'face_screen_distance': 'ok', 'phone': 'none', 'attention': 'screen', 'nail_biting': False}
SLOUCH = dict(GOOD, posture='slouch')
NAILS = dict(GOOD, nail_biting=True)
TOO_CLOSE = dict(GOOD, face_screen_distance='too_close')
PROFILES = FakeProfiles([AgentProfile(id='computer', name='Computer', habits=['screens', 'breaks']),
                         AgentProfile(id='phone', name='Phone', habits=['phone_free']),
                         AgentProfile(id='paper', name='Paper', habits=['tasks'])])


PICTURE = b'a desk picture'
TWO_PM = calendar.timegm((2026, 10, 21, 14, 0, 0, 0, 0, 0))       # on the FakeClock (UTC)


class FakeVisionDesk(FakeLivePictures, FakeCharacterMemory):
    def __init__(self):
        FakeLivePictures.__init__(self)
        FakeCharacterMemory.__init__(self)


class Rig:
    def __init__(self, answers=(), vision='fake', members=('computer',), warm=True, settings=SETTINGS, words=WORDS):
        self.clock = FakeClock(TWO_PM)
        self.answers = list(answers)
        self.ai = FakeChatModel(lambda messages: self.answers.pop(0)) if vision == 'fake' else vision
        self.desk = FakeVisionDesk()
        self.desk.put(0, PICTURE, PICTURE, Size(64, 48))
        self.bus = SyncEventBus()
        self.status = FakeDeskStatus(warm=warm)
        self.photos = FakePhotoTaker()
        self.habits = VisionHabits(self.ai, self.bus, self.desk, StaticRoster(members), PROFILES, self.status,
                                   settings, self.clock, self.photos, prompts=words)

    def look(self, answer, after=0.0):
        """One round where the vision AI answers `answer` (a dict, or raw text)."""
        self.clock.advance(after)
        self.answers.append(answer if isinstance(answer, str) else json.dumps(answer))
        return self.habits.tick(self.clock.now())

    def nudges(self):
        return [(a.feature, a.agent) for a in self.bus.of_type(Alert)]


# ── when it looks ──
def test_the_fake_desk_has_what_the_vision_checks_need():
    assert isinstance(FakeVisionDesk(), VisionDesk)


def test_it_looks_every_few_seconds_but_never_more_often_than_the_minimum():
    assert Rig().look(GOOD) == 42
    fast = SETTINGS.model_copy(update={'every_sec': 5})
    assert Rig(settings=fast).look(GOOD) == 18


def test_nothing_happens_without_a_vision_ai():
    rig = Rig(vision=None)
    assert rig.habits.tick(rig.clock.now()) == 42
    assert rig.habits.latest() == ({}, 0.0)


def test_no_question_without_a_picture():
    rig = Rig()
    rig.desk.plain_pictures.clear()
    rig.habits.tick(rig.clock.now())
    assert rig.ai.calls == []


def test_the_question_is_about_the_newest_picture_and_waits_for_no_one():
    rig = Rig()
    rig.look(GOOD)
    call = rig.ai.calls[0]
    assert call['kind'] == 'vision' and call['jpeg'] == PICTURE and call['background']
    assert '"posture"' in call['prompt']


def test_an_ai_that_cannot_answer_changes_nothing():
    rig = Rig(vision=FakeChatModel(fail='Ollama is not running'))
    assert rig.habits.tick(rig.clock.now()) == 42
    assert rig.habits.latest() == ({}, 0.0) and rig.habits.events() == []


# ── what it saw ──
def test_the_answer_is_kept_for_the_app_and_the_chat():
    rig = Rig()
    rig.look('Here you go: {"posture": "slouch", "phone": "on_desk", "attention": "screen", "nail_biting": false}')
    state, ts = rig.habits.latest()
    assert state == {'posture': 'slouch', 'phone': 'on_desk', 'attention': 'screen', 'nail_biting': False}
    assert ts == rig.clock.now()
    assert rig.desk.vision_brief() == 'posture=slouch, phone=on_desk, attention=screen'


def test_an_answer_that_is_not_json_is_ignored():
    rig = Rig()
    rig.look('I think the person is sitting fine.')
    assert rig.habits.latest() == ({}, 0.0) and rig.desk.vision_brief() == ''


def test_a_long_slouch_goes_in_the_habit_log_with_a_photo():
    rig = Rig()
    rig.look(SLOUCH)
    rig.look(SLOUCH, after=42)
    rig.look(GOOD, after=58)
    [entry] = rig.habits.events()
    assert (entry['category'], entry['title'], entry['duration_sec']) == ('posture_bad', 'Poor posture ~2 min', 100)
    assert entry['ts'] == rig.clock.now()
    assert entry['snapshot_id'] == 'photo-1' and rig.photos.saved == [('Posture: ~2m slouch', ())]


def test_a_short_slouch_is_not_logged():
    rig = Rig()
    rig.look(SLOUCH)
    rig.look(GOOD, after=89)
    assert rig.habits.events() == []


def test_the_summary_counts_todays_slouching_including_right_now():
    rig = Rig()
    rig.look(SLOUCH)
    rig.look(GOOD, after=100)
    rig.look(SLOUCH, after=42)
    rig.clock.advance(30)
    assert rig.habits.summary() == {'posture_bad_seconds_today': 130, 'nail_biting_count_today': 0,
                                    'ongoing_slouch_seconds': 30}


def test_nail_biting_is_logged_when_it_starts_not_while_it_goes_on():
    rig = Rig()
    rig.look(NAILS)
    rig.look(NAILS, after=60)
    assert [e['category'] for e in rig.habits.events()] == ['nail_biting']
    rig.look(GOOD, after=1)
    rig.look(dict(GOOD, nails_hand_to_mouth=True), after=1)
    assert len(rig.habits.events()) == 2
    assert rig.habits.summary()['nail_biting_count_today'] == 2


def test_nail_biting_that_starts_again_soon_is_not_logged_again():
    rig = Rig()
    rig.look(NAILS)
    rig.look(GOOD, after=1)
    rig.look(NAILS, after=1)
    assert len(rig.habits.events()) == 1
    rig.look(GOOD, after=1)
    rig.look(NAILS, after=47)
    assert len(rig.habits.events()) == 2


def test_the_summary_counts_only_today():
    rig = Rig()
    rig.look(NAILS)
    rig.look(SLOUCH, after=1)
    rig.look(GOOD, after=100)
    rig.clock.advance(24 * 3600)
    assert rig.habits.summary() == {'posture_bad_seconds_today': 0, 'nail_biting_count_today': 0,
                                    'ongoing_slouch_seconds': 0}


def test_the_log_keeps_only_the_newest_entries():
    rig = Rig(settings=SETTINGS.model_copy(update={'max_log_events': 2, 'nail_log_cooldown_sec': 0}))
    for _ in range(3):
        rig.look(NAILS, after=1)
        rig.look(GOOD, after=1)
    events = rig.habits.events()
    assert len(events) == 2 and events[0]['ts'] == rig.clock.now() - 1


# ── nudges ──
def test_a_slouch_seen_twice_in_a_row_gets_a_nudge_from_the_screens_agent():
    rig = Rig()
    rig.look(SLOUCH)
    assert rig.nudges() == []
    rig.look(SLOUCH, after=42)
    [alert] = rig.bus.of_type(Alert)
    assert (alert.kind, alert.scenario, alert.feature, alert.agent) == ('scenario', 'reminders', 'vision_slouch',
                                                                         'computer')
    assert alert.text == WORDS.nudges.slouch and alert.ts == rig.clock.now()


def test_the_question_and_the_nudge_come_from_the_prompt_settings():
    words = WORDS.model_copy(update={'habit_scene': 'How is the person sitting?',
                                     'nudges': WORDS.nudges.model_copy(update={'slouch': 'Sit up, please.'})})
    rig = Rig(words=words)
    rig.look(SLOUCH)
    rig.look(SLOUCH, after=42)
    assert rig.ai.calls[0]['prompt'] == 'How is the person sitting?'
    assert [a.text for a in rig.bus.of_type(Alert)] == ['Sit up, please.']


def test_the_same_nudge_waits_its_repeat_time():
    rig = Rig()
    rig.look(SLOUCH)
    rig.look(SLOUCH, after=42)
    rig.look(SLOUCH, after=1400)
    assert len(rig.nudges()) == 1
    rig.look(SLOUCH, after=100)
    assert len(rig.nudges()) == 2


def test_no_nudge_while_the_camera_is_warming_up():
    rig = Rig(warm=False)
    rig.look(SLOUCH)
    rig.look(SLOUCH, after=42)
    assert rig.nudges() == []


def test_one_nudge_at_a_time():
    both = dict(TOO_CLOSE, posture='slouch')
    rig = Rig()
    rig.look(both)
    rig.look(both, after=42)
    assert rig.nudges() == [('vision_too_close', 'computer')]


def test_a_phone_in_hand_gets_a_nudge_from_the_phone_free_agent():
    rig = Rig(members=('computer', 'phone'))
    rig.look(dict(GOOD, phone='in_hand'))
    rig.look(dict(GOOD, attention='phone'), after=42)
    assert rig.nudges() == [('vision_phone', 'phone')]


def test_the_tasks_agent_or_lens_speaks_when_no_agent_has_the_habit():
    rig = Rig(members=('paper',))
    rig.look(TOO_CLOSE)
    rig.look(TOO_CLOSE, after=42)
    nobody = Rig(members=())
    nobody.look(TOO_CLOSE)
    nobody.look(TOO_CLOSE, after=42)
    assert rig.nudges() == [('vision_too_close', 'paper')] and nobody.nudges() == [('vision_too_close', 'lens')]
