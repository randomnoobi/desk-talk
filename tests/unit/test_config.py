"""Typed settings (desktalk/config/): loading config/*.yaml and .env, and clear messages for bad values.

Every test works on copies of the real config files in a temp folder and passes environ={},
so your own .env and config/settings.local.yaml never change the result.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

import pytest
import yaml
from pydantic import BaseModel, SecretStr, ValidationError

from desktalk.config import ConfigError, load_config
from desktalk.config.loader import RETIRED_SETTINGS

ROOT = Path(__file__).resolve().parents[2]
CONFIG_FILES = ('settings.yaml', 'agents.yaml', 'protocols.yaml', 'prompts.yaml')


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
    assert cfg.event_log.compact_every_min == 10
    assert cfg.protocols.light.morning_window_min == 60
    assert cfg.detector.model == 'models/yolo26m-objv1-150.pt'
    assert cfg.lost_items.items['airpods'].labels == ('earphone', 'earbuds')
    assert cfg.agents_file.agents['cup'].labels == ('cup', 'bottle', 'wine glass', 'mug', 'water bottle',
                                                    'measuring cup', 'kettle')


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
        cast(Any, cfg.camera.webcam_indices).append(1)          # lists come back as tuples


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
    ('safety:\n  danger_zones:\n    laptop: {base_share: 1.5}\n',
     'safety.danger_zones.laptop.base_share must be a number above 0 and at most 1 (got 1.5)'),
])
def test_each_kind_of_bad_value_is_described_in_plain_words(project, local, problem):
    write_local(project, local)
    with pytest.raises(ConfigError) as err:
        load_config(root=project, environ={})
    assert str(err.value) == f'config/settings.local.yaml: {problem}'


def test_a_danger_zone_written_before_base_share_existed_still_loads(project):
    write_local(project, 'safety:\n  danger_zones:\n    mouse: {expand: 20, label: Mouse zone}\n')
    zone = load_config(root=project, environ={}).safety.danger_zones['mouse']
    assert (zone.expand, zone.label, zone.base_share) == (20, 'Mouse zone', 1.0)


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
    ('web:\n  settings_sliders:\n    alert: {min: 999}\n', 'web.settings_sliders.alert', 'min', 'max'),
])
def test_every_smaller_larger_pair_is_checked(project, local, section, smaller, larger):
    write_local(project, local)
    with pytest.raises(ConfigError, match=f'^config/settings.local.yaml: {section}: {smaller} .* must not be more '
                                          f'than {larger} '):
        load_config(root=project, environ={})


def test_the_pi_section_has_the_values_the_old_camera_program_used(project):
    pi = load_config(root=project, environ={}).pi
    assert (pi.fps, pi.width, pi.height, pi.jpeg_quality, pi.photo_dir) == (3, 1280, 720, 80, '~/desk-talk-photos')
    assert (pi.delete_every_min, pi.keep_last_min, pi.min_free_mb, pi.upload_timeout_sec) == (10, 0, 300, 5)


def test_numbers_moved_out_of_the_old_code_keep_their_values(project):
    """Lens's checks and the desk rules kept these numbers in the code before they moved to settings.yaml."""
    cfg = load_config(root=project, environ={})
    lens, safety = cfg.lens, cfg.safety
    assert (lens.measure_width, lens.edge_touch_px, lens.fixed_after_sec, lens.edge_tip_after_sec) == (240, 3, 5, 120)
    assert lens.wait_before_saying_sec.model_dump() == {
        'lost': 15, 'covered': 4, 'dark': 8, 'glare': 8, 'blurry': 12, 'bumped': 5, 'empty': 180, 'cutoff': 45,
        'slow': 30, 'pi': 10, 'pi_detector': 60}
    assert cfg.camera.moved_within_sec == 1.5
    assert safety.temporal.history_length == 30
    assert safety.chain_reaction.path_ratio == 0.30
    assert set(safety.chain_reaction.harmless_crowd) == {'book', 'laptop', 'keyboard', 'mouse', 'cell phone',
                                                         'remote', 'tv'}
    assert safety.risk_weights.model_dump() == {'edge_danger': 2, 'edge_warning': 1, 'zone_intrusion': 2,
                                                'chain_reaction': 3, 'crowd': 1, 'approaching': 2,
                                                'repeated_intrusion': 2}
    assert safety.desk_border_share == 0.03


# ── every line of the config files reaches the config unchanged ──
def _plain(value):
    """A config value as plain YAML data: lists, text, dicts with only the keys the file had."""
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


def test_every_value_in_the_config_files_reaches_the_config_unchanged(project):
    cfg = load_config(root=project, environ={})
    checked = []
    for name, section in [('settings.yaml', cfg), ('protocols.yaml', cfg.protocols),
                          ('agents.yaml', cfg.agents_file)]:
        for path, value in _leaves(yaml.safe_load((project / 'config' / name).read_text())):
            if path == ('rules',):      # written as 30m and "is", kept as seconds and is_: see test_config_rules.py
                continue
            assert _plain(_lookup(section, path)) == value, f'{name}: {".".join(map(str, path))}'
            checked.append((name, path))
    assert ('protocols.yaml', ('hydration', 'daily_goal_ml')) in checked
    assert ('agents.yaml', ('agents', 'cup', 'labels')) in checked
    assert len(checked) > 500


def test_environment_variables_win_and_moved_ones_are_listed(project):
    environ = {'OLLAMA_MODEL': 'gpt-oss:20b', 'YOLO_MODEL': 'models/yolo26s.pt', 'GEMINI_API_KEY': 'abc',
               'OLLAMA_URL': 'http://localhost:11434/v1', 'CAMERA_SOURCE': 'phone'}
    cfg = load_config(root=project, environ=environ)
    assert cfg.llm.ollama_model == 'gpt-oss:20b'
    assert cfg.detector.model == 'models/yolo26s.pt'
    assert cfg.secrets.gemini_api_key.get_secret_value() == 'abc'
    assert cfg.secrets.ollama_url == 'http://localhost:11434/v1'
    assert cfg.camera.source == 'pi'
    assert cfg.env_applied == {'OLLAMA_MODEL': 'llm.ollama_model', 'YOLO_MODEL': 'detector.model'}
    assert cfg.env_moved == ('CAMERA_SOURCE',)


# ── settings added by the 2026 cleanup (docs/plans/2026-10-23-codebase-cleanup-plan.md) ──
BEFORE_CLEANUP = ROOT / 'tests' / 'fixtures' / 'settings_before_cleanup.yaml'   # config/settings.yaml before it
NEW_SETTINGS = {
    'settings.yaml': [
        ('camera', 'phone_cert_file'), ('camera', 'phone_key_file'), ('camera', 'phone_max_side'),
        ('camera', 'phone_jpeg_quality'), ('camera', 'phone_tilt_max'),
        ('drawing', 'colors', 'risk'), ('drawing', 'line_px'), ('drawing', 'font_scale'),
        ('drawing', 'fill_alpha', 'zone'), ('drawing', 'trail', 'start_after'), ('drawing', 'trail', 'keep'),
        ('drawing', 'dash'), ('drawing', 'arrow_tip'), ('drawing', 'radius'),
        ('web', 'refresh_ms', 'live'), ('web', 'refresh_ms', 'log'), ('web', 'refresh_ms', 'habits'),
        ('web', 'request_timeout_ms'), ('web', 'activity_ping_ms'), ('web', 'toast_ms'),
        ('web', 'list_sizes', 'log'), ('web', 'list_sizes', 'photos'),
        ('web', 'list_sizes', 'chat_kept'), ('web', 'list_sizes', 'chat_saved'), ('web', 'left_chat_shown_min'),
        ('camera', 'phone_capture_width'), ('camera', 'phone_capture_height'),
        ('logging', 'file_name'), ('logging', 'preview_chars'), ('llm', 'error_text_chars'),
        ('pi', 'camera'), ('pi', 'error_text_chars'), ('llm', 'error_log_chars'), ('safety', 'min_desk_corners'),
        ('web', 'settings_sliders', 'zone'), ('web', 'settings_sliders', 'warn'), ('web', 'settings_sliders', 'alert'),
        ('web', 'new_zone_px'), ('lens', 'trusted_gravity'),
        ('lens', 'covered_detail_below'), ('lens', 'covered_by_box_share'),
        # the developer page (docs/plans/2026-10-24-developer-ui-plan.md)
        ('object_states', 'trace_pictures'), ('dev', 'enabled'), ('dev', 'ui_folder'), ('dev', 'vite_port'),
        ('dev', 'poll_sec'), ('dev', 'checks'), ('dev', 'test_command'), ('dev', 'collect_markers'),
        ('dev', 'run_timeout_min'), ('dev', 'output_keep_kb'), ('dev', 'sql_max_rows'), ('dev', 'sql_timeout_sec'),
        ('dev', 'file_preview_kb'), ('dev', 'health', 'pi_cpu_warn_c'), ('dev', 'health', 'pi_cpu_bad_c'),
        ('dev', 'health', 'pi_disk_warn_mb'), ('dev', 'health', 'detect_warn_ms'),
        ('dev', 'flicker_flips_per_min'), ('dev', 'sql_check_every_steps'), ('dev', 'collect_timeout_sec'),
        ('dev', 'flips_window_min'), ('dev', 'table_counts_every_sec'),
    ],
    'protocols.yaml': [('food', 'healthy')],
}


def _key_lines(text: str) -> dict[tuple, int]:
    """Every key in a YAML file → the line it's on (0 = the first line)."""
    out: dict[tuple, int] = {}

    def walk(node, path):
        if isinstance(node, yaml.MappingNode):
            for key, value in node.value:
                out[path + (key.value,)] = key.start_mark.line
                walk(value, path + (key.value,))

    walk(yaml.compose(text), ())
    return out


def test_local_settings_with_every_old_key_still_loads(project):
    """Your own settings.local.yaml may set any setting settings.yaml had before the cleanup, in the same place."""
    shutil.copy(BEFORE_CLEANUP, project / 'config' / 'settings.local.yaml')
    cfg = load_config(root=project, environ={})
    checked = 0
    for path, value in _leaves(yaml.safe_load(BEFORE_CLEANUP.read_text())):
        if any(path[:len(gone)] == gone for gone in RETIRED_SETTINGS):     # left out, see the test below
            continue
        assert _plain(_lookup(cfg, path)) == value, '.'.join(map(str, path))
        checked += 1
    assert checked > 300


@pytest.mark.parametrize('name', NEW_SETTINGS)
def test_every_new_setting_has_a_comment(name):
    """Each setting added since the cleanup started says what it does, on its line or the line above."""
    text = (ROOT / 'config' / name).read_text()
    lines = text.splitlines()
    keys = _key_lines(text)
    old = set(_key_lines(BEFORE_CLEANUP.read_text())) if name == 'settings.yaml' else set()
    new = {path: line for path, line in keys.items() if path not in old} if old else \
        {path: line for path, line in keys.items() if path[:2] in NEW_SETTINGS[name]}
    for path in NEW_SETTINGS[name]:
        assert path in keys, f'{name}: {".".join(path)} is missing'
    no_comment = ['.'.join(map(str, path)) for path, line in new.items()
                  if '#' not in lines[line] and not lines[line - 1].lstrip().startswith('#')]
    assert no_comment == []


def test_new_settings_keep_the_values_the_code_has_today(project):
    cfg = load_config(root=project, environ={})
    camera, drawing, web = cfg.camera, cfg.drawing, cfg.web
    assert (camera.phone_cert_file, camera.phone_key_file) == ('desk-talk.crt', 'desk-talk.key')    # web/pairing.py
    assert (camera.phone_max_side, camera.phone_jpeg_quality, camera.phone_tilt_max) == (960, 70, 50)   # camera.js
    assert drawing.colors.box_on == (60, 200, 90) and drawing.colors.danger == (0, 0, 255)          # drawing.py
    assert drawing.colors.risk == ((0, 200, 0), (0, 220, 220), (0, 140, 255), (0, 0, 255), (0, 0, 160))
    assert (drawing.line_px, drawing.arrow_tip, drawing.trail.start_after, drawing.trail.keep) == (2, 0.15, 3, 6)
    assert (drawing.fill_alpha.zone, drawing.fill_alpha.crowd) == (0.30, 0.10)
    assert web.refresh_ms.model_dump() == {'live': 1500, 'habits': 15000, 'photos': 10000, 'log': 20000}  # app.js
    assert (web.request_timeout_ms, web.activity_ping_ms, web.toast_ms) == (8000, 3000, 4000)
    assert (web.list_sizes.log, web.list_sizes.photos) == (150, 6)
    assert (web.list_sizes.chat_kept, web.list_sizes.chat_saved, web.left_chat_shown_min) == (200, 60, 60)  # app.js
    assert (camera.phone_capture_width, camera.phone_capture_height) == (1280, 720)                  # camera.js
    assert web.settings_sliders.model_dump() == {'zone': {'min': 10, 'max': 200}, 'warn': {'min': 20, 'max': 200},
                                                 'alert': {'min': 10, 'max': 120}}                    # app.js
    assert web.new_zone_px == 60                                                                     # app.js
    assert cfg.lens.trusted_gravity == 1.0                                                           # camera_measure.py
    assert (cfg.logging.file_name, cfg.llm.error_text_chars, cfg.llm.error_log_chars) == ('desktalk.log', 160, 200)
    assert 'banana' in cfg.protocols.food.healthy and len(cfg.protocols.food.healthy) == 20          # tracker.py


def test_every_agent_color_is_in_the_page_palette(project):
    cfg = load_config(root=project, environ={})
    colors = {a.color for a in cfg.agents_file.agents.values() if a.color}
    assert colors <= set(cfg.web.palette)


def test_the_longest_chat_reply_follows_how_the_server_tries_the_ai(project):
    """Wait its turn, keep trying until the time limit (the last try may start just before it), and on Ollama
    ask that last try up to max_retries times for a reply that isn't empty (adapters/llm/)."""
    llm = load_config(root=project, environ={}).llm
    assert (llm.min_interval_sec, llm.ollama_timeout_sec, llm.max_retries, llm.ollama_retry_sec) == (6, 90, 3, 2)
    assert llm.longest_reply_sec() == 6 + 90 + 3 * 90 + 2 * 2


def test_the_page_no_longer_has_a_first_check_wait():
    """The page stopped using web.first_check_timeout_ms, so the setting is gone (it only lived on the cleanup
    branch, so no settings.local.yaml has it)."""
    assert 'first_check_timeout_ms' not in type(load_config(environ={}, dotenv=Path('/nonexistent')).web).model_fields


# ── the desk fixes (docs/superpowers/specs/2026-10-25-desk-fixes-design.md) ──
def test_sensitivity_steps_load(project):
    agents = load_config(root=project, environ={}).agents
    assert [s.name for s in agents.sensitivity_steps] == ['Picky', 'Careful', 'Normal', 'Easy', 'Eager']
    assert [(s.min_confidence, s.join_after_sec) for s in agents.sensitivity_steps] == [
        (0.65, 10), (0.55, 5), (0.45, 3), (0.40, 2.5), (0.35, 2)]
    assert agents.sensitivity_default == 2
    assert agents.default_step().name == 'Normal'
    assert agents.can_join == ('computer', 'cup', 'phone', 'snack', 'paper')
    assert (agents.on_desk_after_sec, agents.on_desk_min_confidence) == (3, 0.45)


def test_same_thing_overlap_loads(project):
    assert load_config(root=project, environ={}).agents.same_thing_overlap == 0.7


@pytest.mark.parametrize('value', [0, -0.1, 1.5])
def test_same_thing_overlap_must_be_more_than_0_and_at_most_1(project, value):
    # 0 would make boxes that don't even touch one thing
    write_local(project, f'agents:\n  same_thing_overlap: {value}\n')
    with pytest.raises(ConfigError, match='same_thing_overlap'):
        load_config(root=project, environ={})


def test_the_default_sensitivity_must_be_one_of_the_steps(project):
    write_local(project, 'agents:\n  sensitivity_default: 5\n')
    with pytest.raises(ConfigError, match='sensitivity_default'):
        load_config(root=project, environ={})


def test_notifications_section_loads(project):
    n = load_config(root=project, environ={}).notifications
    assert (n.enabled, n.warnings, n.reminders, n.sips) == (True, True, True, True)
    assert (n.time_limit_sec, n.command, n.queue_size) == (5, 'osascript', 20)


def test_hydration_sip_shares_load(project):
    hydration = load_config(root=project, environ={}).protocols.hydration
    assert (hydration.sip_lift_share, hydration.sip_back_share, hydration.sip_cover_share) == (0.08, 0.15, 0.6)
    assert hydration.sip_cover_pictures == 2


def test_hydration_moved_setting_loads_and_the_hand_rule_is_gone(project):
    hydration = load_config(root=project, environ={}).protocols.hydration
    assert hydration.sip_moved_share == 0.007
    assert hydration.sip_needs_person is True
    assert not hasattr(hydration, 'sip_hand_labels') and not hasattr(hydration, 'sip_hand_share')
    assert (hydration.sip_gone_min_sec, hydration.sip_gone_max_sec) == (2, 120)


def test_the_other_new_settings_load(project):
    cfg = load_config(root=project, environ={})
    tasks, dev, web = cfg.protocols.tasks, cfg.dev, cfg.web
    assert (tasks.remind_after_min, tasks.due_soon_days, tasks.late_repeat_hours) == (120, 1, 24)
    assert (dev.trace_photos_kept, dev.trace_photo_every_sec, dev.trace_events_kept) == (6, 120, 50)
    assert 'trace_photo_quality' not in type(dev).model_fields        # the photos are the camera's JPEG as it is
    assert dev.trace_photos_folder == Path('data/trace_photos')
    assert (web.camera_note_after_sec, web.saved_note_sec, web.retry_start_sec) == (5, 4, 3)
    sizes = web.list_sizes
    assert (sizes.on_desk_shown, sizes.camera_issues_shown, sizes.risk_lines_shown, sizes.side_photos_shown) == (
        8, 2, 6, 6)
    assert (sizes.quote_chars, sizes.toast_chars, sizes.mention_max_chars, sizes.counter_label_chars) == (
        90, 140, 12, 60)
    assert cfg.habit_tracker.todos_key == 'todos'


def test_the_posture_checks_and_the_old_join_settings_are_gone(project):
    cfg = load_config(root=project, environ={})
    assert 'vision_habits' not in type(cfg).model_fields
    assert not {'join_after_sec', 'min_confidence'} & set(type(cfg.agents).model_fields)


def test_settings_that_are_gone_are_dropped_from_your_own_file(project, caplog):
    """An old settings.local.yaml may still set them; they're left out (with a note in the log), not an error."""
    write_local(project, 'agents:\n  join_after_sec: 5\n  min_confidence: 0.5\n  max_agents: 7\n'
                         'vision_habits:\n  every_sec: 30\n')
    with caplog.at_level('WARNING'):
        cfg = load_config(root=project, environ={})
    assert cfg.agents.max_agents == 7
    assert 'agents.join_after_sec' in caplog.text and 'vision_habits' in caplog.text


def test_the_event_log_lists_and_the_photo_quality_that_went_are_dropped_from_your_own_file(project, caplog):
    write_local(project, 'web:\n  list_sizes:\n    habit_log: 40\n    log_detections: 30\n    toast_chars: 99\n'
                         'dev:\n  trace_photo_quality: 70\n')
    with caplog.at_level('WARNING'):
        cfg = load_config(root=project, environ={})
    assert cfg.web.list_sizes.toast_chars == 99
    assert all(word in caplog.text for word in ('habit_log', 'log_detections', 'trace_photo_quality'))


def test_the_blocked_camera_settings_load(project):
    lens = load_config(root=project, environ={}).lens
    assert (lens.covered_detail_below, lens.covered_by_box_share) == (6, 0.7)


@pytest.mark.parametrize('text', ['covered_detail_below: -1', 'covered_by_box_share: 0',
                                  'covered_by_box_share: 1.5'])
def test_the_blocked_camera_settings_are_checked(project, text):
    # a share of 0 would call every picture with anything in it blocked
    write_local(project, f'lens:\n  {text}\n')
    with pytest.raises(ConfigError, match=text.split(':')[0]):
        load_config(root=project, environ={})
