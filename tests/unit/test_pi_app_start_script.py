"""raspberry_pi/app/start.sh, run with stand-ins for what needs root, and for `cp` where it should fail."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

START = Path(__file__).resolve().parents[2] / 'raspberry_pi' / 'app' / 'start.sh'
RULES = '{"zones": {}}\n'


def stand_in(folder: Path, name: str, body: str) -> None:
    path = folder / name
    path.write_text(f'#!/bin/sh\n{body}\n')
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


@pytest.fixture
def desk(tmp_path) -> Path:
    (tmp_path / 'data').mkdir()
    (tmp_path / 'config').mkdir()
    (tmp_path / 'config' / 'safety_rules.json').write_text(RULES)
    tools = tmp_path / 'tools'
    tools.mkdir()
    stand_in(tools, 'chown', 'exit 0')
    # setpriv --reuid=.. --regid=.. --groups=.. <the program>: runs the program
    stand_in(tools, 'setpriv', 'shift 3; exec "$@"')
    return tmp_path


def start(desk: Path) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, 'PATH': f'{desk / "tools"}:{os.environ["PATH"]}', 'PI_USER_ID': '1000', 'PI_GROUP_ID': '1000',
           'PI_AUDIO_GROUP': '29'}
    return subprocess.run(['sh', str(START), 'sh', '-c', 'echo the app started'], cwd=desk, env=env,
                          capture_output=True, text=True, check=False)


def only_rules_in(desk: Path) -> list[str]:
    return sorted(p.name for p in (desk / 'data').iterdir())


def test_a_new_pi_starts_from_the_danger_zones_in_git(desk):
    done = start(desk)
    assert done.returncode == 0 and 'the app started' in done.stdout
    assert (desk / 'data' / 'safety_rules.json').read_text() == RULES
    assert only_rules_in(desk) == ['safety_rules.json']


def test_saved_danger_zones_are_kept(desk):
    (desk / 'data' / 'safety_rules.json').write_text('{"mine": 1}')
    start(desk)
    assert (desk / 'data' / 'safety_rules.json').read_text() == '{"mine": 1}'


def test_a_full_disk_still_starts_the_app_and_leaves_nothing_half_done(desk):
    stand_in(desk / 'tools', 'cp', 'printf "{" > "$2"; exit 1')       # as cp does when the disk fills up
    done = start(desk)
    assert done.returncode == 0 and 'the app started' in done.stdout
    assert only_rules_in(desk) == []


def test_an_empty_file_left_by_an_older_start_is_replaced(desk):
    (desk / 'data' / 'safety_rules.json').write_text('')
    start(desk)
    assert (desk / 'data' / 'safety_rules.json').read_text() == RULES


def test_when_chown_fails_the_app_still_starts(desk):
    stand_in(desk / 'tools', 'chown', 'exit 1')
    done = start(desk)
    assert done.returncode == 0 and 'the app started' in done.stdout


def test_the_app_keeps_the_audio_group_when_it_gives_up_root(desk):
    tools = desk / 'tools'
    stand_in(tools, 'setpriv', 'echo "$@"')
    assert '--groups=29 ' in start(desk).stdout
