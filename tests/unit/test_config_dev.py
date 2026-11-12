"""The dev section of config/settings.yaml (desktalk/config/app.py: DevSettings), for the developer page, and
the developer page's messages in config/prompts.yaml.

Like the other config tests, these load from copies of the real config files in a temp folder.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from desktalk.config import ConfigError, DevCheck, DevHealthLimits, DevSettings, load_config
from tests.unit.test_config import CONFIG_FILES  # the four config files, prompts.yaml too

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def project(tmp_path):
    """A project folder holding copies of the real config files (no .env, no settings.local.yaml)."""
    (tmp_path / 'config').mkdir()
    for name in CONFIG_FILES:
        shutil.copy(ROOT / 'config' / name, tmp_path / 'config' / name)
    return tmp_path


def change_dev(project, **change):
    """Set lines of the dev section in the copy of settings.yaml."""
    path = project / 'config' / 'settings.yaml'
    data = yaml.safe_load(path.read_text())
    data['dev'].update(change)
    path.write_text(yaml.safe_dump(data, sort_keys=False))


def test_dev_settings_load(project):
    config = load_config(root=project, environ={})
    dev = config.dev
    assert isinstance(dev, DevSettings)
    assert dev.enabled is True
    assert dev.checks[0].name == 'Fast tests'
    assert [check.name for check in dev.checks] == ['Fast tests', 'Lint', 'Types', 'Dead code', 'Page tests']
    assert dev.checks[3].command == ('uv', 'run', 'vulture')
    assert dev.checks[4].command == ('npm', '--prefix', 'dev_ui', 'test')
    assert all(isinstance(check, DevCheck) for check in dev.checks)
    assert dev.checks[1].command == ('uv', 'run', 'ruff', 'check', '.')
    assert dev.test_command == ('uv', 'run', 'pytest', '-o', 'addopts=', '-q')
    assert dev.sql_max_rows == 500
    assert dev.ui_folder == Path('dev_ui/dist')
    assert (dev.vite_port, dev.poll_sec, dev.run_timeout_min, dev.sql_timeout_sec) == (5173, 1, 15, 5)
    assert isinstance(dev.health, DevHealthLimits)
    assert dev.health.pi_disk_warn_mb == 1000
    assert config.object_states.trace_pictures == 90
    assert (dev.flicker_flips_per_min, dev.sql_check_every_steps) == (4, 1000)
    assert (dev.collect_timeout_sec, dev.flips_window_min, dev.table_counts_every_sec) == (120, 10, 30)


def test_dev_check_needs_a_command(project):
    change_dev(project, checks=[{'name': 'Nothing', 'command': []}])
    with pytest.raises(ConfigError, match=r'dev\.checks\[0\]\.command must be a list with at least one item'):
        load_config(root=project, environ={})


def test_there_must_be_at_least_one_check(project):
    change_dev(project, checks=[])
    with pytest.raises(ConfigError, match=r'dev\.checks must be a list with at least one item'):
        load_config(root=project, environ={})


def test_the_test_command_cant_be_empty(project):
    change_dev(project, test_command=[])
    with pytest.raises(ConfigError, match=r'dev\.test_command must be a list with at least one item'):
        load_config(root=project, environ={})


@pytest.mark.parametrize('setting', ['run_timeout_min', 'poll_sec', 'sql_timeout_sec'])
def test_run_timeout_must_be_positive(project, setting):
    change_dev(project, **{setting: 0})
    with pytest.raises(ConfigError, match=rf'dev\.{setting} must be a number above 0'):
        load_config(root=project, environ={})


def test_the_pis_warm_line_is_below_its_hot_line(project):
    path = project / 'config' / 'settings.yaml'
    data = yaml.safe_load(path.read_text())
    data['pi'].update(hot_warning_c=90, hot_serious_c=80)
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(ConfigError, match='hot_warning_c .* must not be more than hot_serious_c'):
        load_config(root=project, environ={})


def test_the_developer_page_messages_are_there(project):
    dev = load_config(root=project, environ={}).prompts.dev
    assert dev.run_already_going == 'A run is already going.'
    assert dev.not_built == "The developer page isn't built yet. In dev_ui/, run: npm install && npm run build"
    assert 'sql_timeout_sec' in dev.query_timed_out.format(seconds=5)
    assert '5' in dev.query_timed_out.format(seconds=5)
    assert 'tests/x.py::nope' in dev.unknown_test.format(test_id='tests/x.py::nope')
    assert '../.env' in dev.outside_data_folder.format(path='../.env')
    for text in (dev.other_computer, dev.other_web_page, dev.query_not_one_statement):
        assert text and '{' not in text


def test_the_pi_log_settings_load(project):
    pi_log = load_config(root=project, environ={}).dev.pi_log
    assert pi_log.ssh[0] == 'ssh' and pi_log.ssh[-1] == 'pi'
    assert 'BatchMode=yes' in pi_log.ssh                # never asks for a password
    assert [source.name for source in pi_log.sources][:4] == ['all', 'camera', 'detector', 'app']
    assert all('{first_lines}' in s.start and '{since}' in s.resume for s in pi_log.sources)
    assert pi_log.retry_start_sec <= pi_log.retry_max_sec
    assert pi_log.keep_lines >= pi_log.first_lines


def test_two_pi_logs_cant_share_a_name(project):
    path = project / 'config' / 'settings.yaml'
    data = yaml.safe_load(path.read_text())
    data['dev']['pi_log']['sources'].append(dict(data['dev']['pi_log']['sources'][0]))
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    with pytest.raises(ConfigError, match='two Pi logs are called all'):
        load_config(root=project, environ={})


def test_the_pi_log_time_pattern_must_be_a_pattern(project):
    path = project / 'config' / 'settings.yaml'
    data = yaml.safe_load(path.read_text())
    data['dev']['pi_log']['time_pattern'] = '(unclosed'
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    with pytest.raises(ConfigError, match='time_pattern'):
        load_config(root=project, environ={})


def test_the_pi_log_messages_are_there(project):
    words = load_config(root=project, environ={}).prompts.dev.pi_log
    assert 'nope' in words.unknown_source.format(name='nope')
    assert 'ssh pi' in words.connecting.format(command='ssh pi')
    assert '7' in words.lost.format(reason='exit code 255', seconds=7)
    assert words.connected and '{' not in words.connected
