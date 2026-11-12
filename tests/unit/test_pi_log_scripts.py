"""Starting on boot and the logs: raspberry_pi/systemd/ (what install.sh fills in and installs),
raspberry_pi/get_logs.sh (on the Pi) and scripts/pi_logs.sh (on your Mac). The scripts run for real in a made-up folder,
with stand-ins on PATH for journalctl, docker, ssh, scp and the rest; each stand-in writes down how it was called in
`calls`."""

from __future__ import annotations

import configparser
import shutil
import stat
import subprocess
import tarfile
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests.unit.test_pi_compose_file import SERVICES, env_file

ROOT = Path(__file__).resolve().parents[2]
PI = ROOT / 'raspberry_pi'
UNIT = PI / 'systemd' / 'desktalk.service'
JOURNALD = PI / 'systemd' / 'journald-desktalk.conf'
RPI_VOLATILE = '40-rpi-volatile-storage.conf'      # Raspberry Pi OS's own, which keeps the journal in memory only
REMOTE_FILE = '/home/sam/desk-talk/pi_logs/desktalk-logs-desktalk-2026-11-10_120000.tar.gz'

STAND_IN = r'''#!/bin/bash
printf '%s\n' "$(basename "$0") $*" >> "$FAKE/calls"
name=$(basename "$0")
[ -e "$FAKE/fails_$name" ] && { echo "$name went wrong" >&2; exit 1; }
case "$name" in
  id) case "$*" in -nG) cat "$FAKE/groups" ;; -un) echo sam ;; -u) echo 1000 ;; esac ;;
  hostname) echo desktalk ;;
  journalctl) echo "journal lines for: $*" ;;
  vcgencmd) echo "temp=56.5'C" ;;
  docker)
    case "$*" in
      "compose config --services") printf 'app\ncamera\ndetector\n' ;;
      *) echo "docker says: $*" ;;
    esac ;;
  ssh)
    [ -e "$FAKE/ssh_fails" ] && { echo 'ssh: Could not resolve hostname' >&2; exit 255; }
    echo 'Saving the logs ...'; cat "$FAKE/remote_path" ;;
  scp) cp "$FAKE/the_file" "$2/$(basename "$1")" ;;
  *) echo "$name output" ;;
esac
'''


@dataclass
class Result:
    code: int
    out: str
    calls: list[str]


@dataclass
class Place:
    root: Path
    fake: Path
    env: dict[str, str]

    def run(self, script: Path, *args: str) -> Result:
        calls = self.fake / 'calls'
        calls.write_text('', encoding='utf-8')
        done = subprocess.run(['/bin/bash', str(script), *args], capture_output=True, text=True, timeout=30,
                              check=False, env=self.env, cwd=self.root)
        return Result(done.returncode, done.stdout + done.stderr, calls.read_text(encoding='utf-8').splitlines())


def stand_ins(fake: Path, *names: str) -> None:
    (fake / 'bin').mkdir(parents=True)
    for name in names:
        path = fake / 'bin' / name
        path.write_text(STAND_IN, encoding='utf-8')
        path.chmod(path.stat().st_mode | stat.S_IEXEC)


@pytest.fixture
def place(tmp_path) -> Place:
    root, fake = tmp_path / 'desk talk', tmp_path / 'fake'          # a space, on purpose
    (root / 'raspberry_pi').mkdir(parents=True)
    (root / 'scripts').mkdir()
    for name in ('get_logs.sh', '.env.example'):
        shutil.copy(PI / name, root / 'raspberry_pi' / name)
    shutil.copy(ROOT / 'scripts' / 'pi_logs.sh', root / 'scripts' / 'pi_logs.sh')
    stand_ins(fake, 'id', 'hostname', 'journalctl', 'docker', 'systemctl', 'vcgencmd', 'df', 'uptime', 'ssh', 'scp')
    (fake / 'groups').write_text('sam adm docker\n', encoding='utf-8')
    (fake / 'remote_path').write_text(REMOTE_FILE + '\n', encoding='utf-8')
    (fake / 'the_file').write_bytes(b'the logs')
    return Place(root, fake, {'PATH': f'{fake / "bin"}:/usr/bin:/bin', 'FAKE': str(fake), 'HOME': str(tmp_path),
                              'TMPDIR': str(tmp_path)})


def example() -> dict[str, str]:
    return env_file(PI / '.env.example')


def ini(path: Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str  # type: ignore[assignment,method-assign]    # systemd's names have capitals
    parser.read_string(path.read_text(encoding='utf-8'))
    return parser


# ── desktalk.service ──

def test_the_service_starts_the_containers_once_docker_is_up_as_the_pis_user():
    unit = ini(UNIT)
    assert unit['Unit']['Requires'] == 'docker.service'
    assert 'docker.service' in unit['Unit']['After'].split()
    assert unit['Service']['Type'] == 'oneshot' and unit['Service']['RemainAfterExit'] == 'yes'
    assert unit['Service']['User'] == '@PI_USER@'                     # in the docker group, so never root
    assert unit['Service']['WorkingDirectory'] == '@COMPOSE_FOLDER@'
    assert unit['Install']['WantedBy'] == 'multi-user.target'


def test_the_service_never_builds_or_waits_for_the_network():
    """A build takes many minutes and needs the internet; the Pi must start with no Wi-Fi at all."""
    text = UNIT.read_text(encoding='utf-8')
    assert ini(UNIT)['Service']['ExecStart'] == '@DOCKER@ compose up -d --no-build'
    assert '--build ' not in text and 'network-online' not in text and 'network.target' not in text


def test_stopping_the_service_leaves_the_containers_to_docker():
    """No ExecStop: Docker stops them cleanly at shutdown and, with restart: unless-stopped, starts them again at boot
    by itself. A `docker compose stop` here would mark them as stopped by hand and take that second way away."""
    assert 'ExecStop' not in ini(UNIT)['Service']


def test_the_services_waits_are_filled_in_from_the_env_file():
    service = ini(UNIT)['Service']
    assert service['TimeoutStartSec'] == '@SERVICE_START_WAIT_SEC@'
    assert service['Restart'] == 'on-failure' and service['RestartSec'] == '@SERVICE_RETRY_SEC@'
    assert int(example()['SERVICE_START_WAIT_SEC']) > int(example()['SERVICE_RETRY_SEC']) > 0
    assert example()['INSTALL_SERVICE_FILE'] == '/etc/systemd/system/desktalk.service'


# ── The journal ──

def test_the_journal_stays_on_the_card_within_the_numbers_from_the_env_file():
    journal = ini(JOURNALD)['Journal']
    assert dict(journal) == {'Storage': 'persistent', 'SystemMaxUse': '@JOURNAL_MAX_USE@',
                             'MaxRetentionSec': '@JOURNAL_KEEP@', 'MaxFileSec': '@JOURNAL_FILE_SPAN@'}
    values = example()
    assert values['JOURNAL_MAX_USE'] == '200M' and values['JOURNAL_KEEP'] == '30day'


def test_a_file_spans_less_than_the_time_kept_so_old_lines_go_on_time():
    """journald deletes a whole file once its newest line is too old: files a month long would keep lines two months."""
    days = {key: int(example()[key].removesuffix('day')) for key in ('JOURNAL_KEEP', 'JOURNAL_FILE_SPAN')}
    assert days['JOURNAL_FILE_SPAN'] < days['JOURNAL_KEEP']


def test_the_journal_file_wins_over_raspberry_pi_oss_memory_only_one():
    """journald reads the files of all its .conf.d folders in name order, and the last one says."""
    installed = Path(example()['INSTALL_JOURNALD_FILE'])
    assert str(installed.parent) == '/etc/systemd/journald.conf.d'
    assert sorted([installed.name, RPI_VOLATILE])[-1] == installed.name


# ── get_logs.sh, on the Pi ──

def get_logs(place: Place, *args: str) -> Result:
    return place.run(place.root / 'raspberry_pi' / 'get_logs.sh', *args)


def saved_file(result: Result) -> Path:
    path = Path(result.out.strip().splitlines()[-1])
    assert path.is_file(), result.out
    return path


def contents(path: Path) -> dict[str, str]:
    """Each file's text, by name. A Mac's tar adds a ._ file for each file's extra attributes; the Pi's doesn't."""
    found = {}
    with tarfile.open(path) as tar:
        for member in tar.getmembers():
            name = Path(member.name).name
            file = tar.extractfile(member)
            if file is not None and not name.startswith('._'):
                found[name] = file.read().decode()
    return found


def test_saves_each_containers_journal_for_the_hours_in_the_env_file(place):
    result = get_logs(place)
    assert result.code == 0, result.out
    hours = example()['LOGS_HOURS']
    for service in SERVICES:
        assert f'journalctl -t desktalk-{service} --since=-{hours}h -o short-iso --no-pager' in result.calls
    files = contents(saved_file(result))
    assert {f'{service}.log' for service in SERVICES} <= set(files)
    assert 'journal lines for: -t desktalk-app' in files['app.log']


def test_the_hours_can_be_given(place):
    result = get_logs(place, '6')
    assert result.code == 0
    assert 'journalctl -t desktalk-camera --since=-6h -o short-iso --no-pager' in result.calls


@pytest.mark.parametrize('hours', ['abc', '0', '-3', '2h', '1 2'])
def test_hours_that_are_no_whole_number_are_refused_before_anything_is_saved(place, hours):
    result = get_logs(place, hours)
    assert result.code == 2 and 'hours' in result.out
    assert not (place.root / 'pi_logs').exists()


def test_the_file_also_says_the_boots_the_containers_the_disk_and_the_temperature(place):
    result = get_logs(place)
    files = contents(saved_file(result))
    assert 'journalctl --list-boots --no-pager' in result.calls
    assert files['boots.txt'].startswith('$ journalctl --list-boots')
    assert 'docker says: compose ps -a' in files['containers.txt']
    assert 'df output' in files['disk.txt'] and 'docker says: system df' in files['disk.txt']
    assert "temp=56.5'C" in files['temperature.txt']
    assert 'systemctl output' in files['start-on-boot.txt']


def test_it_lands_in_the_folder_from_the_env_file_dated_and_says_where(place):
    result = get_logs(place)
    path = saved_file(result)
    folder = (place.root / 'raspberry_pi' / example()['LOGS_FOLDER']).resolve()
    assert path.parent == folder and path.is_absolute()
    assert path.name.startswith('desktalk-logs-desktalk-20') and path.name.endswith('.tar.gz')


def test_a_command_that_fails_is_written_down_and_the_rest_still_saved(place):
    (place.fake / 'fails_vcgencmd').write_text('', encoding='utf-8')
    (place.fake / 'fails_journalctl').write_text('', encoding='utf-8')
    result = get_logs(place)
    assert result.code == 0
    files = contents(saved_file(result))
    assert 'vcgencmd went wrong' in files['temperature.txt'] and 'failed' in files['temperature.txt']
    assert 'failed' in files['app.log'] and 'df output' in files['disk.txt']


def test_without_the_right_to_read_the_journal_it_asks_docker_and_says_so(place):
    """Raspberry Pi OS puts its first user in adm, which may read the whole journal. Someone else gets what Docker
    still has: the containers running now, not the ones a rebuild replaced."""
    (place.fake / 'groups').write_text('sam docker\n', encoding='utf-8')
    result = get_logs(place, '6')
    assert result.code == 0
    assert 'docker compose logs --since 6h --timestamps --no-color app' in result.calls
    assert not any(line.startswith('journalctl -t') for line in result.calls)
    assert 'adm' in result.out


def test_only_the_newest_files_are_kept(place):
    folder = place.root / 'pi_logs'
    folder.mkdir()
    keep = int(example()['LOGS_KEEP'])
    for day in range(1, keep + 3):
        (folder / f'desktalk-logs-desktalk-2026-01-{day:02d}_000000.tar.gz').write_bytes(b'old')
    (folder / 'mine.txt').write_text('not ours', encoding='utf-8')
    newest = saved_file(get_logs(place))
    left = sorted(p.name for p in folder.glob('desktalk-logs-*'))
    assert len(left) == keep and newest.name in left
    assert 'desktalk-logs-desktalk-2026-01-01_000000.tar.gz' not in left
    assert (folder / 'mine.txt').exists()


# ── pi_logs.sh, on your Mac ──

def pi_logs(place: Place, *args: str) -> Result:
    return place.run(place.root / 'scripts' / 'pi_logs.sh', *args)


def test_asks_the_pi_with_the_defaults_and_copies_the_file_back(place):
    result = pi_logs(place)
    assert result.code == 0, result.out
    values = example()
    ssh = next(line for line in result.calls if line.startswith('ssh '))
    assert ssh.split()[-4:] == [values['LOGS_SSH_HOST'], 'bash',
                                f'{values["LOGS_PI_REPO"]}/raspberry_pi/get_logs.sh', values['LOGS_HOURS']]
    copied = place.root / values['LOGS_MAC_FOLDER'] / Path(REMOTE_FILE).name
    assert copied.read_bytes() == b'the logs'
    assert f'scp {values["LOGS_SSH_HOST"]}:{REMOTE_FILE} {copied.parent}' in result.calls
    assert result.out.strip().splitlines()[-1] == str(copied)


def test_the_pi_the_hours_the_repo_and_the_folder_can_be_given(place, tmp_path):
    result = pi_logs(place, '--host', 'mypi', '--hours', '3', '--repo', 'code/dt', '--to', str(tmp_path / 'here'))
    assert result.code == 0, result.out
    ssh = next(line for line in result.calls if line.startswith('ssh '))
    assert ssh.split()[-4:] == ['mypi', 'bash', 'code/dt/raspberry_pi/get_logs.sh', '3']
    assert (tmp_path / 'here' / Path(REMOTE_FILE).name).is_file()


def test_the_pi_can_come_from_the_same_name_the_pi_tests_use(place):
    place.env['DESKTALK_PI'] = 'otherpi'
    result = pi_logs(place)
    assert any(line.startswith('ssh ') and ' otherpi bash ' in line for line in result.calls)


def test_a_pi_that_cant_be_reached_says_so_and_copies_nothing(place):
    (place.fake / 'ssh_fails').write_text('', encoding='utf-8')
    result = pi_logs(place)
    assert result.code == 1 and 'ssh' in result.out
    assert not any(line.startswith('scp') for line in result.calls)


@pytest.mark.parametrize('args', [('--hours', 'lots'), ('--host',), ('--what',)])
def test_wrong_flags_are_refused_before_asking_the_pi(place, args):
    result = pi_logs(place, *args)
    assert result.code == 2
    assert not any(line.startswith('ssh') for line in result.calls)


@pytest.mark.parametrize('script', [PI / 'get_logs.sh', ROOT / 'scripts' / 'pi_logs.sh'])
def test_the_scripts_stop_on_errors_and_can_be_run(script):
    assert 'set -euo pipefail' in script.read_text(encoding='utf-8')
    assert script.stat().st_mode & stat.S_IXUSR


def test_the_pulled_logs_are_not_in_git():
    lines = (ROOT / '.gitignore').read_text(encoding='utf-8').splitlines()
    assert f'/{example()["LOGS_MAC_FOLDER"]}/' in lines
    assert (PI / example()['LOGS_FOLDER']).resolve() == ROOT / example()['LOGS_MAC_FOLDER']
