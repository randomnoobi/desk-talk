"""server.py's start-up lines (the settings, the Pi command, the phone link): through logging, so they reach
the log file too, and shown in the terminal as they are, whatever the log level."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

import pytest

import server
from desktalk.config import ConfigError
from tests.integration.app_kit import changed, desk_config


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
