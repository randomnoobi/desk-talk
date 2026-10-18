"""config/*.yaml loading, local overrides, .env values, and app preferences."""

from pathlib import Path

import pytest

from desktalk.settings import Preferences, deep_merge, load_settings

ROOT = Path(__file__).resolve().parents[2]


def test_real_config_files_load():
    s = load_settings(environ={}, dotenv=Path('/nonexistent'))
    assert s.camera.source == 'pi'
    assert s.camera.fps == 3
    assert s.pi.delete_every_min == 10
    assert s.event_log.compact_every_min == 10
    assert s.detector.model.endswith('.pt')
    # Protocols numbers from the book
    assert s.protocols.hydration.daily_goal_ml == 2400
    assert s.protocols.breaks.sit_limit_min == 30
    assert s.protocols.light.morning_window_min == 60
    # agents.yaml
    agents = s.agents_file['agents']
    assert agents['computer']['name'] == 'Computer'
    assert 'person' in s.agents_file['ignore']


def test_dot_access_and_missing_setting_message():
    s = load_settings(environ={}, dotenv=Path('/nonexistent'))
    assert s.lens.dark_below == s['lens']['dark_below']
    with pytest.raises(AttributeError, match='missing from the config files'):
        _ = s.camera.no_such_thing


def test_local_settings_override(tmp_path):
    for name in ('settings.yaml', 'agents.yaml', 'protocols.yaml'):
        (tmp_path / name).write_text((ROOT / 'config' / name).read_text())
    (tmp_path / 'settings.local.yaml').write_text('camera:\n  fps: 5\n')
    s = load_settings(config_dir=tmp_path, environ={}, dotenv=Path('/nonexistent'))
    assert s.camera.fps == 5
    assert s.camera.source == 'pi'          # untouched keys survive the merge


def test_env_values(tmp_path):
    env = {'OLLAMA_MODEL': 'gpt-oss:20b', 'OLLAMA_URL': 'http://localhost:11434/v1', 'CAMERA_SOURCE': 'phone'}
    s = load_settings(environ=env, dotenv=Path('/nonexistent'))
    assert s.llm.ollama_model == 'gpt-oss:20b'
    assert s.secrets.ollama_url == 'http://localhost:11434/v1'
    assert s.camera.source == 'pi'                    # moved to settings.yaml, .env no longer decides
    assert 'CAMERA_SOURCE' in s._env_moved


def test_dotenv_file_is_read(tmp_path):
    env_file = tmp_path / '.env'
    env_file.write_text('# comment\nGEMINI_API_KEY="abc123"\n')
    env = {}
    s = load_settings(environ=env, dotenv=env_file)
    assert s.secrets.gemini_api_key == 'abc123'


def test_deep_merge_keeps_nested_keys():
    assert deep_merge({'a': {'x': 1, 'y': 2}, 'b': 1}, {'a': {'y': 3}}) == {'a': {'x': 1, 'y': 3}, 'b': 1}


def test_preferences_saved_and_validated(tmp_path):
    s = load_settings(environ={}, dotenv=Path('/nonexistent'))
    p = Preferences(s, tmp_path / 'prefs.json')
    assert p.get('wake_time') == '07:00'
    p.update(wake_time='6:45', bed_time='23:15', quiet_enabled=True, quiet_start_hour=25)
    again = Preferences(s, tmp_path / 'prefs.json')
    assert again.get('wake_time') == '06:45'
    assert again.get('bed_time') == '23:15'
    assert again.get('quiet_enabled') is True
    assert again.get('quiet_start_hour') == 1          # 25 wraps to 1 on a 24-hour clock
    with pytest.raises(ValueError):
        p.update(wake_time='seven')
