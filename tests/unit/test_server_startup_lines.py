"""server.py's start-up lines (the settings, the address, the camera): through logging, so they reach the log
file too, and shown in the terminal as they are, whatever the log level. The pairing key is never in them."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from types import SimpleNamespace

import pytest

import server
from desktalk.config import ConfigError
from desktalk.domain import Saving
from desktalk.web.pairing import Links
from tests.integration.app_kit import changed, desk_config

KEY = 'Zq7Wk3mRtx'                    # a pairing key no other line would contain by chance
PUBLIC = 'desktalk.local:5000'
DOCKER_ADDRESS = '172.18.0.3'          # what lan_ip() finds inside a container


@pytest.fixture(autouse=True)
def put_the_root_logger_back():
    """Setting up logging changes the root logger for the whole process; undo that after each test."""
    root = logging.getLogger()
    before, level = list(root.handlers), root.level
    yield
    for handler in list(root.handlers):
        # Only plain terminal/file handlers; pytest's own capture handlers are subclasses and stay.
        if handler not in before and type(handler) in (logging.StreamHandler, RotatingFileHandler):
            root.removeHandler(handler)
            handler.close()
    root.setLevel(level)


def test_the_start_up_lines_reach_the_log_file_and_show_plainly_in_the_terminal(tmp_path, capsys):
    config = desk_config(tmp_path)
    log_file = server.start_logging(config)
    server.settings_lines(config)
    assert log_file == config.resolve(config.logging.folder) / config.logging.file_name
    assert '  Desk Talk' in log_file.read_text()
    shown = capsys.readouterr().err.splitlines()
    assert '  Desk Talk' in shown                       # the line as it is, no time or level in front
    assert f'  Camera       : {config.camera.source}  ({config.camera.fps:g} pictures a second)' in shown


def test_the_start_up_lines_show_even_when_the_log_level_is_warning(tmp_path, capsys):
    config = changed(desk_config(tmp_path), 'logging', level='WARNING')
    server.start_logging(config)
    server.say('  -> Open http://localhost:5000 on this computer')
    logging.getLogger('desktalk.chat.lens').info('looking at the desk')      # other INFO lines are still left out
    assert capsys.readouterr().err.splitlines() == ['  -> Open http://localhost:5000 on this computer']


def test_a_setting_that_needs_fixing_is_said_as_an_error(monkeypatch, caplog):
    def broken():
        raise ConfigError('web.port: must be a number')
    monkeypatch.setattr(server, 'load_config', broken)
    with caplog.at_level(logging.INFO):
        assert server.main() == 2
    [record] = caplog.records
    assert record.levelno == logging.ERROR and 'web.port: must be a number' in record.getMessage()


def test_the_log_file_is_named_by_the_settings(tmp_path):
    config = changed(desk_config(tmp_path), 'logging', file_name='my-desk.log')
    log_file = server.start_logging(config)
    assert log_file == config.resolve(config.logging.folder) / 'my-desk.log'


def shown_lines(config, source_kind, capsys):
    """What `address_lines` says for a camera source: the terminal's lines, as they are."""
    server.start_logging(config)
    links = Links(DOCKER_ADDRESS, config.web.port, KEY, public_address=config.web.public_address)
    server.address_lines(config, links, source_kind)
    return capsys.readouterr().err.splitlines()


def test_startup_shows_the_public_address(tmp_path, capsys):
    config = changed(desk_config(tmp_path), 'web', public_address=PUBLIC)
    shown = shown_lines(config, 'pi', capsys)
    assert any(PUBLIC in line and 'Open' in line for line in shown)
    assert not any('localhost' in line or DOCKER_ADDRESS in line for line in shown)


def test_startup_in_docker_skips_the_pi_command(tmp_path, capsys):
    config = changed(desk_config(tmp_path), 'web', public_address=PUBLIC)
    shown = shown_lines(config, 'pi', capsys)
    assert not any('raspberry_pi.camera' in line for line in shown)


def test_startup_on_a_computer_points_to_the_page_and_the_key_file_for_the_pi(tmp_path, capsys):
    config = desk_config(tmp_path)
    shown = shown_lines(config, 'pi', capsys)
    assert any('http://localhost:5000' in line for line in shown)
    [by_hand] = [line for line in shown if 'raspberry_pi.camera' in line]
    assert f'--server http://{DOCKER_ADDRESS}:5000' in by_hand and '--key-file' in by_hand and '--key ' not in by_hand
    assert any(str(config.resolve(config.camera.pair_key_file)) in line for line in shown)


@pytest.mark.parametrize('public', ['', PUBLIC])
@pytest.mark.parametrize('source_kind', ['pi', 'phone', 'webcam'])
def test_the_pairing_key_is_not_in_the_start_up_lines(tmp_path, capsys, source_kind, public):
    config = changed(desk_config(tmp_path), 'web', public_address=public)
    shown = shown_lines(config, source_kind, capsys)
    assert shown and not any(KEY in line for line in shown)
    log_file = config.resolve(config.logging.folder) / config.logging.file_name
    assert KEY not in log_file.read_text()


@pytest.mark.parametrize('source_kind', ['pi', 'phone'])
def test_a_whole_start_never_logs_the_pairing_key(tmp_path, monkeypatch, capsys, source_kind):
    config = changed(desk_config(tmp_path), 'camera', source=source_kind)
    desktalk = SimpleNamespace(detector=SimpleNamespace(label='fake'), model=SimpleNamespace(label='fake'),
                               safety_rules=SimpleNamespace(loaded=False), source_kind=source_kind, pair_key=KEY)
    monkeypatch.setattr(server, 'load_config', lambda: config)
    monkeypatch.setattr(server, 'build', lambda cfg, **kw: desktalk)
    monkeypatch.setattr(server, 'lan_ip', lambda: DOCKER_ADDRESS)
    monkeypatch.setattr(server, 'ensure_cert', lambda *a, **k: None)
    monkeypatch.setattr(server, 'run', lambda *a: None)
    assert server.main() == 0
    log_file = config.resolve(config.logging.folder) / config.logging.file_name
    assert KEY not in log_file.read_text() and KEY not in capsys.readouterr().err


def test_off_mode_says_nothing_is_saved_at_start(tmp_path, monkeypatch, capsys):
    """start.sh found Desk Talk's own space missing: the log file and the app both use the in-memory folder it
    gives, and the start-up lines say nothing is being saved."""
    config = desk_config(tmp_path)
    memory = tmp_path / 'memory'
    built: dict = {}
    rules_file = memory / 'config' / 'safety_rules.json'
    desktalk = SimpleNamespace(detector=SimpleNamespace(label='fake'), model=SimpleNamespace(label='fake'),
                               safety_rules=SimpleNamespace(loaded=True), rules_file=rules_file,
                               source_kind='webcam', pair_key=KEY)

    def fake_build(cfg, **kw):
        built.update(kw)
        return desktalk
    monkeypatch.setenv('DESKTALK_SAVING', 'off')
    monkeypatch.setenv('DESKTALK_DATA_FOLDER', str(memory))
    monkeypatch.setattr(server, 'load_config', lambda: config)
    monkeypatch.setattr(server, 'build', fake_build)
    monkeypatch.setattr(server, 'lan_ip', lambda: DOCKER_ADDRESS)
    monkeypatch.setattr(server, 'run', lambda *a: None)
    assert server.main() == 0
    assert built == {'saving': Saving.OFF, 'data_folder': memory}
    shown = capsys.readouterr().err.splitlines()
    assert any('nothing is being saved' in line.lower() for line in shown)
    assert any(line.startswith('  Event log    : ') and str(memory) in line for line in shown)
    assert f'  Danger zones : {rules_file}' in shown
    assert (memory / config.logging.folder / config.logging.file_name).is_file()
    assert not (config.root / 'data').exists()


def test_off_mode_with_no_folder_given_makes_one_in_the_memory_fallback_folder(tmp_path, monkeypatch, capsys):
    shm = tmp_path / 'shm'
    shm.mkdir()
    config = changed(desk_config(tmp_path), 'storage', memory_fallback_folder=shm)
    built: dict = {}
    desktalk = SimpleNamespace(detector=SimpleNamespace(label='fake'), model=SimpleNamespace(label='fake'),
                               safety_rules=SimpleNamespace(loaded=False), source_kind='webcam', pair_key=KEY)

    def fake_build(cfg, **kw):
        built.update(kw)
        return desktalk
    monkeypatch.setenv('DESKTALK_SAVING', 'off')
    monkeypatch.delenv('DESKTALK_DATA_FOLDER', raising=False)
    monkeypatch.setattr(server, 'load_config', lambda: config)
    monkeypatch.setattr(server, 'build', fake_build)
    monkeypatch.setattr(server, 'lan_ip', lambda: DOCKER_ADDRESS)
    monkeypatch.setattr(server, 'run', lambda *a: None)
    assert server.main() == 0
    folder = built['data_folder']
    assert folder.parent == shm and folder.is_dir()
    shown = capsys.readouterr().err.splitlines()
    assert any(line.startswith('  Saving       : ') and str(folder) in line for line in shown)
    assert not (config.root / 'data').exists()


def test_plain_mode_says_nothing_about_saving(tmp_path, monkeypatch, capsys):
    config = desk_config(tmp_path)
    desktalk = SimpleNamespace(detector=SimpleNamespace(label='fake'), model=SimpleNamespace(label='fake'),
                               safety_rules=SimpleNamespace(loaded=False), source_kind='webcam', pair_key=KEY)
    monkeypatch.delenv('DESKTALK_SAVING', raising=False)
    monkeypatch.setattr(server, 'load_config', lambda: config)
    monkeypatch.setattr(server, 'build', lambda cfg, **kw: desktalk)
    monkeypatch.setattr(server, 'lan_ip', lambda: DOCKER_ADDRESS)
    monkeypatch.setattr(server, 'run', lambda *a: None)
    assert server.main() == 0
    assert not any('saved' in line.lower() for line in capsys.readouterr().err.splitlines())
