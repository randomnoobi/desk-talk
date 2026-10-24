"""The object_states section of config/settings.yaml (desktalk/config.py: ObjectStatesSettings).

Like the other config tests, these load from copies of the real config files in a temp folder.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from desktalk.config import ConfigError, load_config

ROOT = Path(__file__).resolve().parents[2]
CONFIG_FILES = ('settings.yaml', 'agents.yaml', 'protocols.yaml')


@pytest.fixture
def project(tmp_path):
    """A project folder holding copies of the real config files (no .env, no settings.local.yaml)."""
    (tmp_path / 'config').mkdir()
    for name in CONFIG_FILES:
        shutil.copy(ROOT / 'config' / name, tmp_path / 'config' / name)
    return tmp_path


def change_settings(project, **change):
    """Set (or, for None, remove) lines of the object_states section in the copy of settings.yaml."""
    path = project / 'config' / 'settings.yaml'
    data = yaml.safe_load(path.read_text())
    for key, value in change.items():
        if value is None:
            data['object_states'].pop(key)
        else:
            data['object_states'][key] = value
    path.write_text(yaml.safe_dump(data, sort_keys=False))


def test_object_states_load_as_written(project):
    s = load_config(root=project, environ={}).object_states
    assert s.on_confidence == 0.5
    assert s.off_confidence == 0.4
    assert s.window_pictures == 5
    assert s.vote_share == 0.5
    assert s.appear_sec == 3
    assert s.gone_sec == 3
    assert s.move_fraction == 0.2
    assert s.no_move_labels == ('person',)
    assert s.person_label == 'person'
    assert s.person_appear_sec == 3
    assert s.person_gone_sec == 90
    assert s.person_counts_movement is True
    assert s.snapshot_every_sec == 60
    assert s.restore_max_age_sec == 300
    assert s.database == Path('data/object_states.sqlite')


def test_off_line_above_on_line_is_an_error(project):
    change_settings(project, off_confidence=0.6)
    with pytest.raises(ConfigError) as error:
        load_config(root=project, environ={})
    assert 'off_confidence' in str(error.value)
    assert 'on_confidence' in str(error.value)


def test_a_missing_setting_stops_the_start(project):
    change_settings(project, person_gone_sec=None)
    with pytest.raises(ConfigError) as error:
        load_config(root=project, environ={})
    assert 'person_gone_sec' in str(error.value)
