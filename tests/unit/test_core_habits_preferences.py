"""Choices made in the app: wake time, bedtime and quiet hours (desktalk/core/habits/preferences.py)."""

import logging
import shutil
import time
from pathlib import Path

import pytest

from desktalk.config import load_config
from desktalk.core.habits.preferences import PreferencesFile, quiet_hours_now
from desktalk.ports import Preferences as PreferencesPort
from tests.fakes import FakePreferences, InMemoryKeyValueStore

ROOT = Path(__file__).resolve().parents[2]
DEFAULTS = {'wake_time': '07:00', 'bed_time': '22:30', 'quiet_enabled': False, 'quiet_start_hour': 22,
            'quiet_end_hour': 7}


@pytest.fixture(scope='module')
def config(tmp_path_factory):
    root = tmp_path_factory.mktemp('project')
    (root / 'config').mkdir()
    for name in ('settings.yaml', 'agents.yaml', 'protocols.yaml', 'prompts.yaml'):
        shutil.copy(ROOT / 'config' / name, root / 'config' / name)
    return load_config(root, environ={})


@pytest.fixture
def store():
    return InMemoryKeyValueStore()


def prefs_from(config, store):
    return PreferencesFile(store, config.protocols.schedule, config.reminders.quiet_hours)


def test_it_is_a_preferences_view(config, store):
    assert isinstance(prefs_from(config, store), PreferencesPort)


def test_defaults_come_from_protocols_and_settings(config, store):
    p = prefs_from(config, store)
    assert p.all() == DEFAULTS
    assert p.get('wake_time') == '07:00' and p.get('quiet_end_hour') == 7


def test_defaults_follow_the_config_files(config, store):
    schedule = config.protocols.schedule.model_copy(update={'wake_time': '06:15'})
    quiet = config.reminders.quiet_hours.model_copy(update={'enabled': True, 'start_hour': 21})
    p = PreferencesFile(store, schedule, quiet)
    assert p.get('wake_time') == '06:15' and p.get('quiet_enabled') is True and p.get('quiet_start_hour') == 21


def test_changes_are_saved_and_validated(config, store):
    p = prefs_from(config, store)
    out = p.update({'wake_time': '6:45', 'bed_time': '23:15', 'quiet_enabled': 1, 'quiet_start_hour': 25})
    expected = {'wake_time': '06:45', 'bed_time': '23:15', 'quiet_enabled': True, 'quiet_start_hour': 1,
                'quiet_end_hour': 7}
    assert out == expected
    assert store.data == {'preferences': expected}             # data/preferences.json, as before
    again = prefs_from(config, store)
    assert again.all() == expected
    assert again.get('quiet_enabled') is True


def test_a_bad_time_changes_nothing(config, store):
    p = prefs_from(config, store)
    with pytest.raises(ValueError, match='not a time like 07:30'):
        p.update({'wake_time': '06:00', 'bed_time': 'seven'})
    assert p.all() == DEFAULTS and store.data == {}


def test_a_bad_hour_is_refused(config, store):
    p = prefs_from(config, store)
    with pytest.raises(ValueError):
        p.update({'quiet_start_hour': 'late'})
    assert p.all() == DEFAULTS


@pytest.mark.parametrize('changes', [{'quiet_start_hour': [22]}, {'quiet_end_hour': {'h': 7}},
                                     {'quiet_start_hour': float('inf')}])
def test_a_value_of_the_wrong_kind_is_a_value_error_and_changes_nothing(config, store, changes):
    p = prefs_from(config, store)
    with pytest.raises(ValueError):
        p.update(changes)
    assert p.all() == DEFAULTS and store.data == {}


class BrokenStore(InMemoryKeyValueStore):
    def save(self, key, value):
        raise OSError('disk full')


def test_a_change_that_cannot_be_saved_changes_nothing(config):
    p = prefs_from(config, BrokenStore())
    with pytest.raises(OSError):
        p.update({'wake_time': '06:00'})
    assert p.get('wake_time') == '07:00'


def test_unknown_and_empty_changes_are_ignored(config, store):
    p = prefs_from(config, store)
    assert p.update({'favorite_color': 'teal', 'wake_time': None}) == DEFAULTS


def test_preferences_file_from_before_still_loads(config):
    store = InMemoryKeyValueStore({'preferences': {'wake_time': '08:00', 'bed_time': '00:30', 'quiet_enabled': True,
                                                   'quiet_start_hour': 23, 'quiet_end_hour': 8, 'old_thing': 1}})
    p = prefs_from(config, store)
    assert p.all() == {'wake_time': '08:00', 'bed_time': '00:30', 'quiet_enabled': True, 'quiet_start_hour': 23,
                       'quiet_end_hour': 8}


def test_a_broken_saved_value_falls_back_to_the_default(config, caplog):
    store = InMemoryKeyValueStore({'preferences': {'wake_time': 'soon', 'quiet_end_hour': 9}})
    with caplog.at_level(logging.WARNING):
        p = prefs_from(config, store)
    assert p.get('wake_time') == '07:00' and p.get('quiet_end_hour') == 9
    assert 'wake_time' in caplog.text


def test_saved_preferences_that_are_not_a_dict_are_ignored(config):
    p = prefs_from(config, InMemoryKeyValueStore({'preferences': ['nope']}))
    assert p.all() == DEFAULTS


def test_changing_the_copy_from_all_changes_nothing(config, store):
    p = prefs_from(config, store)
    p.all()['wake_time'] = '05:00'
    assert p.get('wake_time') == '07:00'


def local(hour):
    return time.mktime((2026, 6, 15, hour, 30, 0, 0, 0, -1))


def test_quiet_hours_across_midnight():
    prefs = FakePreferences({'quiet_enabled': True, 'quiet_start_hour': 22, 'quiet_end_hour': 7})
    assert quiet_hours_now(prefs, local(23)) and quiet_hours_now(prefs, local(6))
    assert not quiet_hours_now(prefs, local(7)) and not quiet_hours_now(prefs, local(12))


def test_quiet_hours_in_the_day_and_switched_off():
    prefs = FakePreferences({'quiet_enabled': True, 'quiet_start_hour': 13, 'quiet_end_hour': 15})
    assert quiet_hours_now(prefs, local(14)) and not quiet_hours_now(prefs, local(15))
    prefs.values['quiet_enabled'] = False
    assert not quiet_hours_now(prefs, local(14))
