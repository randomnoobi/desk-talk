"""The sensitivity slider, shared by the roster and the habits."""

import logging
import shutil
from pathlib import Path

import pytest

from desktalk.config import load_config
from desktalk.core.agents.sensitivity import Slider, confidence_needed
from desktalk.domain import AgentProfile
from tests.fakes import FakePreferences

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope='module')
def agents(tmp_path_factory):
    root = tmp_path_factory.mktemp('project')
    (root / 'config').mkdir()
    for name in ('settings.yaml', 'agents.yaml', 'protocols.yaml', 'prompts.yaml'):
        shutil.copy(ROOT / 'config' / name, root / 'config' / name)
    return load_config(root, environ={}).agents


def test_the_step_is_the_one_the_slider_picks(agents):
    prefs = FakePreferences({'sensitivity': 0})
    slider = Slider(agents, prefs)
    assert slider.step().name == 'Picky'
    prefs.values['sensitivity'] = 4
    assert slider.step().name == 'Eager'
    del prefs.values['sensitivity']
    assert slider.step().name == 'Normal'


def test_a_bad_value_is_logged_once_and_uses_the_default_step(agents, caplog):
    slider = Slider(agents, FakePreferences({'sensitivity': 'picky'}))
    with caplog.at_level(logging.WARNING, logger='desktalk.core.agents.sensitivity'):
        assert slider.step().name == 'Normal' and slider.step().name == 'Normal'
    assert len(caplog.records) == 1 and "'picky'" in caplog.records[0].getMessage()


def test_an_agents_own_min_confidence_counts_only_when_stricter(agents):
    normal = agents.default_step()                                   # 0.45
    assert confidence_needed(normal, None) == 0.45
    assert confidence_needed(normal, AgentProfile(id='cup', name='Cup', min_confidence=0.55)) == 0.55
    assert confidence_needed(normal, AgentProfile(id='cup', name='Cup', min_confidence=0.3)) == 0.45
    assert confidence_needed(normal, AgentProfile(id='cup', name='Cup')) == 0.45
