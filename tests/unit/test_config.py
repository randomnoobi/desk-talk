"""Typed settings (desktalk/config.py): loading config/*.yaml and .env, and clear messages for bad values.

Every test works on copies of the real config files in a temp folder and passes environ={},
so your own .env and config/settings.local.yaml never change the result.
"""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from pydantic import BaseModel, SecretStr, ValidationError

from desktalk.config import ConfigError, load_config
from desktalk.settings import load_settings

ROOT = Path(__file__).resolve().parents[2]
CONFIG_FILES = ('settings.yaml', 'agents.yaml', 'protocols.yaml')


@pytest.fixture
def project(tmp_path):
    """A project folder holding copies of the real config files (no .env, no settings.local.yaml)."""
    (tmp_path / 'config').mkdir()
    for name in CONFIG_FILES:
        shutil.copy(ROOT / 'config' / name, tmp_path / 'config' / name)
    return tmp_path


def write_local(project, text):
    """Your own overrides: config/settings.local.yaml."""
    (project / 'config' / 'settings.local.yaml').write_text(text)


def test_real_config_files_load(project):
    cfg = load_config(root=project, environ={})
    assert cfg.camera.source == 'pi'
    assert cfg.camera.fps == 3
    assert cfg.pi.delete_every_min == 10
    assert cfg.lens.dark_below == 45
    assert cfg.lost_items.items['keys'].labels == ('key', 'remote')
    assert cfg.protocols.hydration.daily_goal_ml == 2400
    assert cfg.protocols.schedule.latitude is None
    assert cfg.agents_file.agents['computer'].name == 'Computer'
    assert 'person' in cfg.agents_file.ignore


def test_local_file_overrides_only_the_lines_it_has(project):
    write_local(project, 'camera:\n  fps: 5\nreminders:\n  quiet_hours:\n    enabled: true\n')
    cfg = load_config(root=project, environ={})
    assert cfg.camera.fps == 5
    assert cfg.camera.source == 'pi'                     # untouched keys in the same section survive
    assert cfg.reminders.quiet_hours.enabled is True
    assert cfg.reminders.quiet_hours.start_hour == 22    # nested sections are merged, not replaced


def test_pi_delete_every_min_0_means_never(project):
    write_local(project, 'pi:\n  delete_every_min: 0\n')
    assert load_config(root=project, environ={}).pi.delete_every_min == 0


def test_bad_value_names_the_file_the_setting_and_what_is_wrong(project):
    write_local(project, 'camera:\n  fps: fast\n')
    with pytest.raises(ConfigError) as err:
        load_config(root=project, environ={})
    assert str(err.value) == 'config/settings.local.yaml: camera.fps must be a number above 0 (got "fast")'


def test_bad_value_in_agents_file_names_that_file(project):
    agents = project / 'config' / 'agents.yaml'
    agents.write_text(agents.read_text().replace('temperature: 0.75', 'temperature: warm'))
    with pytest.raises(ConfigError) as err:
        load_config(root=project, environ={})
    assert str(err.value) == 'config/agents.yaml: agents.cup.temperature must be a number from 0 to 1 (got "warm")'


def test_misspelled_setting_is_an_error_not_ignored(project):
    write_local(project, 'camera:\n  fsp: 5\n')
    with pytest.raises(ConfigError) as err:
        load_config(root=project, environ={})
    assert str(err.value) == ('config/settings.local.yaml: camera.fsp is not a known setting '
                              '(did you mean camera.fps?)')


def test_config_is_read_only(project):
    cfg = load_config(root=project, environ={})
    with pytest.raises(ValidationError, match='frozen'):
        cfg.camera.fps = 10
    with pytest.raises(AttributeError):
        cfg.camera.webcam_indices.append(1)          # lists come back as tuples


def test_api_keys_come_from_dotenv_and_are_hidden_when_printed(project):
    (project / '.env').write_text('# comment\nGEMINI_API_KEY="abc123"\nOLLAMA_URL=http://localhost:11434/v1\n')
    cfg = load_config(root=project, environ={})
    assert cfg.secrets.gemini_api_key.get_secret_value() == 'abc123'
    assert cfg.secrets.openai_api_key.get_secret_value() == ''
    assert cfg.secrets.ollama_url == 'http://localhost:11434/v1'
    assert 'abc123' not in repr(cfg)


def test_dotenv_can_still_set_model_names(project):
    (project / '.env').write_text('OLLAMA_MODEL=llama3.2\nYOLO_MODEL=models/yolo26s-objv1-150.pt\n')
    cfg = load_config(root=project, environ={'OLLAMA_MODEL': 'gpt-oss:20b'})
    assert cfg.llm.ollama_model == 'gpt-oss:20b'           # the environment wins over .env
    assert cfg.detector.model == 'models/yolo26s-objv1-150.pt'
    assert cfg.env_applied == {'OLLAMA_MODEL': 'llm.ollama_model', 'YOLO_MODEL': 'detector.model'}


def test_old_dotenv_settings_that_moved_to_yaml_are_listed_not_used(project):
    cfg = load_config(root=project, environ={'CAMERA_SOURCE': 'phone'})
    assert cfg.camera.source == 'pi'
    assert cfg.env_moved == ('CAMERA_SOURCE',)


@pytest.mark.parametrize('local, setting', [
    ('secrets:\n  gemini_api_key: abc\n', 'secrets'),
    ('llm:\n  gemini_api_key: abc\n', 'llm.gemini_api_key'),
])
def test_api_keys_in_yaml_are_refused(project, local, setting):
    write_local(project, local)
    with pytest.raises(ConfigError) as err:
        load_config(root=project, environ={})
    assert str(err.value) == (f'config/settings.local.yaml: {setting} is not a known setting '
                              f'(API keys go in .env, see .env.example)')



def test_paths_stay_as_written_and_resolve_against_the_project_folder(project):
    cfg = load_config(root=project, environ={})
    assert cfg.event_log.folder == Path('data/events')
    assert cfg.resolve(cfg.event_log.folder) == project / 'data' / 'events'
    assert cfg.resolve('~/photos') == Path.home() / 'photos'
    assert cfg.resolve('/var/desk') == Path('/var/desk')



def test_missing_setting_names_the_file_it_belongs_in(project):
    settings = project / 'config' / 'settings.yaml'
    lines = settings.read_text().splitlines(keepends=True)
    settings.write_text(''.join(line for line in lines if 'pictures analysed per second' not in line))
    with pytest.raises(ConfigError) as err:
        load_config(root=project, environ={})
    assert str(err.value) == 'config/settings.yaml: camera.fps is missing'



@pytest.mark.parametrize('local, problem', [
    ('camera:\n  source: usb\n', 'camera.source must be one of: pi, phone, webcam (got "usb")'),
    ('agents:\n  announce: maybe\n', 'agents.announce must be true or false (got "maybe")'),
    ('agents:\n  max_agents: 2.5\n', 'agents.max_agents must be a whole number above 0 (got 2.5)'),
    ('camera:\n  disconnect_after_sec: -1\n', 'camera.disconnect_after_sec must be a number above 0 (got -1)'),
    ('pi:\n  keep_last_min: -5\n', 'pi.keep_last_min must be a number, 0 or more (got -5)'),
    ('camera:\n  webcam_indices: [0, front]\n',
     'camera.webcam_indices[1] must be a whole number, 0 or more (got "front")'),
    ('reminders:\n  quiet_hours:\n    start_hour: 24\n',
     'reminders.quiet_hours.start_hour must be a whole number from 0 to 23 (got 24)'),
    ('lens: 5\n', 'lens must be a group of settings (got 5)'),
])
def test_each_kind_of_bad_value_is_described_in_plain_words(project, local, problem):
    write_local(project, local)
    with pytest.raises(ConfigError) as err:
        load_config(root=project, environ={})
    assert str(err.value) == f'config/settings.local.yaml: {problem}'


def test_several_problems_are_all_listed_one_per_line(project):
    write_local(project, 'camera:\n  fps: 0\n  fsp: 5\n')
    with pytest.raises(ConfigError) as err:
        load_config(root=project, environ={})
    assert str(err.value).splitlines() == [
        'config/settings.local.yaml: camera.fps must be a number above 0 (got 0)',
        'config/settings.local.yaml: camera.fsp is not a known setting (did you mean camera.fps?)',
    ]



@pytest.mark.parametrize('local, problem', [
    ('camera:\n  fps: [3\n', 'config/settings.local.yaml: not valid YAML near line 3'),
    ('- fps: 3\n', 'config/settings.local.yaml should contain "key: value" settings'),
])
def test_a_file_that_is_not_settings_gives_a_clear_message(project, local, problem):
    write_local(project, local)
    with pytest.raises(ConfigError) as err:
        load_config(root=project, environ={})
    assert str(err.value).startswith(problem)


def test_missing_config_file_is_named(project):
    (project / 'config' / 'agents.yaml').unlink()
    with pytest.raises(ConfigError, match='^config/agents.yaml is missing$'):
        load_config(root=project, environ={})



# ── settings added for the refactor ──
def test_settings_for_keeping_things_running_are_there(project):
    cfg = load_config(root=project, environ={})
    detector = cfg.pi.detector
    assert detector.format in ('ncnn', 'pt')
    assert isinstance(detector.half, bool)
    assert isinstance(detector.image_size, int)
    assert urlsplit(detector.url).hostname == '127.0.0.1'        # the container is never reachable from the network
    assert isinstance(detector.failures_before_pause, int)
    assert detector.wait_per_picture_sec > 0 and detector.health_check_every_sec > 0
    for start, most in [(detector.pause_start_sec, detector.pause_max_sec),
                        (cfg.pi.upload_retry_start_sec, cfg.pi.upload_retry_max_sec),
                        (cfg.supervisor.restart_start_sec, cfg.supervisor.restart_max_sec),
                        (cfg.llm.retry_start_sec, cfg.llm.retry_max_sec)]:
        assert 0 < start <= most
    assert cfg.pi.hot_warning_c > 0 and cfg.pi.clock_off_warning_sec > 0
    assert cfg.llm.timeout_sec > 0
    assert isinstance(cfg.llm.breaker_failures, int) and cfg.llm.breaker_open_sec > 0
    assert isinstance(cfg.event_stream.queue_size, int)
    assert cfg.web.pi_upload_max_mb > 0
    assert cfg.logging.folder == Path('data/logs')
    assert cfg.logging.level in ('DEBUG', 'INFO', 'WARNING', 'ERROR')
    assert isinstance(cfg.logging.files_kept, int) and cfg.logging.file_size_mb > 0


def test_a_bad_value_in_a_new_section_is_caught_too(project):
    write_local(project, 'pi:\n  detector:\n    format: onnx\n')
    with pytest.raises(ConfigError) as err:
        load_config(root=project, environ={})
    assert str(err.value) == 'config/settings.local.yaml: pi.detector.format must be one of: ncnn, pt (got "onnx")'


def test_a_wait_that_starts_longer_than_its_limit_is_refused(project):
    write_local(project, 'supervisor:\n  restart_start_sec: 120\n')
    with pytest.raises(ConfigError) as err:
        load_config(root=project, environ={})
    assert str(err.value) == ('config/settings.local.yaml: supervisor: restart_start_sec (120) '
                              'must not be more than restart_max_sec (60)')


@pytest.mark.parametrize('local, section, smaller, larger', [
    ('pi:\n  upload_retry_start_sec: 99\n', 'pi', 'upload_retry_start_sec', 'upload_retry_max_sec'),
    ('pi:\n  detector:\n    pause_start_sec: 999\n', 'pi.detector', 'pause_start_sec', 'pause_max_sec'),
    ('llm:\n  retry_start_sec: 999\n', 'llm', 'retry_start_sec', 'retry_max_sec'),
    ('idle_chat:\n  min_sec: 9999\n', 'idle_chat', 'min_sec', 'max_sec'),
    ('safety:\n  edge_proximity:\n    danger_px: 999\n', 'safety.edge_proximity', 'danger_px', 'warn_px'),
    ('web:\n  list_sizes:\n    log_events: {default: 9999}\n', 'web.list_sizes.log_events', 'default', 'max'),
])
def test_every_smaller_larger_pair_is_checked(project, local, section, smaller, larger):
    write_local(project, local)
    with pytest.raises(ConfigError, match=f'^config/settings.local.yaml: {section}: {smaller} .* must not be more '
                                          f'than {larger} '):
        load_config(root=project, environ={})


def test_the_pi_program_still_reads_its_settings(project):
    """raspberry_pi/camera_node.py reads the pi: section; new keys there (like pi.detector) must not upset it."""
    spec = importlib.util.spec_from_file_location('camera_node', ROOT / 'raspberry_pi' / 'camera_node.py')
    node = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(node)
    on_the_pi = node.build_config(node.parse_args(['--offline']))
    pi = load_config(root=project, environ={}).pi
    assert on_the_pi == {key: getattr(pi, key) for key in node.DEFAULTS}


def test_numbers_moved_out_of_the_old_code_have_the_same_values(project):
    """Until Phase 3 switches the app over, the old modules still hold their own copies of these numbers.
    (Only constants the old code never changes at run time are compared here.)"""
    from desktalk.vision import camera_health, relationships
    cfg = load_config(root=project, environ={})
    lens, safety = cfg.lens, cfg.safety
    assert lens.measure_width == camera_health.SAMPLE_WIDTH
    assert lens.edge_touch_px == camera_health.EDGE_TOUCH_PX
    assert lens.fixed_after_sec == camera_health.CLEAR_HOLD_SEC
    assert lens.edge_tip_after_sec == camera_health.EDGE_TIP_AFTER_SEC
    waits = lens.wait_before_saying_sec.model_dump()
    waits.pop('pi')                  # new: the Pi's health, which the old code never checked
    waits.pop('pi_detector')         # new: the Pi's own detector
    assert waits == {k: v[0] for k, v in camera_health.ISSUES.items()}
    assert safety.temporal.history_length == relationships.HISTORY_LENGTH
    assert safety.chain_reaction.path_ratio == relationships.CHAIN_PATH_RATIO
    assert set(safety.chain_reaction.harmless_crowd) == relationships.BENIGN_CROWD_CLASSES
    assert safety.risk_weights.model_dump() == relationships.RISK_WEIGHTS
    assert safety.desk_border_share == relationships.BORDER_FRACTION


# ── parity with today's loader (desktalk/settings.py), until the app switches over ──
def _plain(value):
    """A config value the way the old loader gives it: lists, text, dicts with only the keys the file had."""
    if isinstance(value, BaseModel):
        return {k: _plain(getattr(value, k)) for k in value.model_fields_set}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, SecretStr):
        return value.get_secret_value()
    if isinstance(value, Path):
        return str(value)
    return value


def _leaves(section, path=()):
    for key, value in section.items():
        if isinstance(value, dict) and value:
            yield from _leaves(value, path + (key,))
        else:
            yield path + (key,), value


def _lookup(config, path):
    for part in path:
        config = config[part] if isinstance(config, dict) else getattr(config, part)
    return config


@pytest.mark.parametrize('environ', [
    {},
    {'OLLAMA_MODEL': 'gpt-oss:20b', 'YOLO_MODEL': 'models/yolo26s.pt', 'GEMINI_API_KEY': 'abc',
     'OLLAMA_URL': 'http://localhost:11434/v1', 'CAMERA_SOURCE': 'phone'},
])
def test_every_value_the_old_loader_gives_is_the_same(project, environ):
    old = load_settings(config_dir=project / 'config', environ=dict(environ), dotenv=project / '.env')
    new = load_config(root=project, environ=dict(environ))
    renamed = {'_env_applied': 'env_applied', '_env_moved': 'env_moved'}
    checked = []
    for path, value in _leaves(old):
        if path[0] == '_config_dir':
            continue
        path = (renamed.get(path[0], path[0]),) + path[1:]
        assert _plain(_lookup(new, path)) == value, '.'.join(map(str, path))
        checked.append(path)
    assert ('protocols', 'hydration', 'daily_goal_ml') in checked
    assert ('agents_file', 'agents', 'cup', 'labels') in checked
