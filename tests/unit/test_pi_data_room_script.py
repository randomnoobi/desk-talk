"""raspberry_pi/data_room.sh, run for real in a made-up Pi: a copy of the files it reads in a temporary folder, with
stand-ins on PATH for everything that would touch the machine (df, stat, mount, mkfs.ext4, sudo, systemctl ...). Each
stand-in writes down how it was called in `calls`, so nothing here can change this computer's disks or needs root.

The stand-ins keep a pretend machine in the state folder: what's mounted where (mounts.json), the kernel's view of each
loop device (sys/block/loopN/: its backing file and size, which the script reads through DESKTALK_SYS), and what each
image's filesystem holds (disks/<the image's inode>/). An image file holds only its own size, as text, so no test ever
writes gigabytes. `systemctl start` runs the installed unit's real ExecStart line, unescaped the way systemd does it, so
the unit itself is tested too."""

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
  df) [ -e "$FAKE/df_fails" ] && exit 1
      printf 'Filesystem 1M-blocks Used Available Capacity Mounted on\n/dev/mmcblk0p2 100000 1000 %s 1%% /\n' "$(cat "$FAKE/free_mb")" ;;
  id) case "$1" in -u) echo 1000 ;; -g) echo 1001 ;; *) exec /usr/bin/id "$@" ;; esac ;;
  e2fsck) exit "$(cat "$FAKE/e2fsck_code" 2>/dev/null || echo 0)" ;;
  diff)
    [ -e "$FAKE/copy_differs" ] && { echo 'Files differ'; exit 1; }
    [ -e "$FAKE/diff_code" ] && { echo "Only in ${@: -1}: lost+found"; exit "$(cat "$FAKE/diff_code")"; }
    exec /usr/bin/diff "$@" ;;
  du) [ -e "$FAKE/du_says_nothing" ] && exit 1
      exec /usr/bin/du "$@" ;;
  mv)  # GNU's mv -T: never into a folder that's there
    [ "$1" = -T ] || exec /bin/mv "$@"
    shift; [ "$1" = -- ] && shift
    if [ -d "$2" ] && [ -n "$(ls -A "$2")" ]; then echo "mv: cannot overwrite '$2'" >&2; exit 1; fi
    [ -d "$2" ] && rmdir "$2"
    exec /bin/mv -- "$1" "$2" ;;
  sudo)
    [ -e "$FAKE/sudo_fails" ] && { echo 'sudo: a password is required' >&2; exit 1; }
    cmd=$1; shift
    [ -e "$FAKE/sudo_fails_for_$cmd" ] && { echo "$cmd: it went wrong" >&2; exit 1; }
    if [ -x "$FAKE/bin/$cmd" ]; then exec "$FAKE/bin/$cmd" "$@"; fi
    exec "$cmd" "$@" ;;
  *) exec "$FAKE_PYTHON" -I -S "$FAKE/machine.py" "$name" "$@" ;;
esac
'''  # noqa: E501

MACHINE = r'''
import json, os, shlex, shutil, subprocess, sys
from pathlib import Path

FAKE = Path(os.environ['FAKE'])
SYS = Path(os.environ['DESKTALK_SYS'])
MB = 1024 * 1024
MARKER = '.desktalk-data-room'
TRIMS_ON = 4294966784


def has(name):
    return (FAKE / name).exists()


def load():
    path = FAKE / 'mounts.json'
    return json.loads(path.read_text()) if path.exists() else []


def save(mounts):
    (FAKE / 'mounts.json').write_text(json.dumps(mounts))


def real(path):
    return os.path.realpath(path)


def disk_of(image):
    return FAKE / 'disks' / str(os.stat(image).st_ino)


def size_of(image):
    return int(Path(image).read_text().strip() or 0)


def mount_at(target):
    return next((m for m in load() if m['target'] == real(target)), None)


def stat_(fmt, path):
    if fmt == '%d':
        mount = mount_at(path)
        print(1 if mount is None else 100 + sum(map(ord, mount['source'])))
    elif fmt == '%s':
        print(size_of(path))
    elif fmt == '%b %B':
        answers = FAKE / 'blocks_answers'                   # this many good answers, then none
        if answers.exists():
            left = int(answers.read_text())
            if left <= 0:
                return 1
            answers.write_text(str(left - 1))
        blocks = size_of(path) // 512
        if has('short_blocks') or has('lost_blocks'):
            blocks //= 2
        print(f'{blocks} 512')
    else:
        return 2
    return 0


def mount(args):
    options, source, target = args[1], args[2], args[3]
    if mount_at(target) is not None:
        return 32
    disk = disk_of(source)
    if 'loop' in options.split(','):
        name = next(f'loop{n}' for n in range(100) if not (SYS / 'block' / f'loop{n}').exists())
        (SYS / 'block' / name / 'loop').mkdir(parents=True)
        (SYS / 'block' / name / 'loop' / 'backing_file').write_text(real(source) + '\n')
        (SYS / 'block' / name / 'size').write_text(f'{size_of(source) // 512}\n')
        (SYS / 'block' / name / 'queue').mkdir()
        discard = SYS / 'block' / name / 'queue' / 'discard_max_bytes'
        discard.write_text(f'{TRIMS_ON}\n')                # the kernel lets trims through to the file
        if has('discard_stuck'):
            discard.chmod(0o444)
        source = f'/dev/{name}'
    for child in sorted(disk.iterdir()):
        shutil.move(str(child), str(Path(target) / child.name))
    if has('marker_vanishes') and 'noatime' in options:
        (Path(target) / MARKER).unlink(missing_ok=True)
    save(load() + [{'source': source, 'target': real(target), 'disk': str(disk), 'options': options}])
    return 0


def umount(target):
    mount = mount_at(target)
    if mount is None or (has('umount_refuses') and 'noatime' in mount['options']):     # only the unit's mount
        return 32
    for child in sorted(Path(target).iterdir()):
        shutil.move(str(child), str(Path(mount['disk']) / child.name))
    save([m for m in load() if m != mount])
    shutil.rmtree(SYS / 'block' / mount['source'].removeprefix('/dev/'), ignore_errors=True)
    if has('short_after_umount'):
        (FAKE / 'short_blocks').touch()
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
        active.unlink()                                     # systemd counts it stopped even if ExecStop failed
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
    return 0


def main(name, args):
    if name == 'stat':
        return stat_(args[1], args[2])
    if name == 'findmnt':                                   # findmnt -n [-o SOURCE] --mountpoint <folder>
        found = mount_at(args[-1])
        if found is None:
            return 1
        print(found['source'])
        return 0
    if name == 'losetup':                                   # losetup -c <loop>: the kernel sees the file's new size
        loop = SYS / 'block' / args[1].removeprefix('/dev/')
        if not loop.exists():
            return 1
        (loop / 'size').write_text(f'{size_of((loop / "loop" / "backing_file").read_text().strip()) // 512}\n')
        return 0
    if name == 'fallocate':                                 # fallocate -l <bytes or MB>M <file>
        size = args[1]
        Path(args[-1]).write_text(str(int(size[:-1]) * MB if size.endswith('M') else int(size)))
        if not has('stays_short'):
            (FAKE / 'lost_blocks').unlink(missing_ok=True)
        return 0
    if name == 'mkfs.ext4':
        disk = disk_of(args[-1])
        shutil.rmtree(disk, ignore_errors=True)
        (disk / 'lost+found').mkdir(parents=True)
        if has('short_after_mkfs'):
            (FAKE / 'short_blocks').touch()
        return 0
    if name == 'mount':
        return mount(args)
    if name == 'umount':
        return umount(args[-1])
    if name == 'systemctl':
        return systemctl(args)
    if name == 'resize2fs':
        return 1 if has('resize2fs_fails') else 0
    if name == 'debugfs':                                   # debugfs -R "stat /<name>" <file>
        if has('debugfs_misreads'):                         # a debugfs whose output the script doesn't know
            print('something else')
            return 0
        disk = disk_of(args[-1])
        found = disk / args[1].split(' ', 1)[1].lstrip('/')
        if not disk.is_dir():
            print('Bad magic number in super-block while trying to open', args[-1], file=sys.stderr)
        elif found.is_file():
            print('Inode: 12   Type: regular    Mode:  0644   Flags: 0x80000')
        else:
            print(found.name + ': File not found by ext2_lookup', file=sys.stderr)
        return 0
    return 0


sys.exit(main(sys.argv[1], sys.argv[2:]))
'''

STAND_INS = ('df', 'id', 'stat', 'findmnt', 'losetup', 'fallocate', 'mkfs.ext4', 'e2fsck', 'mount', 'umount',
             'resize2fs', 'sudo', 'systemctl', 'diff', 'mv', 'du', 'debugfs')


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
    def new_image(self) -> Path:
        return self.root / 'data.img.new'

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

    def loop(self, device: str) -> Path:
        return self.fake / 'sys' / 'block' / device.removeprefix('/dev/')

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
    (fake / 'sys' / 'block').mkdir(parents=True)
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
                     'DESKTALK_SYS': str(fake / 'sys'), 'HOME': str(tmp_path), 'TMPDIR': str(temp)}
    return made


@pytest.fixture
def pi(tmp_path) -> RoomPi:
    return make_pi(tmp_path, 'desk talk')                  # a space, on purpose


def sudo_calls(result: Result) -> list[str]:
    return [call for call in result.calls if call.startswith('sudo ')]


def calls_to(result: Result, *names: str) -> list[str]:
    return [call for call in result.calls if call.split()[0] in names]


def unmount_as_after_a_bad_boot(pi: RoomPi) -> None:
    assert pi.machine('umount', str(pi.data)) == 0
    pi.clear(f'active_{UNIT}')


def a_usb_drive_at_the_data_folder(pi: RoomPi) -> None:
    (pi.fake / 'mounts.json').write_text(json.dumps([{'source': '/dev/sda1', 'target': os.path.realpath(pi.data),
                                                      'disk': str(pi.fake / 'usb')}]), encoding='utf-8')


def made_a_new_file(pi: RoomPi, result: Result) -> list[str]:
    """Any mkfs, and any fallocate but the one that sets the room's own file aside again at its own size."""
    again = f'fallocate -l {pi.image.read_text()} {pi.image}' if pi.image.is_file() else None
    return [call for call in calls_to(result, 'fallocate', 'mkfs.ext4') if call != again]


def changed_nothing(pi: RoomPi, result: Result) -> None:
    assert files_in(pi.data) == HISTORY
    assert not pi.image.exists() and not pi.new_image.exists()
    assert not pi.old_folders() and not pi.mounts() and not list(pi.temp.iterdir())
    assert not calls_to(result, 'fallocate', 'mkfs.ext4', 'mount')


def only_the_new_file_went(pi: RoomPi, result: Result, before: os.stat_result) -> None:
    """A failure in steps 1 to 5: the data folder is the same folder, untouched, and nothing else is left."""
    assert files_in(pi.data) == HISTORY and os.stat(pi.data).st_ino == before.st_ino
    assert not pi.image.exists() and not pi.new_image.exists()
    assert not pi.old_folders() and not pi.mounts() and not list(pi.temp.iterdir())
    assert not pi.unit_file.exists()


def trims_setting(pi: RoomPi) -> str:
    """What the kernel lets through to the room's file when something on the Pi trims (0: nothing)."""
    [mount] = pi.mounts()
    return (pi.loop(mount['source']) / 'queue' / 'discard_max_bytes').read_text().strip()


def a_checked_copy_left_by_a_cut_short_move(pi: RoomPi, *, set_aside: bool = True) -> Path | None:
    """A power cut part-way through a move: <file>.new is made and holds the checked copy with the marker. With
    `set_aside` the old folder was set aside too, and the data folder is the bare mount point, root's, empty."""
    pi.new_image.write_text(str(8192 * MB))
    disk = pi.fake / 'disks' / str(os.stat(pi.new_image).st_ino)
    (disk / 'lost+found').mkdir(parents=True)
    for name, content in HISTORY.items():
        (disk / name).parent.mkdir(parents=True, exist_ok=True)
        (disk / name).write_bytes(content)
    (disk / MARKER).touch()
    if not set_aside:
        return None
    old = pi.root / 'data.before-room-2026-11-11-120000'
    pi.data.rename(old)
    pi.data.mkdir()
    return old


# ── status ──

def test_status_says_leftover_for_a_move_cut_short_after_the_old_folder_was_set_aside(pi):
    a_checked_copy_left_by_a_cut_short_move(pi)
    assert pi.status() == 'leftover\n'


def test_a_move_cut_short_is_leftover_even_when_debugfs_cant_be_read(pi):
    """The old folder is only set aside after the copy is checked, so it alone says the copy is a checked one: a debugfs
    the script can't read must never turn it into a half-made file that gets deleted."""
    old = a_checked_copy_left_by_a_cut_short_move(pi)
    pi.state('debugfs_misreads')
    assert pi.status() == 'leftover\n'
    result = pi.run('make')
    assert result.code == 0, result.out
    assert pi.status() == 'room\n' and files_in(pi.data) == HISTORY
    assert old is not None and files_in(old) == HISTORY


def test_a_new_file_is_never_deleted_while_an_old_folder_is_kept_beside_it(pi):
    """debugfs misreads the copy and files were written in the data folder since: it can't tell which holds the history,
    so it changes nothing and says where each one is."""
    old = a_checked_copy_left_by_a_cut_short_move(pi)
    (pi.data / 'events.jsonl').write_bytes(b'written after the power cut\n')
    pi.state('debugfs_misreads')
    result = pi.run('make')
    assert result.code == 1
    assert pi.new_image.exists() and old is not None and files_in(old) == HISTORY
    assert str(old) in result.out and str(pi.new_image) in result.out and 'nothing was changed' in result.out
    assert not calls_to(result, 'fallocate', 'mkfs.ext4')


def test_a_checked_copy_without_an_old_folder_set_aside_is_still_none(pi):
    """The data folder wasn't touched, so it holds the history and the app may go on saving there."""
    a_checked_copy_left_by_a_cut_short_move(pi, set_aside=False)
    assert pi.status() == 'none\n'


def test_status_says_none_room_elsewhere_or_unmounted(pi):
    assert pi.status() == 'none\n'
    assert pi.run('make').code == 0
    assert pi.status() == 'room\n'
    unmount_as_after_a_bad_boot(pi)
    assert pi.status() == 'unmounted\n'
    a_usb_drive_at_the_data_folder(pi)
    assert pi.status() == 'elsewhere\n'


def test_another_file_mounted_at_the_data_folder_is_not_the_room(pi):
    assert pi.run('make').code == 0
    [mount] = pi.mounts()
    (pi.loop(mount['source']) / 'loop' / 'backing_file').write_text(f'{pi.root}/other.img\n')
    assert pi.status() == 'elsewhere\n'                    # some other disk, so it's left alone


# ── making it ──

def test_makes_a_room_of_the_set_size_and_moves_the_data_in(pi):
    before = os.stat(pi.data)
    result = pi.run('make')
    assert result.code == 0, result.out
    assert pi.image.read_text() == str(8192 * MB)
    assert f'fallocate -l 8192M {pi.new_image}' in result.calls
    assert f'mkfs.ext4 -q -F -m 0 -E nodiscard,root_owner=1000:1001 {pi.new_image}' in result.calls
    [mount] = pi.mounts()
    assert mount['target'] == os.path.realpath(pi.data) and mount['options'] == 'loop,noatime'
    assert files_in(pi.data) == HISTORY                     # in the room now, -wal and -shm too
    assert (pi.data / MARKER).exists()
    [old] = pi.old_folders()
    assert files_in(old) == HISTORY and os.stat(old).st_ino == before.st_ino      # the old folder itself, renamed
    assert f'mv -T -- {pi.data} {old}' in result.calls and f'mv -T -- {pi.new_image} {pi.image}' in result.calls
    assert pi.unit_file.exists() and (pi.fake / f'enabled_{UNIT}').exists()
    assert f'sudo systemctl enable {UNIT}' in result.calls and f'sudo systemctl start {UNIT}' in result.calls
    assert f'sudo mkdir -m 755 -- {pi.data}' in result.calls
    assert str(old) in result.out and 'rm -rf' in result.out
    assert not pi.new_image.exists() and not list(pi.temp.iterdir())


def test_a_card_too_small_gets_a_smaller_room(pi):
    pi.state('free_mb', '5000')                             # 5000 - 2000 kept free for updates = 3000
    result = pi.run('make')
    assert result.code == 0, result.out
    assert f'fallocate -l 3000M {pi.new_image}' in result.calls
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


def test_when_df_fails_it_stops_and_changes_nothing(pi):
    pi.state('df_fails')
    result = pi.run('make')
    assert result.code == 1
    assert "df couldn't say how much space is free" in result.out
    changed_nothing(pi, result)


def test_a_sparse_or_short_image_is_refused(pi):
    before = os.stat(pi.data)
    pi.state('short_blocks')
    result = pi.run('make')
    assert result.code == 1
    assert 'set aside' in result.out
    assert not calls_to(result, 'mkfs.ext4', 'mount') and sudo_calls(result) == [f'sudo du -sm -- {pi.data}']
    only_the_new_file_went(pi, result, before)


@pytest.mark.parametrize('when', ['short_after_mkfs', 'short_after_umount'])
def test_space_given_back_after_the_first_check_is_caught_before_the_swap(pi, when):
    """mkfs.ext4 hands a file's space back to the card unless it's told nodiscard; anything else could, too."""
    before = os.stat(pi.data)
    pi.state(when)
    result = pi.run('make')
    assert result.code == 1
    assert 'set aside' in result.out
    only_the_new_file_went(pi, result, before)


def test_a_failed_copy_check_leaves_the_data_folder_as_it_was(pi):
    before = os.stat(pi.data)
    pi.state('copy_differs')
    result = pi.run('make')
    assert result.code == 1
    only_the_new_file_went(pi, result, before)


def test_a_copy_check_that_ran_into_trouble_is_not_trusted(pi):
    """diff answers 2 for trouble: even if all it printed was the lost+found line, the copy isn't checked."""
    before = os.stat(pi.data)
    pi.state('diff_code', '2')
    result = pi.run('make')
    assert result.code == 1
    only_the_new_file_went(pi, result, before)


def test_a_failed_first_mount_puts_the_old_folder_back(pi):
    before = os.stat(pi.data)
    pi.state('e2fsck_code', '8')
    result = pi.run('make')
    assert result.code == 1
    assert 'journalctl -u desktalk-data-room' in result.out and 'is back where it was' in result.out
    assert files_in(pi.data) == HISTORY and os.stat(pi.data).st_ino == before.st_ino
    assert not pi.old_folders() and not pi.mounts()
    assert not pi.image.exists() and not pi.new_image.exists()
    assert not (pi.fake / f'enabled_{UNIT}').exists() and not (pi.fake / f'active_{UNIT}').exists()
    assert f'sudo systemctl disable {UNIT}' in result.calls


@pytest.mark.parametrize('failing', ['mkdir', 'install'])
def test_a_failure_part_way_through_the_swap_puts_the_old_folder_back(pi, failing):
    """mkdir fails right after the old folder is set aside; install once the new file has its name."""
    before = os.stat(pi.data)
    pi.state(f'sudo_fails_for_{failing}')
    result = pi.run('make')
    assert result.code == 1
    assert files_in(pi.data) == HISTORY and os.stat(pi.data).st_ino == before.st_ino
    assert not pi.old_folders() and not pi.mounts()
    assert not pi.image.exists() and not pi.new_image.exists()
    assert 'is back at' in result.out


def test_a_put_back_that_cant_unmount_removes_nothing_and_says_so(pi):
    pi.state('marker_vanishes')                             # mounted, but it doesn't look like the room ...
    pi.state('umount_refuses')                              # ... and it can't be unmounted again
    result = pi.run('make')
    assert result.code == 1
    [old] = pi.old_folders()
    assert files_in(old) == HISTORY and pi.image.exists() and pi.mounts()
    assert "couldn't be put back by itself" in result.out and str(pi.image) in result.out
    assert 'is back' not in result.out and "it's removed" not in result.out
    assert result.out.strip().splitlines()[-1].startswith('Stopping:')
    assert "couldn't be put back" in result.out.strip().splitlines()[-1]


# ── a room that's there but not mounted ──

def test_an_existing_image_is_never_made_again(pi):
    assert pi.run('make').code == 0
    unmount_as_after_a_bad_boot(pi)
    room = pi.image.read_text()
    inode = os.stat(pi.image).st_ino
    pi.state('e2fsck_code', '4')                            # damage the safe repair can't fix
    result = pi.run('make')
    assert result.code == 1
    assert "isn't mounted" in result.out and 'journalctl -u desktalk-data-room' in result.out
    assert not made_a_new_file(pi, result)
    assert pi.image.read_text() == room and os.stat(pi.image).st_ino == inode
    assert not pi.mounts() and pi.status() == 'unmounted\n'

    pi.state('e2fsck_code', '1')                            # the safe repair fixed it
    result = pi.run('make')
    assert result.code == 0, result.out
    assert pi.status() == 'room\n' and files_in(pi.data) == HISTORY
    assert not made_a_new_file(pi, result)


def test_a_mounted_room_missing_its_marker_is_not_moved_out_of(pi):
    """The room's own files are in the data folder: telling the person to move them out would split the history."""
    assert pi.run('make').code == 0
    [mount] = pi.mounts()
    (pi.data / MARKER).unlink()
    assert pi.status() == 'unmounted\n'
    for args in (('make',), ('make', '--dry-run')):
        result = pi.run(*args)
        assert result.code == 1
        assert f'{MARKER} is missing' in result.out and 'touch ' in result.out
        assert "Don't move" in result.out and 'ls -A' in result.out and 'Move them' not in result.out
        assert pi.mounts() == [mount] and files_in(pi.data) == HISTORY
        assert not sudo_calls(result) and not made_a_new_file(pi, result)


def test_the_unit_sets_the_rooms_space_aside_again_before_it_mounts_it(pi):
    assert pi.run('make').code == 0
    unmount_as_after_a_bad_boot(pi)
    pi.state('lost_blocks')                                 # a trim gave some back while it wasn't mounted
    pi.state('calls', '')
    assert pi.machine('systemctl', 'start', UNIT) == 0
    calls = (pi.fake / 'calls').read_text(encoding='utf-8').splitlines()
    assert calls.index(f'fallocate -l {8192 * MB} {pi.image}') < calls.index(f'e2fsck -p {pi.image}')
    assert not (pi.fake / 'lost_blocks').exists() and pi.status() == 'room\n'


def test_the_room_is_never_mounted_over_files_in_the_data_folder(pi):
    assert pi.run('make').code == 0
    unmount_as_after_a_bad_boot(pi)
    (pi.data / 'events.jsonl').write_bytes(b'written while the room was missing\n')
    for args in (('make',), ('make', '--dry-run')):
        result = pi.run(*args)
        assert result.code == 1
        assert "isn't empty" in result.out and 'events.jsonl' in result.out and 'nothing was changed' in result.out
        assert not pi.mounts() and not sudo_calls(result)
        assert files_in(pi.data) == {'events.jsonl': b'written while the room was missing\n'}
    assert pi.status() == 'unmounted\n'


def test_the_unit_never_mounts_over_files_in_the_data_folder(pi):
    """At boot too: the service checks for itself."""
    assert pi.run('make').code == 0
    unmount_as_after_a_bad_boot(pi)
    (pi.data / 'events.jsonl').write_bytes(b'newer\n')
    assert pi.machine('systemctl', 'start', UNIT) != 0
    assert not pi.mounts() and files_in(pi.data) == {'events.jsonl': b'newer\n'}
    (pi.data / 'events.jsonl').unlink()
    assert pi.machine('systemctl', 'start', UNIT) == 0
    assert pi.status() == 'room\n' and files_in(pi.data) == HISTORY


# ── a room that's there ──

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
    assert not sudo_calls(result) and not calls_to(result, 'fallocate')

    pi.change_example('DATA_ROOM_SIZE_MB', '10000')
    result = pi.run('make')                                 # the same size: nothing to do
    assert result.code == 0
    assert not sudo_calls(result) and not calls_to(result, 'fallocate')


@pytest.mark.parametrize(('free', 'grows'), [(3808, True), (3807, False)])
def test_a_grow_needs_only_the_space_the_card_isnt_holding_yet(pi, free, grows):
    """8192 MB held, 10000 wanted: 1808 MB more, with 2000 kept free for updates."""
    assert pi.run('make').code == 0
    pi.change_example('DATA_ROOM_SIZE_MB', '10000')
    pi.state('free_mb', str(free))
    result = pi.run('make')
    assert result.code == 0, result.out
    assert pi.image.read_text() == str((10000 if grows else 8192) * MB)
    if not grows:
        assert 'takes 1808 MB' in result.out and 'Free 1 MB' in result.out


@pytest.mark.parametrize('failing', ['sudo_fails', 'resize2fs_fails'])
def test_a_grow_that_stopped_part_way_is_finished_next_time(pi, failing):
    assert pi.run('make').code == 0
    [mount] = pi.mounts()
    pi.change_example('DATA_ROOM_SIZE_MB', '10000')
    pi.state(failing)
    result = pi.run('make')
    assert result.code == 1
    assert f'sudo losetup -c {mount["source"]} && sudo resize2fs {mount["source"]}' in result.out
    pi.clear(failing)
    result = pi.run('make')
    assert result.code == 0, result.out
    assert f'sudo resize2fs {mount["source"]}' in result.calls and not calls_to(result, 'fallocate')
    assert (pi.loop(mount['source']) / 'size').read_text().strip() == str(10000 * MB // 512)
    result = pi.run('make')                                 # finished: nothing more to do, and no sudo
    assert result.code == 0 and not sudo_calls(result)


def test_a_grow_that_cant_read_how_much_is_held_stops_plainly(pi):
    assert pi.run('make').code == 0
    pi.change_example('DATA_ROOM_SIZE_MB', '10000')
    pi.state('blocks_answers', '1')                         # the hold check gets its answer; the grow's doesn't
    result = pi.run('make')
    assert result.code == 1
    assert "couldn't tell how much of Desk Talk's room" in result.out
    assert pi.image.read_text() == str(8192 * MB) and not calls_to(result, 'fallocate')


def test_a_room_whose_space_is_no_longer_all_held_gets_it_back(pi):
    assert pi.run('make').code == 0
    pi.state('lost_blocks')
    result = pi.run('make')
    assert result.code == 0, result.out
    assert f'fallocate -l {8192 * MB} {pi.image}' in result.calls and 'Set aside again' in result.out
    assert not (pi.fake / 'lost_blocks').exists() and not sudo_calls(result)

    pi.state('lost_blocks')
    pi.state('stays_short')                                 # the card is too full to give it back
    result = pi.run('make')
    assert result.code == 0
    assert 'IMPORTANT' in result.out and 'Free some space' in result.out


def test_a_data_folder_on_another_disk_is_left_alone(pi):
    a_usb_drive_at_the_data_folder(pi)
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

    pi.state('lost_blocks')
    result = pi.run('make', '--dry-run')
    assert result.code == 0 and 'Would set aside again' in result.out
    assert not calls_to(result, 'fallocate') and (pi.fake / 'lost_blocks').exists()
    pi.clear('lost_blocks')

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
    backing = (pi.loop(mount['source']) / 'loop' / 'backing_file').read_text().strip()
    assert backing == os.path.realpath(pi.image)
    assert files_in(pi.data) == HISTORY and pi.status() == 'room\n'
    [old] = pi.old_folders()
    assert files_in(old) == HISTORY


def test_a_move_cut_short_by_a_power_cut_is_not_started_again(pi):
    """Cut between putting the old folder aside and naming the new file, with a copy debugfs can't read: the move is
    finished, not started again, and when the copy won't mount, it stops with the history safe in the old folder."""
    old = pi.root / 'data.before-room-2026-11-11-120000'
    pi.data.rename(old)
    pi.data.mkdir()
    pi.new_image.write_text(str(8192 * MB))
    result = pi.run('make')
    assert result.code == 1
    assert str(old) in result.out and 'Nothing was deleted' in result.out
    assert files_in(old) == HISTORY and (pi.new_image.exists() or pi.image.exists())
    assert not calls_to(result, 'mkfs.ext4')                # never made again (the unit only re-holds its space)


def test_a_leftover_new_file_from_a_stopped_try_is_made_again(pi):
    pi.new_image.write_text('half made')
    result = pi.run('make')
    assert result.code == 0, result.out
    assert pi.status() == 'room\n' and files_in(pi.data) == HISTORY


# ── trims ──

def test_the_unit_switches_trims_off_for_the_room_so_they_cant_punch_holes_in_it(pi):
    result = pi.run('make')
    assert result.code == 0, result.out
    assert trims_setting(pi) == '0'
    unmount_as_after_a_bad_boot(pi)
    assert pi.machine('systemctl', 'start', UNIT) == 0         # at boot too
    assert trims_setting(pi) == '0' and pi.status() == 'room\n'


def test_trims_that_cant_be_switched_off_still_mount_the_room_and_say_so(pi):
    pi.state('discard_stuck')
    result = pi.run('make')
    assert result.code == 0, result.out
    assert pi.status() == 'room\n' and files_in(pi.data) == HISTORY
    # The unit's own line goes to the journal; this one is data_room.sh's, which install.sh keeps for its notes.
    [line] = [line for line in result.out.splitlines() if line.startswith('IMPORTANT: ') and 'by hand' in line]
    assert 'trims' in line and 'set aside again' in line


def test_a_room_mounted_with_trims_on_gets_them_switched_off(pi):
    """Mounted by an older unit, which left trims on."""
    assert pi.run('make').code == 0
    [mount] = pi.mounts()
    (pi.loop(mount['source']) / 'queue' / 'discard_max_bytes').write_text('4294966784\n')
    result = pi.run('make')
    assert result.code == 0, result.out
    assert trims_setting(pi) == '0' and any(call.startswith('sudo tee ') for call in result.calls)
    result = pi.run('make')                                 # off now: no sudo
    assert result.code == 0 and not sudo_calls(result)


def test_the_new_file_has_trims_off_while_the_history_is_copied_in(pi):
    result = pi.run('make')
    assert result.code == 0, result.out
    tees = [call for call in result.calls if call.startswith('sudo tee ')]
    assert len(tees) == 1 and tees[0].endswith('/queue/discard_max_bytes')
    assert result.calls.index(tees[0]) < next(i for i, call in enumerate(result.calls) if call.startswith('sudo cp '))


# ── without sudo (update.sh) ──

def test_without_sudo_a_grow_is_left_for_later_and_says_how(pi):
    assert pi.run('make').code == 0
    pi.change_example('DATA_ROOM_SIZE_MB', '10000')
    result = pi.run('make', '--no-sudo')
    assert result.code == 0, result.out
    assert not sudo_calls(result) and not calls_to(result, 'fallocate')
    assert pi.image.read_text() == str(8192 * MB)
    [line] = [line for line in result.out.splitlines() if line.startswith("Desk Talk's room needs sudo to ")]
    assert 'grow' in line and '10000' in line and 'install.sh' in line


def test_without_sudo_a_grow_that_stopped_part_way_is_left_for_later(pi):
    assert pi.run('make').code == 0
    pi.change_example('DATA_ROOM_SIZE_MB', '10000')
    pi.state('resize2fs_fails')
    assert pi.run('make').code == 1
    pi.clear('resize2fs_fails')
    result = pi.run('make', '--no-sudo')
    assert result.code == 0, result.out
    assert not sudo_calls(result)
    [line] = [line for line in result.out.splitlines() if line.startswith("Desk Talk's room needs sudo to ")]
    assert 'finish growing' in line and 'install.sh' in line


def test_without_sudo_trims_left_on_say_so(pi):
    assert pi.run('make').code == 0
    [mount] = pi.mounts()
    (pi.loop(mount['source']) / 'queue' / 'discard_max_bytes').write_text('4294966784\n')
    result = pi.run('make', '--no-sudo')
    assert result.code == 0 and not sudo_calls(result)
    assert any(line.startswith('IMPORTANT: ') and 'trim' in line and 'install.sh' in line
               for line in result.out.splitlines())


def test_without_sudo_a_room_still_to_make_stops_and_changes_nothing(pi):
    result = pi.run('make', '--no-sudo')
    assert result.code == 1 and 'sudo' in result.out
    changed_nothing(pi, result)
    assert not sudo_calls(result)


# ── how much the data folder holds ──

def test_the_data_folder_is_measured_with_sudo_so_roots_files_count(pi):
    result = pi.run('make')
    assert result.code == 0, result.out
    assert f'sudo du -sm -- {pi.data}' in result.calls


def test_a_measure_with_no_number_stops_plainly_and_changes_nothing(pi):
    pi.state('du_says_nothing')
    result = pi.run('make')
    assert result.code == 1
    assert "couldn't tell how much the data folder" in result.out and 'integer' not in result.out
    changed_nothing(pi, result)


# ── a move cut short ──

def test_a_move_cut_short_after_the_old_folder_was_set_aside_is_finished(pi):
    old = a_checked_copy_left_by_a_cut_short_move(pi)
    result = pi.run('make')
    assert result.code == 0, result.out
    assert pi.status() == 'room\n' and files_in(pi.data) == HISTORY
    assert old is not None and files_in(old) == HISTORY and pi.old_folders() == [old]
    assert not pi.new_image.exists() and not made_a_new_file(pi, result)
    assert str(old) in result.out and trims_setting(pi) == '0'


def test_a_move_cut_short_never_deletes_the_checked_copy_when_files_were_written_since(pi):
    """The review's case: the app wrote a new history into the bare mount point after the power cut."""
    old = a_checked_copy_left_by_a_cut_short_move(pi)
    (pi.data / 'events.jsonl').write_bytes(b'written after the power cut\n')
    inode = os.stat(pi.new_image).st_ino
    for args in (('make',), ('make', '--dry-run')):
        result = pi.run(*args)
        assert result.code == 1
        assert str(old) in result.out and str(pi.new_image) in result.out and 'events.jsonl' in result.out
        assert 'nothing was changed' in result.out
        assert pi.new_image.exists() and os.stat(pi.new_image).st_ino == inode
        assert files_in(pi.fake / 'disks' / str(inode)) == HISTORY
        assert old is not None and files_in(old) == HISTORY
        assert files_in(pi.data) == {'events.jsonl': b'written after the power cut\n'}
        assert not sudo_calls(result) and not made_a_new_file(pi, result) and not pi.image.exists()


def test_a_checked_copy_from_before_the_old_folder_was_set_aside_is_never_deleted(pi):
    a_checked_copy_left_by_a_cut_short_move(pi, set_aside=False)
    inode = os.stat(pi.new_image).st_ino
    result = pi.run('make')
    assert result.code == 1
    assert str(pi.new_image) in result.out and 'rm -f' in result.out and 'nothing was changed' in result.out
    assert pi.new_image.exists() and os.stat(pi.new_image).st_ino == inode
    assert files_in(pi.data) == HISTORY and not pi.old_folders()
    assert not sudo_calls(result) and not made_a_new_file(pi, result)


def test_a_dry_run_of_a_move_cut_short_says_it_would_finish_it(pi):
    a_checked_copy_left_by_a_cut_short_move(pi)
    result = pi.run('make', '--dry-run')
    assert result.code == 0, result.out
    assert 'Would finish' in result.out
    assert not sudo_calls(result) and pi.status() == 'leftover\n'


# ── the settings and the unit ──

@pytest.mark.parametrize(('name', 'value', 'says'), [
    ('DATA_ROOM_FILE', '', 'DATA_ROOM_FILE in raspberry_pi/.env is empty'),
    ('DESKTALK_DATA', '', 'DESKTALK_DATA in raspberry_pi/.env is empty'),
    ('DATA_ROOM_SIZE_MB', '512', 'smaller than DATA_ROOM_SMALLEST_MB'),
    ('DATA_ROOM_SIZE_MB', 'lots', "isn't a whole number"),
])
def test_bad_settings_stop_it_with_a_plain_message(pi, name, value, says):
    pi.change_example(name, value)
    result = pi.run('make')
    assert result.code == 1 and says in result.out
    changed_nothing(pi, result)


def test_a_missing_setting_stops_even_status(pi):
    text = pi.example.read_text(encoding='utf-8')
    pi.example.write_text(re.sub(r'^DATA_ROOM_FILE=.*\n', '', text, flags=re.M), encoding='utf-8')
    result = pi.run('status')
    assert result.code == 1 and 'DATA_ROOM_FILE is missing' in result.out


def test_a_room_file_that_isnt_a_plain_file_is_refused(pi):
    pi.image.mkdir()
    result = pi.run('make')
    assert result.code == 1 and "isn't a plain file" in result.out
    assert files_in(pi.data) == HISTORY and not calls_to(result, 'fallocate', 'mkfs.ext4', 'sudo')


def test_the_unit_repairs_only_safely_and_mounts_before_docker():
    text = (PI / 'systemd' / UNIT).read_text(encoding='utf-8')
    lines = [line for line in text.splitlines() if line and not line.startswith('#')]
    assert 'Type=oneshot' in lines and 'RemainAfterExit=yes' in lines
    assert 'Before=docker.service' in lines and 'WantedBy=multi-user.target' in lines
    assert not any(line.startswith(('Requires=', 'RequiredBy=')) for line in lines)   # Docker starts without it
    start = next(line for line in lines if line.startswith('ExecStart='))
    stop = next(line for line in lines if line.startswith('ExecStop='))
    assert 'e2fsck -p ' in start and ' -y' not in '\n'.join(lines) and '-ge 4' in start
    assert 'ls -A "$$2"' in start.split('e2fsck')[0]                                   # empty folder, checked first
    hold = 'fallocate -l "$$(stat -c %%s "$$1")" "$$1"'
    assert hold in start and start.index(hold) < start.index('e2fsck -p') < start.index('mount -o loop')
    assert '%' not in start.replace('%%', '')                                          # systemd would fill in a lone %
    assert 'mount -o loop,noatime ' in start and 'umount ' in stop
    assert start.index('mount -o loop') < start.index('discard_max_bytes')             # trims off, once it's mounted
    assert start.endswith(' "@IMAGE@" "@DATA@" "@SYS@"') and stop.endswith(' "@DATA@"')   # each path one word
    assert '@' not in start.removesuffix(' "@IMAGE@" "@DATA@" "@SYS@"')                 # never inside the script
    assert not re.search(r'(?<!\$)\$(?!\$)', start.replace('$$', ''))                # systemd would fill in a lone $


@pytest.mark.parametrize('folder', ['desk talk', 'my 100% "desk" $HOME\\talk'])
def test_unit_text_is_the_unit_it_installs_and_changes_nothing(tmp_path, folder):
    """install.sh compares it with the installed unit, to put a newer one from git in place once the room is there."""
    pi = make_pi(tmp_path, folder)
    before = pi.run('unit-text')
    assert before.code == 0, before.out
    changed_nothing(pi, before)
    assert not sudo_calls(before) and not pi.unit_file.exists()
    assert pi.run('make').code == 0
    assert pi.run('unit-text').out == before.out == pi.unit_file.read_text(encoding='utf-8')


def test_where_the_old_folder_went_starts_the_way_install_sh_looks_for(pi):
    """install.sh keeps this line, and any line starting IMPORTANT:, for its closing notes."""
    result = pi.run('make')
    [old] = pi.old_folders()
    [line] = [line for line in result.out.splitlines() if line.startswith('The old data folder is kept at ')]
    assert str(old) in line and 'rm -rf' in line


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
