"""scripts/backup_pi.sh and scripts/install_weekly_backup.sh, run for real against a made-up Pi.

A stand-in `ssh` runs the command in a folder that plays the Pi's home, and a stand-in `docker` runs
`compose exec ... app python -` with this computer's Python in the made-up repo there, so the database copy is made
by the script's own Python, through SQLite's backup. A stand-in `launchctl` only writes down how it was called.
Nothing here reaches a real Pi, Docker or launchd."""

from __future__ import annotations

import os
import plistlib
import shutil
import sqlite3
import stat
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from desktalk.adapters.storage import SqliteStateStore
from tests.contracts.state_store_contract import a_record
from tests.unit.test_config import CONFIG_FILES

ROOT = Path(__file__).resolve().parents[2]
BACKUP = ROOT / 'scripts' / 'backup_pi.sh'
INSTALL = ROOT / 'scripts' / 'install_weekly_backup.sh'
STAMP = '2026-11-01-100000'                  # how a backup folder is named: the day and time it was made

SSH = r'''#!/bin/bash
printf 'ssh %s\n' "$*" >> "$FAKE/calls"
while [ $# -gt 0 ]; do case "$1" in -o) shift 2 ;; -*) shift ;; *) break ;; esac; done
host=$1; shift
[ "$host" = "$FAKE_HOST" ] || { echo "ssh: Could not resolve hostname $host" >&2; exit 255; }
cd "$FAKE_PI_HOME" && PATH="$FAKE/pi_bin:$PATH" exec bash -c "$*"
'''

# The Pi's tar (GNU tar): exit 1 when a file changed while it was read (the camera keeps adding story pictures), 2
# when it failed.
PI_TAR = r'''#!/bin/bash
/usr/bin/tar "$@" || exit
[ -e "$FAKE/tar_saw_a_change" ] && { echo 'tar: ./trace_photos/cup: file changed as we read it' >&2; exit 1; }
[ -e "$FAKE/tar_fails" ] && { echo 'tar: ./todos.json: Cannot open: Permission denied' >&2; exit 2; }
exit 0
'''

DOCKER = r'''#!/bin/bash
printf 'docker %s\n' "$*" >> "$FAKE/calls"
[ -e "$FAKE/app_down" ] && { echo 'service "app" is not running' >&2; exit 1; }
case "$*" in
  "compose exec -T --user "*" app python -") cd "$FAKE_PI_HOME/desk-talk" && TMPDIR="$FAKE/container_tmp" exec "$FAKE_PYTHON" - ;;
  *) exit 1 ;;
esac
'''  # noqa: E501

LAUNCHCTL = r'''#!/bin/bash
printf 'launchctl %s\n' "$*" >> "$FAKE/calls"
'''


@dataclass
class Run:
    code: int
    out: str
    calls: list[str]


@dataclass
class Mac:
    """This computer, with a made-up Pi behind `ssh pi`."""
    fake: Path
    pi_repo: Path
    backups: Path
    env: dict[str, str]
    store: SqliteStateStore

    def run(self, script: Path, *args: str, **env: str) -> Run:
        (self.fake / 'calls').write_text('', encoding='utf-8')
        done = subprocess.run(['/bin/bash', str(script), *args], capture_output=True, text=True, timeout=60,
                              check=False, env={**self.env, **env})
        calls = (self.fake / 'calls').read_text(encoding='utf-8').splitlines()
        return Run(done.returncode, done.stdout + done.stderr, calls)

    def made(self) -> list[Path]:
        return sorted(p for p in self.backups.iterdir()) if self.backups.exists() else []


def stand_in(folder: Path, name: str, text: str) -> None:
    path = folder / name
    path.write_text(text, encoding='utf-8')
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


ROWS = [a_record(100.0), a_record(200.0, label='cup')]


@pytest.fixture
def mac(tmp_path) -> Iterator[Mac]:
    fake, pi_home, home = tmp_path / 'fake', tmp_path / 'pi home', tmp_path / 'mac home'     # spaces, on purpose
    repo = pi_home / 'desk-talk'
    (fake / 'bin').mkdir(parents=True)
    (fake / 'container_tmp').mkdir()
    (fake / 'pi_bin').mkdir()
    stand_in(fake / 'pi_bin', 'tar', PI_TAR)
    home.mkdir()
    for name, text in (('ssh', SSH), ('docker', DOCKER), ('launchctl', LAUNCHCTL)):
        stand_in(fake / 'bin', name, text)

    (repo / 'config').mkdir(parents=True)
    for name in CONFIG_FILES:
        shutil.copy(ROOT / 'config' / name, repo / 'config' / name)
    (repo / 'raspberry_pi' / 'mine').mkdir(parents=True)
    (repo / 'raspberry_pi' / '.env').write_text('TZ=Europe/Paris\nDESKTALK_DATA=../data\n', encoding='utf-8')
    (repo / 'raspberry_pi' / 'mine' / 'settings.mine.yaml').write_text('pi: {}\n', encoding='utf-8')
    data = repo / 'data'
    for name, text in (('events/2026-11-10/events.jsonl', 'an event\n'), ('habits/2026-11-10.json', '{}'),
                       ('todos.json', '[]'), ('safety_rules.json', '{}'), ('trace_photos/cup/1.jpg', 'a picture'),
                       ('logs/desktalk.log', 'a log line\n')):
        (data / name).parent.mkdir(parents=True, exist_ok=True)
        (data / name).write_text(text, encoding='utf-8')
    store = SqliteStateStore(data / 'object_states.sqlite')     # left open, so the rows are still in the WAL file
    store.append(ROWS)

    env = {'PATH': f'{fake / "bin"}:/usr/bin:/bin:/usr/sbin:/sbin', 'HOME': str(home), 'FAKE': str(fake),
           'FAKE_HOST': 'pi', 'FAKE_PI_HOME': str(pi_home), 'FAKE_PYTHON': sys.executable,
           'TMPDIR': str(tmp_path)}
    made = Mac(fake, repo, home / 'DeskTalkBackups', env, store)
    yield made
    store.close()


# ── backup_pi.sh ──

def test_a_backup_copies_the_data_and_the_pis_own_settings(mac):
    result = mac.run(BACKUP)
    assert result.code == 0, result.out
    [folder] = mac.made()
    assert len(folder.name) == len(STAMP) and folder.name[:2] == '20'
    for name in ('data/events/2026-11-10/events.jsonl', 'data/habits/2026-11-10.json', 'data/todos.json',
                 'data/safety_rules.json', 'data/trace_photos/cup/1.jpg', 'raspberry_pi/.env',
                 'raspberry_pi/mine/settings.mine.yaml'):
        assert (folder / name).read_bytes() == (mac.pi_repo / name).read_bytes(), name
    assert str(folder) in result.out and 'object_states.sqlite' in result.out


def test_the_database_is_a_whole_copy_with_the_rows_still_in_the_wal_file(mac):
    assert (mac.pi_repo / 'data' / 'object_states.sqlite-wal').stat().st_size > 0
    assert mac.run(BACKUP).code == 0
    [folder] = mac.made()
    data = folder / 'data'
    assert not (data / 'object_states.sqlite-wal').exists() and not (data / 'object_states.sqlite-shm').exists()
    copy = sqlite3.connect(data / 'object_states.sqlite')
    try:
        assert copy.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    finally:
        copy.close()
    store = SqliteStateStore(data / 'object_states.sqlite')
    try:
        assert store.changes(0.0) == ROWS
    finally:
        store.close()


def test_the_copy_inside_the_container_is_made_as_the_pis_user_and_cleared_up(mac):
    result = mac.run(BACKUP)
    [copy] = [line for line in result.calls if line.startswith('docker compose exec')]
    assert '--user' in copy                     # never root: root could leave files the app can't open
    assert list((mac.fake / 'container_tmp').iterdir()) == []


def test_the_logs_stay_unless_asked_for(mac):
    mac.run(BACKUP)
    assert not (mac.made()[0] / 'data' / 'logs').exists()
    shutil.rmtree(mac.made()[0])
    mac.run(BACKUP, '--with-logs')
    assert (mac.made()[0] / 'data' / 'logs' / 'desktalk.log').exists()


def test_ssh_never_asks_for_a_password(mac):
    calls = [line for line in mac.run(BACKUP).calls if line.startswith('ssh ')]
    assert calls and all('BatchMode=yes' in line for line in calls)


def test_only_the_newest_backups_are_kept(mac):
    mac.backups.mkdir()
    for name in ('2026-10-01-100000', '2026-10-08-100000', '2026-10-15-100000'):
        (mac.backups / name / 'data').mkdir(parents=True)
    (mac.backups / 'my notes').mkdir()                      # not a backup: left alone
    result = mac.run(BACKUP, '--keep', '2')
    assert result.code == 0, result.out
    names = [p.name for p in mac.made()]
    assert '2026-10-15-100000' in names and 'my notes' in names
    assert '2026-10-01-100000' not in names and '2026-10-08-100000' not in names
    assert len(names) == 3
    assert '2026-10-01-100000' in result.out


def test_a_failed_backup_leaves_no_half_folder_and_deletes_no_old_one(mac):
    mac.backups.mkdir()
    (mac.backups / STAMP).mkdir()
    (mac.fake / 'app_down').touch()
    result = mac.run(BACKUP, '--keep', '1')
    assert result.code != 0
    assert [p.name for p in mac.made()] == [STAMP]
    assert 'docker compose up -d' in result.out


def test_a_picture_that_changes_while_it_is_copied_is_no_failure(mac):
    (mac.fake / 'tar_saw_a_change').touch()
    result = mac.run(BACKUP)
    assert result.code == 0, result.out
    assert (mac.made()[0] / 'data' / 'todos.json').exists()


def test_a_copy_that_fails_on_the_pi_fails_the_backup(mac):
    (mac.fake / 'tar_fails').touch()
    result = mac.run(BACKUP)
    assert result.code != 0 and mac.made() == []


def test_a_pi_that_cant_be_reached_says_so(mac):
    result = mac.run(BACKUP, '--host', 'elsewhere')
    assert result.code != 0 and 'elsewhere' in result.out and mac.made() == []


def test_the_defaults_can_be_changed_with_the_environment(mac, tmp_path):
    to = tmp_path / 'other place'
    result = mac.run(BACKUP, FAKE_HOST='desk2', DESKTALK_BACKUP_HOST='desk2', DESKTALK_BACKUP_TO=str(to))
    assert result.code == 0, result.out
    assert len(list(to.iterdir())) == 1 and mac.made() == []


def test_a_pi_with_no_database_yet_still_gets_the_rest_backed_up(mac):
    mac.store.close()
    for path in (mac.pi_repo / 'data').glob('object_states.sqlite*'):
        path.unlink()
    result = mac.run(BACKUP)
    assert result.code == 0, result.out
    [folder] = mac.made()
    assert (folder / 'data' / 'todos.json').exists() and not (folder / 'data' / 'object_states.sqlite').exists()
    assert 'no object state database' in result.out


def test_an_unknown_flag_shows_how_to_use_it(mac):
    result = mac.run(BACKUP, '--nope')
    assert result.code == 2 and 'Usage' in result.out


# ── install_weekly_backup.sh ──

def plist_of(folder: Path) -> dict:
    [path] = list(folder.glob('*.plist'))
    return plistlib.loads(path.read_bytes())


def test_the_weekly_job_runs_the_backup_on_the_day_and_time_asked(mac, tmp_path):
    agents = tmp_path / 'agents'
    result = mac.run(INSTALL, '--plist-dir', str(agents), '--day', 'wednesday', '--time', '21:30', '--keep', '4',
                     '--host', 'desk2', '--to', str(tmp_path / 'to here'))
    assert result.code == 0, result.out
    job = plist_of(agents)
    assert job['ProgramArguments'][:2] == ['/bin/bash', str(BACKUP)]
    assert job['ProgramArguments'][2:] == ['--keep', '4', '--host', 'desk2', '--to', str(tmp_path / 'to here')]
    assert job['StartCalendarInterval'] == {'Weekday': 3, 'Hour': 21, 'Minute': 30}
    assert job['StandardOutPath'].startswith(str(tmp_path / 'to here'))
    [plist] = list(agents.glob('*.plist'))
    assert f'launchctl bootstrap gui/{os.getuid()} {plist}' in result.calls


@pytest.mark.skipif(shutil.which('plutil') is None, reason='plutil is only on a Mac')
def test_the_weekly_job_is_a_good_plist(mac, tmp_path):
    agents = tmp_path / 'agents'
    assert mac.run(INSTALL, '--plist-dir', str(agents), '--no-load').code == 0
    [plist] = list(agents.glob('*.plist'))
    lint = subprocess.run(['plutil', '-lint', str(plist)], capture_output=True, text=True, check=False)
    assert lint.returncode == 0, lint.stdout + lint.stderr


def test_the_defaults_are_sunday_and_the_usual_folder(mac, tmp_path):
    agents = tmp_path / 'agents'
    assert mac.run(INSTALL, '--plist-dir', str(agents), '--no-load').code == 0
    job = plist_of(agents)
    assert job['StartCalendarInterval']['Weekday'] == 0
    assert job['ProgramArguments'][2:] == []                 # backup_pi.sh's own defaults


def test_no_load_writes_the_file_only(mac, tmp_path):
    result = mac.run(INSTALL, '--plist-dir', str(tmp_path / 'agents'), '--no-load')
    assert result.code == 0 and not [c for c in result.calls if c.startswith('launchctl')]


def test_installing_again_replaces_the_job(mac, tmp_path):
    agents = tmp_path / 'agents'
    mac.run(INSTALL, '--plist-dir', str(agents))
    result = mac.run(INSTALL, '--plist-dir', str(agents), '--time', '08:05')
    launches = [c for c in result.calls if c.startswith('launchctl')]
    assert launches[0].startswith('launchctl bootout') and launches[-1].startswith('launchctl bootstrap')
    assert plist_of(agents)['StartCalendarInterval'] == {'Weekday': 0, 'Hour': 8, 'Minute': 5}


def test_uninstall_stops_the_job_and_removes_its_file(mac, tmp_path):
    agents = tmp_path / 'agents'
    mac.run(INSTALL, '--plist-dir', str(agents))
    result = mac.run(INSTALL, '--plist-dir', str(agents), '--uninstall')
    assert result.code == 0 and list(agents.glob('*.plist')) == []
    assert any(c.startswith(f'launchctl bootout gui/{os.getuid()}/') for c in result.calls)


@pytest.mark.parametrize('flags', [('--time', '25:00'), ('--time', 'noon'), ('--day', 'someday'), ('--what',)])
def test_a_wrong_day_or_time_is_refused(mac, tmp_path, flags):
    result = mac.run(INSTALL, '--plist-dir', str(tmp_path / 'agents'), *flags)
    assert result.code == 2 and not (tmp_path / 'agents').exists()


def test_the_scripts_stop_on_errors_and_can_be_run():
    for path in (BACKUP, INSTALL):
        assert 'set -euo pipefail' in path.read_text(encoding='utf-8')
        assert os.access(path, os.X_OK)
