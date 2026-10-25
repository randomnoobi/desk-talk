"""setup_logging: the terminal plus a rotating file, one plain line per message."""

from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler

import pytest

from desktalk.runtime.log_setup import setup_logging

# time, level, part name, message
FILE = 'desktalk.log'          # logging.file_name in config/settings.yaml
LINE = re.compile(r'^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d (?P<level>[A-Z]+) (?P<part>[\w.]+): (?P<message>.*)$')


@pytest.fixture(autouse=True)
def put_the_root_logger_back():
    """setup_logging changes the root logger for the whole process; undo that after each test."""
    root = logging.getLogger()
    before, level = list(root.handlers), root.level
    yield
    for handler in list(root.handlers):
        # Only plain terminal/file handlers; pytest's own capture handlers are subclasses and stay.
        if handler not in before and type(handler) in (logging.StreamHandler, RotatingFileHandler):
            root.removeHandler(handler)
            handler.close()
    root.setLevel(level)


def test_each_message_is_one_plain_line_in_a_file_in_the_folder(tmp_path):
    path = setup_logging(tmp_path / 'logs', 'INFO', max_mb=1, keep_files=3, file_name=FILE)
    logging.getLogger('desktalk.chat.lens').warning('the picture is %s', 'blurry')
    assert path.parent == tmp_path / 'logs'
    [line] = path.read_text().splitlines()
    match = LINE.match(line)
    assert match, line
    assert match.group('level', 'part', 'message') == ('WARNING', 'desktalk.chat.lens', 'the picture is blurry')


def test_messages_also_go_to_the_terminal_as_the_same_plain_line(tmp_path, capsys):
    setup_logging(tmp_path / 'logs', 'INFO', max_mb=1, keep_files=3, file_name=FILE)
    logging.getLogger('desktalk.habits.reminders').info('time to drink some water')
    [line] = capsys.readouterr().err.splitlines()
    match = LINE.match(line)
    assert match, line
    assert match.group('level', 'part', 'message') == ('INFO', 'desktalk.habits.reminders', 'time to drink some water')


def test_calling_it_twice_does_not_write_each_message_twice(tmp_path, capsys):
    setup_logging(tmp_path / 'logs', 'INFO', max_mb=1, keep_files=3, file_name=FILE)
    path = setup_logging(tmp_path / 'logs', 'INFO', max_mb=1, keep_files=3, file_name=FILE)
    logging.getLogger('desktalk.chat.lens').warning('the picture is blurry')
    assert len(path.read_text().splitlines()) == 1
    assert len(capsys.readouterr().err.splitlines()) == 1


def test_messages_below_the_level_are_left_out_and_the_level_can_be_lower_case(tmp_path):
    path = setup_logging(tmp_path / 'logs', 'warning', max_mb=1, keep_files=3, file_name=FILE)
    part = logging.getLogger('desktalk.chat.lens')
    part.info('looking at the desk')
    part.warning('the picture is blurry')
    lines = [LINE.match(line) for line in path.read_text().splitlines()]
    assert [m.group('message') if m else None for m in lines] == ['the picture is blurry']


def test_the_file_rolls_over_at_max_mb_and_keeps_only_keep_files_old_ones(tmp_path):
    path = setup_logging(tmp_path / 'logs', 'INFO', max_mb=0.001, keep_files=2,
                         file_name=FILE)                                         # about 1 KB per file
    part = logging.getLogger('desktalk.chat.lens')
    for n in range(200):                                                            # about 14 KB in all
        part.info('picture %03d looks fine', n)
    assert sorted(p.name for p in path.parent.iterdir()) == [FILE, f'{FILE}.1', f'{FILE}.2']
    assert all(p.stat().st_size <= 1100 for p in path.parent.iterdir())
    assert 'picture 199 looks fine' in path.read_text()


def test_the_file_gets_the_name_from_the_settings(tmp_path):
    path = setup_logging(tmp_path / 'logs', 'INFO', max_mb=1, keep_files=3, file_name='my-desk.log')
    logging.getLogger('desktalk.chat.lens').warning('the picture is blurry')
    assert path == tmp_path / 'logs' / 'my-desk.log' and 'blurry' in path.read_text()
