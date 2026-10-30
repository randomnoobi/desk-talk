"""settings → dev.scenarios (desktalk/config/app.py: DemoSettings, DemoScenario): the developer page's Demo tab,
which fires pretend warnings on purpose after a countdown, and its words in config/prompts.yaml → dev.scenarios."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from desktalk.config import ConfigError, DemoScenario, DemoSettings, load_config
from tests.unit.test_config import CONFIG_FILES

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def project(tmp_path):
    """A project folder holding copies of the real config files (no .env, no settings.local.yaml)."""
    (tmp_path / 'config').mkdir()
    for name in CONFIG_FILES:
        shutil.copy(ROOT / 'config' / name, tmp_path / 'config' / name)
    return tmp_path


def change_scenarios(project, **change):
    """Set lines of dev.scenarios in the copy of settings.yaml."""
    path = project / 'config' / 'settings.yaml'
    data = yaml.safe_load(path.read_text())
    data['dev']['scenarios'].update(change)
    path.write_text(yaml.safe_dump(data, sort_keys=False))


def items(project):
    return yaml.safe_load((project / 'config' / 'settings.yaml').read_text())['dev']['scenarios']['items']


def test_the_demo_settings_load(project):
    demo = load_config(root=project, environ={}).dev.scenarios
    assert isinstance(demo, DemoSettings)
    assert (demo.countdown_default_sec, demo.countdown_max_sec, demo.waiting_max) == (5, 600, 10)
    assert demo.check_every_sec == 0.25
    assert all(isinstance(item, DemoScenario) for item in demo.items)
    assert [item.fires for item in demo.items] == ['edge_warning', 'danger_zone', 'drink_reminder',
                                                   'camera_covered', 'sip', 'lost_object']
    edge = demo.items[0]
    assert (edge.name, edge.object, edge.edge, edge.distance_px) == ('cup_at_edge', 'cup', 'front', 12)
    zone = demo.items[1]
    assert (zone.object, zone.near) == ('cup', 'computer')
    assert demo.items[5].object == 'keys' and demo.items[5].minutes == 20


def test_the_demo_words_load(project):
    words = load_config(root=project, environ={}).prompts.dev.scenarios
    assert words.zone
    assert '{minutes}' in words.drink_text and '{minutes}' in words.drink_evidence


def test_the_default_countdown_is_not_more_than_the_most(project):
    change_scenarios(project, countdown_default_sec=700)
    with pytest.raises(ConfigError, match='countdown_default_sec'):
        load_config(root=project, environ={})


def test_two_scenarios_cannot_share_a_name(project):
    listed = items(project)
    change_scenarios(project, items=[*listed, listed[0]])
    with pytest.raises(ConfigError, match='cup_at_edge'):
        load_config(root=project, environ={})


@pytest.mark.parametrize(('fires', 'missing'), [('edge_warning', 'distance_px'), ('danger_zone', 'near'),
                                                ('drink_reminder', 'minutes'), ('lost_object', 'object')])
def test_a_scenario_needs_what_its_kind_fills_in(project, fires, missing):
    listed = items(project)
    one = next(item for item in listed if item['fires'] == fires)
    one.pop(missing)
    change_scenarios(project, items=listed)
    with pytest.raises(ConfigError, match=missing):
        load_config(root=project, environ={})


def test_an_unknown_kind_is_refused(project):
    listed = items(project)
    listed[0]['fires'] = 'fireworks'
    change_scenarios(project, items=listed)
    with pytest.raises(ConfigError, match='fires'):
        load_config(root=project, environ={})
