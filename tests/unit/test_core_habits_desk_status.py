"""Questions the background jobs ask before speaking up (desktalk/core/habits/desk_status.py)."""

import shutil
import time
from pathlib import Path

import pytest

from desktalk.config import load_config
from desktalk.core.habits.desk_status import DeskStatusService
from desktalk.domain import Todo
from desktalk.ports import DeskStatus as DeskStatusPort
from tests.fakes import (
    FakeCharacterMemory,
    FakeClock,
    FakeDeskScene,
    FakeFrameSource,
    FakeObjectStates,
    FakePreferences,
)

ROOT = Path(__file__).resolve().parents[2]
NOON = time.mktime((2026, 6, 15, 12, 0, 0, 0, 0, -1))
CLOCK = FakeClock(NOON, utc_offset_hours=time.localtime(NOON).tm_gmtoff / 3600)   # local noon on the clock too
MIN = 60


@pytest.fixture(scope='module')
def config(tmp_path_factory):
    root = tmp_path_factory.mktemp('project')
    (root / 'config').mkdir()
    for name in ('settings.yaml', 'agents.yaml', 'protocols.yaml', 'prompts.yaml'):
        shutil.copy(ROOT / 'config' / name, root / 'config' / name)
    return load_config(root, environ={})


class FakeDesk(FakeDeskScene, FakeCharacterMemory):
    """The desk as the status sees it: the scene and the tracker panels."""

    def __init__(self):
        FakeDeskScene.__init__(self)
        FakeCharacterMemory.__init__(self)

    def camera_started(self, at):
        self.note_picture({}, at, person=False, moved=False, new_session=True)


class Rig:
    def __init__(self, config, quiet=False, person_label='person', **reminders):
        self.desk = FakeDesk()
        self.camera = FakeFrameSource()
        self.camera.connected = True
        self.prefs = FakePreferences({'quiet_enabled': quiet, 'quiet_start_hour': 11, 'quiet_end_hour': 13})
        self.states = FakeObjectStates()
        self.status = DeskStatusService(self.desk, self.camera, self.prefs,
                                        config.reminders.model_copy(update=reminders), self.states,
                                        person_label=person_label, clock=CLOCK)


def test_it_is_a_desk_status(config):
    assert isinstance(Rig(config).status, DeskStatusPort)


def test_camera_connected_follows_the_camera(config):
    r = Rig(config)
    assert r.status.camera_connected() is True
    r.camera.connected = False
    assert r.status.camera_connected() is False


def test_warm_after_the_camera_has_watched_for_the_warmup(config):
    r = Rig(config)
    assert not r.status.warm(NOON)                          # the camera hasn't started
    r.desk.camera_started(NOON - 14 * MIN)
    assert not r.status.warm(NOON)
    r.desk.camera_started(NOON - 15 * MIN)                  # reminders.warmup_min
    assert r.status.warm(NOON)


def test_warmup_comes_from_the_settings(config):
    r = Rig(config, warmup_min=5)
    r.desk.camera_started(NOON - 6 * MIN)
    assert r.status.warm(NOON)


def test_reminders_ready_needs_warm_camera_on_and_not_quiet_hours(config):
    r = Rig(config)
    r.desk.camera_started(NOON - 60 * MIN)
    assert r.status.reminders_ready(NOON)
    r.camera.connected = False
    assert not r.status.reminders_ready(NOON)
    r.camera.connected = True
    r.prefs.values['quiet_enabled'] = True                   # quiet 11:00-13:00
    assert not r.status.reminders_ready(NOON)
    r.prefs.values['quiet_enabled'] = False
    r.desk.camera_started(NOON - MIN)
    assert not r.status.reminders_ready(NOON)


def test_user_active_follows_the_persons_state(config):
    r = Rig(config)
    assert not r.status.user_active(NOON)                   # no person seen yet
    r.states.set('person', 'UNKNOWN', None)
    assert not r.status.user_active(NOON)
    r.states.set('person', 'PRESENT', NOON - 10)
    assert r.status.user_active(NOON)
    r.states.set('person', 'ABSENT', NOON - 5)
    assert not r.status.user_active(NOON)


def test_the_persons_word_comes_from_the_settings(config):
    r = Rig(config, person_label='human')
    r.states.set('person', 'PRESENT', NOON - 10)
    assert not r.status.user_active(NOON)
    r.states.set('human', 'PRESENT', NOON - 10)
    assert r.status.user_active(NOON)


def test_open_todos_come_from_the_to_do_tracker(config):
    r = Rig(config)
    assert r.status.open_todos() == []
    r.desk.set_widget('paper', {'type': 'todo', 'items': [{'text': 'Essay', 'done': False},
                                                          {'text': 'Email', 'done': True}, {'text': 'Call mum'}]})
    r.desk.set_widget('cup', {'type': 'hydration', 'items': [{'text': 'not a task'}]})
    assert r.status.open_todos() == [Todo('Essay'), Todo('Call mum')]
