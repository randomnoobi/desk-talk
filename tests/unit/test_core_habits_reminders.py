"""Habit reminders: one nudge at a time, said by the right agent (desktalk/core/habits/reminders.py)."""

import shutil
import time
from pathlib import Path

import pytest

from desktalk.config import load_config
from desktalk.core.agents.profiles import ProfileBook
from desktalk.core.agents.roster import Roster
from desktalk.core.agents.speakers import LENS, speaker_for
from desktalk.core.habits.desk_status import DeskStatus
from desktalk.core.habits.reminders import HabitReminders, NoRules
from desktalk.core.habits.tracker import HabitTracker
from desktalk.desk_state import DeskState
from desktalk.domain import AgentProfile, BBox, Detection, DetectionEvent, Light, Nudge, NudgeSent
from desktalk.ports import PeriodicWorker
from tests.contracts.rule_nudges_contract import RuleNudgesContract
from tests.fakes import (
    FakeChatVoice,
    FakeClock,
    FakeDeskStatus,
    FakeFrameSource,
    FakeHabits,
    FakePreferences,
    FakeProfiles,
    FakeRuleNudges,
    InMemoryKeyValueStore,
    RecordingNotifier,
    StaticRoster,
    SyncEventBus,
)

ROOT = Path(__file__).resolve().parents[2]
NOON = time.mktime((2026, 6, 15, 12, 0, 0, 0, 0, -1))
MIN = 60

COMPUTER = AgentProfile(id='computer', name='Computer', habits=['breaks', 'screens'])
WATCH = AgentProfile(id='watch', name='Watch', habits=['breaks'])
CUP = AgentProfile(id='cup', name='Cup', habits=['hydration'])
SIT = Nudge('breaks', 'breaks.sit', evidence='At the desk for 34 minutes.', text='Time for a walk.',
            repeat_after_sec=15 * MIN, speakers=('breaks', 'screens'))
SIP = Nudge('hydration', 'hydration.pace', evidence='No sip in 50 minutes.', text='Have a sip?',
            repeat_after_sec=45 * MIN)
RULE_SIP = Nudge('hydration', 'rule.hydration', evidence='No sip in 50 minutes.', text='Have a sip?',
                 repeat_after_sec=45 * MIN)


@pytest.fixture(scope='module')
def config(tmp_path_factory):
    root = tmp_path_factory.mktemp('project')
    (root / 'config').mkdir()
    for name in ('settings.yaml', 'agents.yaml', 'protocols.yaml'):
        shutil.copy(ROOT / 'config' / name, root / 'config' / name)
    return load_config(root, environ={})


class Rig:
    def __init__(self, config, nudges=(SIT,), members=('computer', 'cup'), voice_text='Hey, stretch your legs!',
                 status=None, rule_nudges=()):
        self.habits = FakeHabits(nudges=nudges)
        self.rules = FakeRuleNudges(rule_nudges)
        self.status = status or FakeDeskStatus(todos=['Essay'])
        self.roster = StaticRoster(members)
        self.profiles = FakeProfiles([COMPUTER, WATCH, CUP])
        self.voice = FakeChatVoice(voice_text)
        self.notifier = RecordingNotifier()
        self.bus = SyncEventBus()
        self.reminders = HabitReminders(self.habits, self.rules, self.status, self.roster, self.profiles,
                                        self.voice, self.notifier, self.bus, config.reminders)


def test_it_is_a_background_job(config):
    assert isinstance(Rig(config).reminders, PeriodicWorker)


def test_a_nudge_is_said_by_an_agent_for_that_habit(config):
    r = Rig(config)
    wait = r.reminders.tick(NOON)
    assert wait == 30                                            # reminders.check_every_sec
    assert r.voice.asked == [('computer', SIT)]
    assert r.notifier.sent == [{'type': 'group_message', 'character': 'computer', 'text': 'Hey, stretch your legs!',
                                'habit': 'breaks', 'toast': True, 'id': r.notifier.sent[0]['id'],
                                'ts': r.notifier.sent[0]['ts']}]
    assert r.habits.fired == [(SIT, NOON)]
    sent = r.bus.of_type(NudgeSent)
    assert [(e.ts, e.habit, e.rule, e.agent, e.evidence, e.text) for e in sent] == \
        [(NOON, 'breaks', 'breaks.sit', 'computer', 'At the desk for 34 minutes.', 'Hey, stretch your legs!')]


def test_the_nudge_s_own_words_when_the_agent_has_none(config):
    r = Rig(config, voice_text=None)
    r.reminders.tick(NOON)
    assert r.notifier.texts() == ['Time for a walk.'] and r.bus.of_type(NudgeSent)[0].text == 'Time for a walk.'


def test_lens_says_it_without_the_ai_when_no_agent_speaks_for_the_habit(config):
    r = Rig(config, members=('cup',))
    r.reminders.tick(NOON)
    assert r.voice.asked == []
    assert r.notifier.sent[0]['character'] == LENS and r.notifier.texts() == ['Time for a walk.']


def test_only_the_first_nudge_goes_out(config):
    r = Rig(config, nudges=(SIP, SIT))
    r.reminders.tick(NOON)
    assert [n.key for n, _ in r.habits.fired] == ['hydration.pace']
    assert r.notifier.sent[0]['character'] == 'cup'


def test_nothing_when_there_is_nothing_to_say(config):
    r = Rig(config, nudges=())
    assert r.reminders.tick(NOON) == 30
    assert r.notifier.sent == [] and r.habits.fired == [] and r.bus.published == []


@pytest.mark.parametrize('not_now', [{'ready': False}, {'active': False}])
def test_nothing_while_not_ready_or_nobody_is_at_the_desk(config, not_now):
    r = Rig(config, status=FakeDeskStatus(**not_now))
    assert r.reminders.tick(NOON) == 30
    assert r.notifier.sent == [] and r.habits.fired == []


def test_at_least_the_minimum_gap_between_reminders(config):
    r = Rig(config)
    r.reminders.tick(NOON)
    r.reminders.tick(NOON + 9 * MIN)                            # reminders.min_gap_sec is 10 minutes
    assert len(r.notifier.sent) == 1
    r.reminders.tick(NOON + 10 * MIN)
    assert len(r.notifier.sent) == 2


def test_check_time_and_gap_come_from_the_settings(config):
    r = Rig(config)
    r.reminders = HabitReminders(r.habits, r.rules, r.status, r.roster, r.profiles, r.voice, r.notifier, r.bus,
                                 config.reminders.model_copy(update={'check_every_sec': 5, 'min_gap_sec': 60}))
    assert r.reminders.tick(NOON) == 5
    r.reminders.tick(NOON + 60)
    assert len(r.notifier.sent) == 2


class TodoHabits(FakeHabits):
    def nudges(self, now, open_todos):
        self.asked_with = list(open_todos)
        return super().nudges(now, open_todos)


def test_the_open_to_dos_go_to_the_rules(config):
    r = Rig(config)
    r.habits = TodoHabits(nudges=(SIT,))
    r.reminders = HabitReminders(r.habits, r.rules, r.status, r.roster, r.profiles, r.voice, r.notifier, r.bus,
                                 config.reminders)
    r.reminders.tick(NOON)
    assert r.habits.asked_with == ['Essay']


def test_rule_nudge_is_said_and_noted(config):
    r = Rig(config, nudges=(), rule_nudges=(RULE_SIP,))
    r.reminders.tick(NOON)
    assert r.notifier.texts() == ['Hey, stretch your legs!'] and r.notifier.sent[0]['character'] == 'cup'
    assert [e.rule for e in r.bus.of_type(NudgeSent)] == ['rule.hydration']
    assert r.rules.fired == [(RULE_SIP, NOON)]
    assert r.habits.fired == [(RULE_SIP, NOON)]                  # the old tracker counts every nudge sent


def test_rule_nudges_go_before_old_ones(config):
    r = Rig(config, nudges=(SIT,), rule_nudges=(RULE_SIP,))
    r.reminders.tick(NOON)
    assert [e.rule for e in r.bus.of_type(NudgeSent)] == ['rule.hydration']
    assert len(r.notifier.sent) == 1


def test_rule_nudges_respect_the_app_wide_gap(config):
    r = Rig(config, nudges=(), rule_nudges=(RULE_SIP,))
    r.reminders.tick(NOON)
    r.reminders.tick(NOON + 9 * MIN)                            # reminders.min_gap_sec is 10 minutes
    assert len(r.notifier.sent) == 1 and len(r.rules.fired) == 1


def test_no_rule_nudges_while_away(config):
    r = Rig(config, rule_nudges=(RULE_SIP,), status=FakeDeskStatus(active=False))
    r.reminders.tick(NOON)
    assert r.rules.calls == 0 and r.notifier.sent == []


def test_speaker_is_an_agent_for_that_habit_or_lens():
    p = FakeProfiles([COMPUTER, WATCH, CUP])
    assert speaker_for(('breaks',), ['cup', 'watch', 'computer'], p) == 'watch'      # first member in join order
    assert speaker_for(('food', 'breaks'), ['cup', 'computer'], p) == 'computer'
    assert speaker_for(('screens', 'breaks'), ['watch', 'computer'], p) == 'computer'  # first habit first
    assert speaker_for(('hydration',), ['computer'], p) == LENS
    assert speaker_for(('light',), [], p) == LENS
    assert speaker_for(('breaks',), ['ghost'], p) == LENS                          # no profile: skipped


# ── the real parts together ───────────────────────────────────────────
class Desk:
    """Pictures → roster and habit tracker → reminders, with a fake camera, AI voice and chat."""

    def __init__(self, config, start):
        self.clock = FakeClock(start=start)
        self.bus = SyncEventBus()
        self.desk = DeskState()
        self.desk.session_start = start
        self.camera = FakeFrameSource()
        self.camera.connected = True
        prefs = FakePreferences({'wake_time': '07:00', 'bed_time': '22:30', 'quiet_enabled': False,
                                 'quiet_start_hour': 22, 'quiet_end_hour': 7})
        self.profiles = ProfileBook(config.agents_file, InMemoryKeyValueStore(), personality_max_chars=400)
        self.roster = Roster(self.bus, self.profiles, self.desk, config.agents, config.presence)
        self.habits = HabitTracker(self.clock, self.bus, InMemoryKeyValueStore(), self.profiles, prefs, self.desk,
                                   config.protocols, config.habit_tracker, config.presence, config.agents,
                                   phone_counts=True)
        self.status = DeskStatus(self.desk, self.camera, prefs, config.reminders)
        self.voice = FakeChatVoice('35 minutes of sitting! Walk five minutes, leave your phone.')
        self.notifier = RecordingNotifier(self.clock)
        self.reminders = HabitReminders(self.habits, FakeRuleNudges(), self.status, self.roster, self.profiles,
                                        self.voice, self.notifier, self.bus, config.reminders)

    def run(self, seconds, *labels):
        """One picture a second (keeps the long runs quick), with someone at the desk."""
        for _ in range(int(seconds)):
            now = self.clock.now()
            dets = tuple(Detection(label, 0.9, BBox(100, 100, 200, 200)) for label in labels)
            self.desk.person_last_seen = now                  # the camera producer does this
            self.bus.publish(DetectionEvent(ts=now, frame_id='f', source='pi', size=(640, 480), detections=dets,
                                            person=True, light=Light(brightness=120)))
            self.clock.advance(1)


def test_sitting_too_long_gets_a_break_nudge_from_computer(config):
    d = Desk(config, start=NOON)
    d.run(35 * MIN, 'laptop')
    d.habits.log('hydration', 'glass', quiet=True)            # so water isn't the most urgent thing
    d.habits.log('light', 'sunlight', quiet=True)
    d.reminders.tick(d.clock.now())
    assert [m['character'] for m in d.notifier.sent] == ['computer']
    assert d.notifier.sent[0]['text'] == '35 minutes of sitting! Walk five minutes, leave your phone.'
    agent, nudge = d.voice.asked[0]
    assert agent == 'computer' and nudge.key == 'breaks.sit' and '35 minutes' in nudge.evidence
    assert [e.rule for e in d.bus.of_type(NudgeSent)] == ['breaks.sit']
    # nothing again right away: min gap between reminders, and the rule waits too
    d.reminders.tick(d.clock.now() + 60)
    assert len(d.notifier.sent) == 1
    assert 'breaks.sit' not in [n.key for n in d.habits.nudges(d.clock.now(), [])]


def test_no_reminders_while_warming_up_or_camera_off(config):
    d = Desk(config, start=NOON)
    d.run(5 * MIN, 'laptop')
    d.reminders.tick(d.clock.now())                           # only 5 minutes of watching
    assert d.notifier.sent == []
    d.run(30 * MIN, 'laptop')
    d.camera.connected = False
    d.reminders.tick(d.clock.now())
    assert d.notifier.sent == []
    d.camera.connected = True
    d.reminders.tick(d.clock.now())
    assert len(d.notifier.sent) == 1


class TestNoRules(RuleNudgesContract):
    @pytest.fixture
    def rule_nudges(self):
        return NoRules()
