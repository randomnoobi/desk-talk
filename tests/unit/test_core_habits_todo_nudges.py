"""The habit tracker's to-do reminders (desktalk/core/habits/tracker.py → habit_rules.task_nudges): the local day
for due dates, and the check-in naming the next open to-do each time.

The day is 15 June 2026 (local time); the clock starts at 09:00.
"""

import shutil
import time
from pathlib import Path

import pytest

from desktalk.config import load_config
from desktalk.core.agents.profiles import ProfileBook
from desktalk.core.habits.tracker import HabitTracker
from desktalk.domain import BBox, Detection, DetectionEvent, Light, Todo
from desktalk.util import at_time_today
from tests.fakes import FakeClock, FakeDeskScene, FakePreferences, InMemoryKeyValueStore, SyncEventBus

ROOT = Path(__file__).resolve().parents[2]
MIN = 60
DAY = time.mktime((2026, 6, 15, 12, 0, 0, 0, 0, -1))
LOCAL_OFFSET_HOURS = time.localtime(DAY).tm_gmtoff / 3600


def at(hhmm, days_later=0):
    return at_time_today(hhmm, DAY) + days_later * 86400


@pytest.fixture(scope='module')
def config(tmp_path_factory):
    root = tmp_path_factory.mktemp('project')
    (root / 'config').mkdir()
    for name in ('settings.yaml', 'agents.yaml', 'protocols.yaml', 'prompts.yaml'):
        shutil.copy(ROOT / 'config' / name, root / 'config' / name)
    return load_config(root, environ={})


class Rig:
    """A habit tracker whose camera started at 09:00, with someone at the desk."""

    def __init__(self, config):
        self.clock = FakeClock(start=at('09:00'), utc_offset_hours=LOCAL_OFFSET_HOURS)
        self.bus = SyncEventBus()
        self.desk = FakeDeskScene()
        self.desk.note_picture({}, self.clock.now(), person=True, moved=False, new_session=True)
        profiles = ProfileBook(config.agents_file, InMemoryKeyValueStore(), personality_max_chars=400)
        prefs = FakePreferences({'wake_time': '07:00', 'bed_time': '22:30'})
        self.tracker = HabitTracker(self.clock, self.bus, InMemoryKeyValueStore(), profiles, prefs, self.desk,
                                    config.protocols, config.habit_tracker, config.presence, config.agents,
                                    phone_counts=True, words=config.prompts.habits)

    def wait(self, seconds):
        """Time goes by with someone at the desk (one picture at the end)."""
        self.clock.advance(seconds)
        self.bus.publish(DetectionEvent(ts=self.clock.now(), frame_id='f', source='pi', size=(640, 480),
                                        detections=(Detection('person', 0.9, BBox(0, 0, 10, 10)),), person=True,
                                        light=Light(brightness=120)))

    def task_nudges(self, todos):
        return [n for n in self.tracker.nudges(self.clock.now(), todos) if n.habit == 'tasks']

    def send(self, todos):
        """The reminders send the first ready to-do nudge; returns it."""
        n = self.task_nudges(todos)[0]
        self.tracker.note_fired(n, self.clock.now())
        return n


def named(nudge):
    return nudge.text.split('"')[1]


def test_no_due_rotates_names(config):
    r = Rig(config)
    todos = [Todo('a'), Todo('b'), Todo('c')]
    r.wait(120 * MIN)
    said = []
    for _ in range(4):
        said.append(named(r.send(todos)))
        assert r.task_nudges(todos) == []                    # not again so soon
        r.wait(120 * MIN)
    assert said == ['a', 'b', 'c', 'a']


def test_check_in_every_two_hours(config):
    r = Rig(config)
    r.wait(119 * MIN)
    assert r.task_nudges([Todo('a')]) == []
    r.wait(1 * MIN)
    assert [n.key for n in r.task_nudges([Todo('a')])] == ['tasks.open']


def test_the_due_day_is_the_clocks_local_day(config):
    r = Rig(config)
    r.wait(1 * MIN)
    today = r.clock.local_day(r.clock.now())
    assert [n.key for n in r.task_nudges([Todo('essay', due=today)])] == ['tasks.due.essay.today']
    assert r.tracker.rule_state(r.clock.now()).today == today


def test_done_todo_no_reminder(config):
    r = Rig(config)
    r.wait(120 * MIN)
    today = r.clock.local_day(r.clock.now())
    assert r.task_nudges([Todo('essay', done=True, due=today), Todo('email', done=True)]) == []
