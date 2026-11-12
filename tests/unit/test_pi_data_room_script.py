"""raspberry_pi/data_room.sh, run for real in a made-up Pi: a copy of the files it reads in a temporary folder, with
stand-ins on PATH for everything that would touch the machine (df, stat, mount, mkfs.ext4, sudo, systemctl ...). Each
stand-in writes down how it was called in `calls`, so nothing here can change this computer's disks or needs root.

The stand-ins keep a pretend machine in the state folder: what's mounted where (mounts.json), which loop device is
backed by which file (loops.json), and what each image's filesystem holds (disks/<the image's inode>/). An image file
holds only its own size, as text, so no test ever writes gigabytes. `systemctl start` runs the installed unit's real
ExecStart line, unescaped the way systemd does it, so the unit itself is tested too."""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PI = ROOT / 'raspberry_pi'
UNIT = 'desktalk-data-room.service'
MB = 1024 * 1024
MARKER = '.desktalk-data-room'

STAND_IN = r'''#!/bin/bash
# Writes down the call, then does what the test's state folder says.
printf '%s\n' "$(basename "$0") $*" >> "$FAKE/calls"
name=$(basename "$0")
case "$name" in
  df) printf 'Filesystem 1M-blocks Used Available Capacity Mounted on\n/dev/mmcblk0p2 100000 1000 %s 1%% /\n' "$(cat "$FAKE/free_mb")" ;;
  id) case "$1" in -u) echo 1000 ;; -g) echo 1001 ;; *) exec /usr/bin/id "$@" ;; esac ;;
  e2fsck) exit "$(cat "$FAKE/e2fsck_code" 2>/dev/null || echo 0)" ;;
  diff) [ -e "$FAKE/copy_differs" ] && { echo 'Files differ'; exit 1; }; exec /usr/bin/diff "$@" ;;
  sudo)
    [ -e "$FAKE/sudo_fails" ] && { echo 'sudo: a password is required' >&2; exit 1; }
    cmd=$1; shift
    if [ -x "$FAKE/bin/$cmd" ]; then exec "$FAKE/bin/$cmd" "$@"; fi
    exec "$cmd" "$@" ;;
  *) exec "$FAKE_PYTHON" -I -S "$FAKE/machine.py" "$name" "$@" ;;
esac
'''  # noqa: E501

MACHINE = r'''
import json, os, shlex, shutil, subprocess, sys
from pathlib import Path

FAKE = Path(os.environ['FAKE'])
MB = 1024 * 1024


def load(name):
    path = FAKE / name
    return json.loads(path.read_text()) if path.exists() else ([] if name == 'mounts.json' else {})


def save(name, value):
    (FAKE / name).write_text(json.dumps(value))


def real(path):
    return os.path.realpath(path)


def disk_of(image):
    return FAKE / 'disks' / str(os.stat(image).st_ino)


def size_of(image):
    return int(Path(image).read_text().strip() or 0)


def mount_at(target):
    return next((m for m in load('mounts.json') if m['target'] == real(target)), None)


def stat_(fmt, path):
    if fmt == '%d':
        mount = mount_at(path)
        print(1 if mount is None else 100 + sum(map(ord, mount['source'])))
    elif fmt == '%s':
        print(size_of(path))
    elif fmt == '%b %B':
        blocks = size_of(path) // 512
        if (FAKE / 'short_blocks').exists():
            blocks //= 2
        print(f'{blocks} 512')
    else:
        return 2
    return 0


def mount(args):
    options, source, target = args[1], args[2], args[3]
    if mount_at(target) is not None:
        return 32
    mounts, loops = load('mounts.json'), load('loops.json')
    disk = disk_of(source)
    if 'loop' in options.split(','):
        name = next(f'/dev/loop{n}' for n in range(100) if f'/dev/loop{n}' not in loops)
        loops[name] = real(source)
        source = name
    for child in sorted(disk.iterdir()):
        shutil.move(str(child), str(Path(target) / child.name))
    mounts.append({'source': source, 'target': real(target), 'disk': str(disk), 'options': options})
    save('mounts.json', mounts)
    save('loops.json', loops)
    return 0


def umount(target):
    mount = mount_at(target)
    if mount is None:
        return 32
    for child in sorted(Path(target).iterdir()):
        shutil.move(str(child), str(Path(mount['disk']) / child.name))
    save('mounts.json', [m for m in load('mounts.json') if m != mount])
    save('loops.json', {k: v for k, v in load('loops.json').items() if k != mount['source']})
    return 0


def unit_line(unit, key):
    text = (FAKE / 'etc' / 'systemd' / 'system' / unit).read_text()
    value = next(line.split('=', 1)[1] for line in text.splitlines() if line.startswith(key + '='))
    return [word.replace('$$', '$').replace('%%', '%') for word in shlex.split(value)]


def systemctl(args):
    what, unit = args[0], (args[1] if len(args) > 1 else '')
    active = FAKE / f'active_{unit}'
    if what in ('stop', 'restart') and active.exists():
        subprocess.run(unit_line(unit, 'ExecStop'), check=False)
        active.unlink()
    if what in ('start', 'restart') and not active.exists():
        if not (FAKE / 'etc' / 'systemd' / 'system' / unit).exists():
            return 5
        code = subprocess.run(unit_line(unit, 'ExecStart'), check=False).returncode
        if code:
            return code
        active.touch()
    if what == 'enable':
        (FAKE / f'enabled_{unit}').touch()
    if what == 'disable':
        (FAKE / f'enabled_{unit}').unlink(missing_ok=True)
    if what == 'is-enabled':
        return 0 if (FAKE / f'enabled_{unit}').exists() else 1
    if what == 'is-active':
        return 0 if active.exists() else 3
    return 0


def main(name, args):
    if name == 'stat':
        return stat_(args[1], args[2])
    if name == 'findmnt':                                   # findmnt -n -o SOURCE --mountpoint <folder>
        found = mount_at(args[-1])
        if found is None:
            return 1
        print(found['source'])
        return 0
    if name == 'losetup':
        if args[0] == '-c':
            return 0
        image = real(args[-1])                              # losetup -l -n -O NAME -j <file>
        for loop, backing in load('loops.json').items():
            if backing == image:
                print(loop)
        return 0
    if name == 'fallocate':                                 # fallocate -l <size>M <file>
        if (FAKE / 'fallocate_fails').exists():
            return 1
        Path(args[-1]).write_text(str(int(args[1].removesuffix('M')) * MB))
        return 0
    if name == 'mkfs.ext4':
        disk = disk_of(args[-1])
        shutil.rmtree(disk, ignore_errors=True)
        (disk / 'lost+found').mkdir(parents=True)
        return 0
    if name == 'mount':
        return mount(args)
    if name == 'umount':
        return umount(args[-1])
    if name == 'systemctl':
        return systemctl(args)
    return 0                                                # resize2fs


sys.exit(main(sys.argv[1], sys.argv[2:]))
'''

STAND_INS = ('df', 'id', 'stat', 'findmnt', 'losetup', 'fallocate', 'mkfs.ext4', 'e2fsck', 'mount', 'umount',
             'resize2fs', 'sudo', 'systemctl', 'diff')


@dataclass
class Result:
    code: int
    out: str
    calls: list[str]


@dataclass
class RoomPi:
    root: Path
    fake: Path
    temp: Path
    env_vars: dict[str, str] = field(default_factory=dict)

    @property
    def data(self) -> Path:
        return self.root / 'data'

    @property
    def image(self) -> Path:
        return self.root / 'data.img'

    @property
    def example(self) -> Path:
        return self.root / 'raspberry_pi' / '.env.example'

    @property
    def unit_file(self) -> Path:
        return self.fake / 'etc' / 'systemd' / 'system' / UNIT

    def state(self, name: str, value: str = '') -> None:
        (self.fake / name).write_text(value, encoding='utf-8')

    def clear(self, name: str) -> None:
        (self.fake / name).unlink(missing_ok=True)

    def run(self, *args: str) -> Result:
        calls = self.fake / 'calls'
        calls.write_text('', encoding='utf-8')
        done = subprocess.run(['/bin/bash', str(self.root / 'raspberry_pi' / 'data_room.sh'), *args],
                              capture_output=True, text=True, timeout=30, check=False, env=self.env_vars)
        return Result(done.returncode, done.stdout + done.stderr, calls.read_text(encoding='utf-8').splitlines())

    def status(self) -> str:
        result = self.run('status')
        assert result.code == 0, result.out
        return result.out

    def machine(self, *args: str) -> int:
        """Run one stand-in by hand, as the Pi would at boot."""
        return subprocess.run([str(self.fake / 'bin' / args[0]), *args[1:]], env=self.env_vars, check=False,
                              capture_output=True).returncode

    def mounts(self) -> list[dict[str, str]]:
        path = self.fake / 'mounts.json'
        return json.loads(path.read_text(encoding='utf-8')) if path.exists() else []

    def old_folders(self) -> list[Path]:
        return sorted(self.root.glob('data.before-room-*'))

    def change_example(self, name: str, value: str) -> None:
        text = self.example.read_text(encoding='utf-8')
        self.example.write_text(re.sub(rf'^{name}=.*$', f'{name}={value}', text, flags=re.M), encoding='utf-8')


HISTORY = {
    'events.jsonl': b'an event\n',
    'history.db': b'the database',
    'history.db-wal': b'what SQLite has not folded in yet',
    'history.db-shm': b'its shared memory',
    'story/one.jpg': b'a picture',
    'logs/desktalk.log': b'a log line\n',
}


def files_in(folder: Path) -> dict[str, bytes]:
    """Every file under the folder, but the filesystem's own lost+found and the room's marker."""
    return {str(p.relative_to(folder)): p.read_bytes() for p in sorted(folder.rglob('*'))
            if p.is_file() and 'lost+found' not in p.parts and p.name != MARKER}


def make_pi(tmp_path: Path, folder: str) -> RoomPi:
    root, fake, temp = tmp_path / folder, tmp_path / 'fake', tmp_path / 'temp'
    (root / 'raspberry_pi' / 'systemd').mkdir(parents=True)
    for name in ('data_room.sh', '.env.example'):
        if (PI / name).exists():
            shutil.copy(PI / name, root / 'raspberry_pi' / name)
    if (PI / 'systemd' / UNIT).exists():
        shutil.copy(PI / 'systemd' / UNIT, root / 'raspberry_pi' / 'systemd' / UNIT)
    for name, content in HISTORY.items():
        (root / 'data' / name).parent.mkdir(parents=True, exist_ok=True)
        (root / 'data' / name).write_bytes(content)
    (fake / 'bin').mkdir(parents=True)
    (fake / 'disks').mkdir()
    temp.mkdir()
    (fake / 'machine.py').write_text(MACHINE, encoding='utf-8')
    for name in STAND_INS:
        path = fake / 'bin' / name
        path.write_text(STAND_IN, encoding='utf-8')
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
    made = RoomPi(root, fake, temp)
    made.state('free_mb', '90000')
    if made.example.exists():
        made.change_example('INSTALL_DATA_ROOM_SERVICE_FILE', str(made.unit_file))
    made.env_vars = {'PATH': f'{fake / "bin"}:/usr/bin:/bin', 'FAKE': str(fake), 'FAKE_PYTHON': sys.executable,
                     'HOME': str(tmp_path), 'TMPDIR': str(temp)}
    return made


@pytest.fixture
def pi(tmp_path) -> RoomPi:
    return make_pi(tmp_path, 'desk talk')                  # a space, on purpose


def sudo_calls(result: Result) -> list[str]:
    return [call for call in result.calls if call.startswith('sudo ')]


def unmount_as_after_a_bad_boot(pi: RoomPi) -> None:
    assert pi.machine('umount', str(pi.data)) == 0
    pi.clear(f'active_{UNIT}')


def changed_nothing(pi: RoomPi, result: Result) -> None:
    assert files_in(pi.data) == HISTORY
    assert not pi.image.exists() and not Path(f'{pi.image}.new').exists()
    assert not pi.old_folders() and not pi.mounts() and not list(pi.temp.iterdir())
    assert not [call for call in result.calls if call.split()[0] in ('fallocate', 'mkfs.ext4', 'mount')]


def test_status_says_none_room_elsewhere_or_unmounted(pi):
    assert pi.status() == 'none\n'
    assert pi.run('make').code == 0
    assert pi.status() == 'room\n'
    unmount_as_after_a_bad_boot(pi)
    assert pi.status() == 'unmounted\n'
    (pi.fake / 'mounts.json').write_text(json.dumps([{'source': '/dev/sda1', 'target': os.path.realpath(pi.data),
                                                      'disk': str(pi.fake / 'usb')}]), encoding='utf-8')
    assert pi.status() == 'elsewhere\n'


def test_makes_a_room_of_the_set_size_and_moves_the_data_in(pi):
    before = os.stat(pi.data)
    result = pi.run('make')
    assert result.code == 0, result.out
    assert pi.image.read_text() == str(8192 * MB)
    assert f'fallocate -l 8192M {pi.image}.new' in result.calls
    assert f'mkfs.ext4 -q -F -m 0 -E root_owner=1000:1001 {pi.image}.new' in result.calls
    [mount] = pi.mounts()
    assert mount['target'] == os.path.realpath(pi.data) and mount['options'] == 'loop,noatime'
    assert files_in(pi.data) == HISTORY                     # in the room now, -wal and -shm too
    assert (pi.data / MARKER).exists()
    [old] = pi.old_folders()
    assert files_in(old) == HISTORY and os.stat(old).st_ino == before.st_ino      # the old folder itself, renamed
    assert pi.unit_file.exists() and (pi.fake / f'enabled_{UNIT}').exists()
    assert f'sudo systemctl enable {UNIT}' in result.calls and f'sudo systemctl start {UNIT}' in result.calls
    assert f'sudo mkdir -m 755 -- {pi.data}' in result.calls
    assert str(old) in result.out and 'rm -rf' in result.out
    assert not Path(f'{pi.image}.new').exists() and not list(pi.temp.iterdir())


def test_a_card_too_small_gets_a_smaller_room(pi):
    pi.state('free_mb', '5000')                             # 5000 - 2000 kept free for updates = 3000
    result = pi.run('make')
    assert result.code == 0, result.out
    assert f'fallocate -l 3000M {pi.image}.new' in result.calls
    assert pi.image.read_text() == str(3000 * MB)
    assert '3000 MB' in result.out
    assert pi.status() == 'room\n'


def test_below_the_smallest_room_it_stops_and_changes_nothing(pi):
    pi.state('free_mb', '2500')                             # 500 MB of room, and the smallest is 1024
    result = pi.run('make')
    assert result.code == 1
    assert 'free 524 MB more' in result.out
    changed_nothing(pi, result)
    assert not sudo_calls(result)


def test_a_sparse_or_short_image_is_refused(pi):
    pi.state('short_blocks')
    result = pi.run('make')
    assert result.code == 1
    assert 'set aside' in result.out
    assert not [call for call in result.calls if call.split()[0] in ('mkfs.ext4', 'mount', 'sudo')]
    assert files_in(pi.data) == HISTORY and not pi.old_folders() and not list(pi.temp.iterdir())
    assert not pi.image.exists() and not Path(f'{pi.image}.new').exists()      # only the new file goes


def test_a_failed_copy_check_leaves_the_data_folder_as_it_was(pi):
    before = os.stat(pi.data)
    pi.state('copy_differs')
    result = pi.run('make')
    assert result.code == 1
    assert files_in(pi.data) == HISTORY and os.stat(pi.data).st_ino == before.st_ino
    assert not pi.image.exists() and not Path(f'{pi.image}.new').exists()
    assert not pi.old_folders() and not pi.mounts() and not list(pi.temp.iterdir())
    assert not pi.unit_file.exists()


def test_an_existing_image_is_never_made_again(pi):
    assert pi.run('make').code == 0
    unmount_as_after_a_bad_boot(pi)
    room = pi.image.read_text()
    inode = os.stat(pi.image).st_ino
    pi.state('e2fsck_code', '4')                            # damage the safe repair can't fix
    result = pi.run('make')
    assert result.code == 1
    assert "isn't mounted" in result.out and 'journalctl -u desktalk-data-room' in result.out
    assert not [call for call in result.calls if call.split()[0] in ('fallocate', 'mkfs.ext4')]
    assert pi.image.read_text() == room and os.stat(pi.image).st_ino == inode
    assert not pi.mounts() and pi.status() == 'unmounted\n'

    pi.state('e2fsck_code', '1')                            # the safe repair fixed it
    result = pi.run('make')
    assert result.code == 0, result.out
    assert pi.status() == 'room\n' and files_in(pi.data) == HISTORY
    assert not [call for call in result.calls if call.split()[0] in ('fallocate', 'mkfs.ext4')]


def test_a_failed_first_mount_puts_the_old_folder_back(pi):
    before = os.stat(pi.data)
    pi.state('e2fsck_code', '8')
    result = pi.run('make')
    assert result.code == 1
    assert 'journalctl -u desktalk-data-room' in result.out
    assert files_in(pi.data) == HISTORY and os.stat(pi.data).st_ino == before.st_ino
    assert not pi.old_folders() and not pi.mounts()
    assert not pi.image.exists() and not Path(f'{pi.image}.new').exists()
    assert not (pi.fake / f'enabled_{UNIT}').exists() and not (pi.fake / f'active_{UNIT}').exists()
    assert f'sudo systemctl disable {UNIT}' in result.calls


def test_grows_when_the_size_goes_up_and_refuses_to_shrink(pi):
    assert pi.run('make').code == 0
    [mount] = pi.mounts()
    pi.change_example('DATA_ROOM_SIZE_MB', '10000')
    result = pi.run('make')
    assert result.code == 0, result.out
    assert f'fallocate -l 10000M {pi.image}' in result.calls
    assert f'sudo losetup -c {mount["source"]}' in result.calls
    assert f'sudo resize2fs {mount["source"]}' in result.calls
    assert pi.image.read_text() == str(10000 * MB)
    assert pi.status() == 'room\n'

    pi.change_example('DATA_ROOM_SIZE_MB', '4096')
    result = pi.run('make')
    assert result.code == 0
    assert "isn't supported" in result.out and '10000 MB' in result.out
    assert pi.image.read_text() == str(10000 * MB)
    assert not sudo_calls(result) and not [call for call in result.calls if call.startswith('fallocate')]

    pi.change_example('DATA_ROOM_SIZE_MB', '10000')
    result = pi.run('make')                                 # the same size: nothing to do
    assert result.code == 0
    assert not sudo_calls(result) and not [call for call in result.calls if call.startswith('fallocate')]



def test_a_grow_without_sudo_says_how_to_finish_it(pi):
    assert pi.run('make').code == 0
    [mount] = pi.mounts()
    pi.change_example('DATA_ROOM_SIZE_MB', '10000')
    pi.state('sudo_fails')
    result = pi.run('make')
    assert result.code == 1
    assert f'sudo losetup -c {mount["source"]} && sudo resize2fs {mount["source"]}' in result.out
    assert pi.status() == 'room\n' and files_in(pi.data) == HISTORY


def test_a_data_folder_on_another_disk_is_left_alone(pi):
    (pi.fake / 'mounts.json').write_text(json.dumps([{'source': '/dev/sda1', 'target': os.path.realpath(pi.data),
                                                      'disk': str(pi.fake / 'usb')}]), encoding='utf-8')
    assert pi.status() == 'elsewhere\n'
    result = pi.run('make')
    assert result.code == 0
    assert 'another disk' in result.out
    assert not sudo_calls(result) and not pi.image.exists() and files_in(pi.data) == HISTORY


def test_a_dry_run_changes_nothing(pi):
    result = pi.run('make', '--dry-run')
    assert result.code == 0, result.out
    assert 'Would make' in result.out and '8192 MB' in result.out
    changed_nothing(pi, result)
    assert not sudo_calls(result) and not pi.unit_file.exists()

    assert pi.run('make').code == 0
    pi.change_example('DATA_ROOM_SIZE_MB', '10000')
    result = pi.run('make', '--dry-run')
    assert result.code == 0 and 'Would grow' in result.out
    assert not sudo_calls(result) and pi.image.read_text() == str(8192 * MB)

    unmount_as_after_a_bad_boot(pi)
    result = pi.run('make', '--dry-run')
    assert result.code == 0 and 'Would mount' in result.out
    assert not sudo_calls(result) and pi.status() == 'unmounted\n'


@pytest.mark.parametrize('folder', ['desk talk', 'my 100% "desk" $HOME\\talk'])
def test_works_with_a_space_in_the_folder_name(tmp_path, folder):
    pi = make_pi(tmp_path, folder)
    result = pi.run('make')
    assert result.code == 0, result.out
    [mount] = pi.mounts()
    assert mount['target'] == os.path.realpath(pi.data)
    assert json.loads((pi.fake / 'loops.json').read_text())[mount['source']] == os.path.realpath(pi.image)
    assert files_in(pi.data) == HISTORY and pi.status() == 'room\n'
    [old] = pi.old_folders()
    assert files_in(old) == HISTORY


def test_a_move_cut_short_by_a_power_cut_is_not_started_again(pi):
    """Cut between putting the old folder aside and naming the new file: the old folder holds the history."""
    old = pi.root / 'data.before-room-2026-11-11-120000'
    pi.data.rename(old)
    pi.data.mkdir()
    Path(f'{pi.image}.new').write_text(str(8192 * MB))
    result = pi.run('make')
    assert result.code == 1
    assert str(old) in result.out
    assert files_in(old) == HISTORY and Path(f'{pi.image}.new').exists()
    assert not [call for call in result.calls if call.split()[0] in ('fallocate', 'mkfs.ext4', 'sudo')]


def test_a_leftover_new_file_from_a_stopped_try_is_made_again(pi):
    Path(f'{pi.image}.new').write_text('half made')
    result = pi.run('make')
    assert result.code == 0, result.out
    assert pi.status() == 'room\n' and files_in(pi.data) == HISTORY


def test_the_unit_repairs_only_safely_and_mounts_before_docker():
    text = (PI / 'systemd' / UNIT).read_text(encoding='utf-8')
    lines = [line for line in text.splitlines() if line and not line.startswith('#')]
    assert 'Type=oneshot' in lines and 'RemainAfterExit=yes' in lines
    assert 'Before=docker.service' in lines and 'WantedBy=multi-user.target' in lines
    assert not any(line.startswith(('Requires=', 'RequiredBy=')) for line in lines)   # Docker starts without it
    start = next(line for line in lines if line.startswith('ExecStart='))
    stop = next(line for line in lines if line.startswith('ExecStop='))
    assert 'e2fsck -p ' in start and ' -y' not in '\n'.join(lines) and '-ge 4' in start
    assert 'mount -o loop,noatime ' in start and 'umount ' in stop
    assert start.endswith(' "@IMAGE@" "@DATA@"') and stop.endswith(' "@DATA@"')       # each path one word
    assert '@' not in start.removesuffix(' "@IMAGE@" "@DATA@"')                         # never inside the script
    assert not re.search(r'(?<!\$)\$(?!\$)', start.replace('$$', ''))                # systemd would fill in a lone $


def test_the_settings_are_in_the_env_example_with_a_comment():
    lines = (PI / '.env.example').read_text(encoding='utf-8').splitlines()
    wanted = {'DATA_ROOM_SIZE_MB': '8192', 'DATA_ROOM_SMALLEST_MB': '1024', 'DATA_ROOM_FILE': '../data.img',
              'DATA_ROOM_MARKER': '.desktalk-data-room', 'DATA_ROOM_ON': 'no', 'APP_MEMORY_DATA_SIZE': '200m',
              'INSTALL_DATA_ROOM_SERVICE_FILE': f'/etc/systemd/system/{UNIT}'}
    for name, value in wanted.items():
        [at] = [i for i, line in enumerate(lines) if line.startswith(f'{name}=')]
        assert lines[at] == f'{name}={value}'
        assert any(line.startswith('#') for line in lines[at - 3:at]), f'{name} needs a comment'


def test_it_says_how_to_use_it(pi):
    result = pi.run('grow')
    assert result.code == 2 and 'Usage' in result.out
