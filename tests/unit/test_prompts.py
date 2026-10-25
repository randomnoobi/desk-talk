"""The wording in config/prompts.yaml (desktalk/config/prompts.py): every blank gets a value, and a blank
the code doesn't fill in stops startup with the text's name."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from desktalk.config import ConfigError, load_config
from desktalk.config.prompts import BLANKS, Prompts, prompt_texts, prompt_values

ROOT = Path(__file__).resolve().parents[2]
PROMPTS_FILE = ROOT / 'config' / 'prompts.yaml'
CONFIG_FILES = ('settings.yaml', 'agents.yaml', 'protocols.yaml', 'prompts.yaml')


@pytest.fixture
def project(tmp_path):
    """A project folder holding copies of the real config files."""
    (tmp_path / 'config').mkdir()
    for name in CONFIG_FILES:
        shutil.copy(ROOT / 'config' / name, tmp_path / 'config' / name)
    return tmp_path


def edit(project, name, old, new):
    path = project / 'config' / name
    text = path.read_text()
    assert text.count(old) == 1, old
    path.write_text(text.replace(old, new))


def as_written() -> Prompts:
    """config/prompts.yaml as it is in the file, before the settings are filled in."""
    return Prompts.model_validate(yaml.safe_load(PROMPTS_FILE.read_text()))


def test_every_prompt_placeholder_has_a_value():
    """Fill every text with the real settings and a stand-in for each blank its code fills in: nothing is
    missing, and the only braces left are the ones written twice on purpose ({{ in the JSON examples})."""
    values = prompt_values(load_config(root=ROOT, environ={}))
    checked = 0
    for where, text in prompt_texts(as_written()):
        stand_ins = {name: 1 for name in BLANKS.get(where, ())}
        filled = text.format(**values, **stand_ins)
        assert filled.count('{') == text.count('{{'), where
        checked += 1
    assert checked > 150


def test_the_blank_lists_name_real_texts_and_no_setting():
    places = {where.split('[')[0] for where, _ in prompt_texts(as_written())}
    assert set(BLANKS) <= places
    settings = set(prompt_values(load_config(root=ROOT, environ={})))
    for where, names in BLANKS.items():
        assert not settings & set(names), where


def test_settings_numbers_are_filled_in_when_loading(project):
    edit(project, 'protocols.yaml', 'on_waking_ml: 600', 'on_waking_ml: 450')
    edit(project, 'settings.yaml', 'sunlight_default_min: 10 ', 'sunlight_default_min: 15 ')
    prompts = load_config(root=project, environ={}).prompts
    assert prompts.habits.hydration_morning.tip == ('Drink 18 to 24 ounces (about 450 ml) of water upon waking, '
                                                    'before coffee.')
    assert 'about 0.5 L (18–24 oz)' in prompts.habits.hydration_morning.text      # 450 ml, as liters
    assert 'use 15 if unsure' in prompts.trackers.actions.light
    assert 'walk every 30 minutes' in prompts.trackers.breaks.format(count=1, last='none yet today', sitting_min=3)


def test_texts_with_blanks_keep_them_and_ready_texts_need_nothing(project):
    prompts = load_config(root=project, environ={}).prompts
    assert prompts.alert.first.format(situation='cup near laptop', name='Cup', object='cup').startswith(
        'SITUATION: cup near laptop')
    assert '{"visible":["label",...]}' in prompts.lost_items.scan.format(item_names='keys')
    assert '{"continue": true|false, "next": "<name>"}' in prompts.small_talk.director_system
    assert prompts.vision.habit_scene.splitlines()[1].startswith('{"posture":')


def test_a_blank_the_code_does_not_fill_in_names_the_text(project):
    edit(project, 'prompts.yaml', 'Say ONE thing as {name} about', 'Say ONE thing as {nmae} about')
    with pytest.raises(ConfigError) as err:
        load_config(root=project, environ={})
    assert str(err.value) == ('config/prompts.yaml: alert.first uses the blank {nmae}, which isn\'t filled in there '
                              '(it can use {situation}, {name} or {object}, or the settings ones at the top of '
                              'the file)')


def test_a_blank_in_a_text_without_blanks_names_the_text(project):
    edit(project, 'prompts.yaml', 'Glare is gone. Thanks!', 'Glare is gone. Thanks, {name}!')
    with pytest.raises(ConfigError, match=r'^config/prompts.yaml: camera.fixed.glare uses the blank \{name\}, which '
                                          r"isn't filled in there \(it can only use the settings ones at the top of "
                                          r'the file\)$'):
        load_config(root=project, environ={})


def test_a_brace_without_its_pair_names_the_text(project):
    edit(project, 'prompts.yaml', 'No video from the phone', 'No video from the phone {')
    with pytest.raises(ConfigError, match=r'^config/prompts.yaml: camera.titles.lost.title has a \{ or \} without its '
                                          r'pair \(write \{\{ or \}\} for a real brace\)$'):
        load_config(root=project, environ={})


def test_a_missing_text_is_named(project):
    edit(project, 'prompts.yaml', '  pi_clock_behind: behind\n', '')
    with pytest.raises(ConfigError, match=r'^config/prompts.yaml: lens.pi_clock_behind is missing$'):
        load_config(root=project, environ={})


def test_a_misspelled_text_name_is_an_error(project):
    edit(project, 'prompts.yaml', '  pi_clock_behind: behind\n', '  pi_clock_behnd: behind\n')
    with pytest.raises(ConfigError) as err:
        load_config(root=project, environ={})
    assert 'config/prompts.yaml: lens.pi_clock_behnd is not a known setting (did you mean lens.pi_clock_behind?)' \
        in str(err.value)


def test_prompts_yaml_is_named_when_it_is_missing(project):
    (project / 'config' / 'prompts.yaml').unlink()
    with pytest.raises(ConfigError, match='^config/prompts.yaml is missing$'):
        load_config(root=project, environ={})


def test_settings_yaml_may_not_set_prompts(project):
    (project / 'config' / 'settings.local.yaml').write_text('prompts:\n  replies: {}\n')
    with pytest.raises(ConfigError, match='^config/settings.local.yaml: prompts is not a known setting'):
        load_config(root=project, environ={})
