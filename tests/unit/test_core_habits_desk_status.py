"""Questions the background jobs ask before speaking up (desktalk/core/habits/desk_status.py)."""

import shutil
import time
from pathlib import Path

import pytest

from desktalk.config import load_config
from desktalk.core.habits.desk_status import DeskStatusService
from desktalk.desk_state import DeskState
from desktalk.ports import DeskStatus as DeskStatusPort
from tests.fakes import FakeFrameSource, FakePreferences

ROOT = Path(__file__).resolve().parents[2]
NOON = time.mktime((2026, 6, 15, 12, 0, 0, 0, 0, -1))
MIN = 60


@pytest.fixture(scope='module')
def config(tmp_path_factory):
    root = tmp_path_factory.mktemp('project')
    (root / 'config').mkdir()
    for name in ('settings.yaml', 'agents.yaml', 'protocols.yaml', 'prompts.yaml'):
        shutil.copy(ROOT / 'config' / name, root / 'config' / name)
    return load_config(root, environ={})


class Rig:
    def __init__(self, config, quiet=False, **reminders):
        self.desk = DeskState()
        self.camera = FakeFrameSource()
        self.camera.connected = True
        self.prefs = FakePreferences({'quiet_enabled': quiet, 'quiet_start_hour': 11, 'quiet_end_hour': 13})
        self.status = DeskStatusService(self.desk, self.camera, self.prefs,
                                        config.reminders.model_copy(update=reminders))


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
    r.desk.session_start = NOON - 14 * MIN
    assert not r.status.warm(NOON)
    r.desk.session_start = NOON - 15 * MIN                  # reminders.warmup_min
    assert r.status.warm(NOON)


def test_warmup_comes_from_the_settings(config):
    r = Rig(config, warmup_min=5)
    r.desk.session_start = NOON - 6 * MIN
    assert r.status.warm(NOON)


def test_reminders_ready_needs_warm_camera_on_and_not_quiet_hours(config):
    r = Rig(config)
    r.desk.session_start = NOON - 60 * MIN
    assert r.status.reminders_ready(NOON)
    r.camera.connected = False
    assert not r.status.reminders_ready(NOON)
    r.camera.connected = True
    r.prefs.values['quiet_enabled'] = True                   # quiet 11:00-13:00
    assert not r.status.reminders_ready(NOON)
    r.prefs.values['quiet_enabled'] = False
    r.desk.session_start = NOON - MIN
    assert not r.status.reminders_ready(NOON)


def test_user_active_when_a_person_or_movement_was_seen_lately(config):
    r = Rig(config)
    assert not r.status.user_active(NOON)
    r.desk.person_last_seen = NOON - 599                    # reminders.someone_here_sec is 600
    assert r.status.user_active(NOON)
    r.desk.person_last_seen = NOON - 600
    assert not r.status.user_active(NOON)
    r.desk.last_motion_ts = NOON - 10
    assert r.status.user_active(NOON)


def test_someone_here_window_comes_from_the_settings(config):
    r = Rig(config, someone_here_sec=60)
    r.desk.last_motion_ts = NOON - 90
    assert not r.status.user_active(NOON)


def test_open_todos_come_from_the_to_do_tracker(config):
    r = Rig(config)
    assert r.status.open_todos() == []
    r.desk.char_widgets = {
        'paper': {'type': 'todo', 'items': [{'text': 'Essay', 'done': False}, {'text': 'Email', 'done': True},
                                            {'text': 'Call mum'}]},
        'cup': {'type': 'hydration', 'items': [{'text': 'not a task'}]},
        'odd': ['not', 'a', 'tracker'],
    }
    assert r.status.open_todos() == ['Essay', 'Call mum']
