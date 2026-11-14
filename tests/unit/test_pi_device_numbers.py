"""raspberry_pi/device_numbers.sh, run for real on a made-up Pi: the script next to a .env and .env.example in a
temporary folder, and a made-up /proc/devices. The kernel numbers the camera's kinds of device (media, dma_heap) at
each boot in the order their drivers load, so any boot can change them; desktalk.service runs this before it starts
the containers, so the camera container may open the numbers this boot gave."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests.unit.test_pi_compose_file import env_file

ROOT = Path(__file__).resolve().parents[2]
PI = ROOT / 'raspberry_pi'
SCRIPT = 'device_numbers.sh'
DEVICES = 'Character devices:\n  1 mem\n 81 video4linux\n116 alsa\n{dma}{media}\nBlock devices:\n  8 media\n'


def devices_text(*, media: str | None = '237', dma: str | None = '253') -> str:
    return DEVICES.format(media=f'{media} media\n' if media else '', dma=f'{dma} dma_heap\n' if dma else '')


@dataclass
class Result:
    code: int
    out: str


@dataclass
class FakePi:
    folder: Path
    proc: Path
    elsewhere: Path
    fake_bin: Path

    @property
    def env_path(self) -> Path:
        return self.folder / '.env'

    def set_env(self, name: str, value: str) -> None:
        text = self.env_path.read_text(encoding='utf-8')
        self.env_path.write_text(re.sub(rf'^{name}=.*$', f'{name}={value}', text, flags=re.M), encoding='utf-8')

    def devices(self, text: str) -> None:
        """Swapped in whole, as the kernel's file never shows half a list."""
        new = self.proc / 'devices.new'
        new.write_text(text, encoding='utf-8')
        new.replace(self.proc / 'devices')

    def fake_command(self, name: str, body: str) -> None:
        """A command of this name, found before the real one."""
        command = self.fake_bin / name
        command.write_text(f'#!/bin/bash\n{body}\n', encoding='utf-8')
        command.chmod(0o755)

    def environment(self) -> dict[str, str]:
        return {'PATH': f'{self.fake_bin}:/usr/bin:/bin', 'DESKTALK_PROC': str(self.proc),
                'HOME': str(self.elsewhere)}

    def start(self) -> subprocess.Popen[str]:
        return subprocess.Popen(['/bin/bash', str(self.folder / SCRIPT)], cwd=self.elsewhere, env=self.environment(),
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    def run(self) -> Result:
        done = subprocess.run(['/bin/bash', str(self.folder / SCRIPT)], cwd=self.elsewhere, env=self.environment(),
                              capture_output=True, text=True, timeout=30, check=False)
        return Result(done.returncode, done.stdout + done.stderr)

    def values(self) -> dict[str, str]:
        return env_file(self.env_path)


@pytest.fixture
def pi(tmp_path) -> FakePi:
    folder, proc, elsewhere = tmp_path / 'desk talk' / 'raspberry_pi', tmp_path / 'proc', tmp_path / 'home'  # a space
    fake_bin = tmp_path / 'bin'
    for made in (folder, proc, elsewhere, fake_bin):
        made.mkdir(parents=True)
    shutil.copy(PI / SCRIPT, folder / SCRIPT)
    shutil.copy(PI / '.env.example', folder / '.env.example')
    shutil.copy(PI / '.env.example', folder / '.env')
    (folder / '.env').chmod(0o644)
    made_pi = FakePi(folder, proc, elsewhere, fake_bin)
    made_pi.set_env('CAMERA_MEDIA_MAJOR', '510')
    made_pi.set_env('CAMERA_DMA_HEAP_MAJOR', '253')
    made_pi.set_env('DEVICE_NUMBERS_WAIT_SEC', '0.2')          # the tests can't wait half a minute
    made_pi.set_env('DEVICE_NUMBERS_CHECK_EVERY_SEC', '0.05')
    made_pi.devices(devices_text())
    return made_pi


def test_the_script_stops_on_errors_and_can_be_run():
    assert 'set -euo pipefail' in (PI / SCRIPT).read_text(encoding='utf-8')
    assert os.access(PI / SCRIPT, os.X_OK)


def test_the_example_has_the_waits_it_reads():
    values = env_file(PI / '.env.example')
    assert float(values['DEVICE_NUMBERS_WAIT_SEC']) > 0 and float(values['DEVICE_NUMBERS_CHECK_EVERY_SEC']) > 0


def test_a_number_this_boot_changed_is_written_and_said(pi):
    result = pi.run()
    assert result.code == 0, result.out
    assert pi.values()['CAMERA_MEDIA_MAJOR'] == '237'
    assert 'CAMERA_MEDIA_MAJOR: 510 → 237' in result.out
    assert pi.env_path.stat().st_mode & 0o777 == 0o644
    assert {path.name for path in pi.folder.iterdir()} == {SCRIPT, '.env', '.env.example'}    # no temporary file left


def test_an_unchanged_number_leaves_the_file_untouched(pi):
    pi.set_env('CAMERA_MEDIA_MAJOR', '237')
    before = pi.env_path.stat()
    result = pi.run()
    assert result.code == 0, result.out
    after = pi.env_path.stat()
    assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)
    assert 'CAMERA_MEDIA_MAJOR: 237' in result.out and 'unchanged' in result.out
    assert 'CAMERA_DMA_HEAP_MAJOR: 253' in result.out


def test_a_block_device_with_the_same_name_is_not_taken(pi):
    pi.devices(devices_text(media=None))
    result = pi.run()
    assert result.code == 0, result.out
    assert pi.values()['CAMERA_MEDIA_MAJOR'] == '510'


def test_a_kind_not_listed_is_waited_for_then_kept_and_said(pi):
    pi.devices(devices_text(media=None))
    started = time.monotonic()
    result = pi.run()
    waited = time.monotonic() - started
    assert result.code == 0, result.out
    assert pi.values()['CAMERA_MEDIA_MAJOR'] == '510'
    assert 'CAMERA_MEDIA_MAJOR' in result.out and 'media' in result.out and 'kept' in result.out
    assert waited >= 0.2


def test_a_kind_that_shows_up_during_the_wait_is_used(pi):
    pi.set_env('DEVICE_NUMBERS_WAIT_SEC', '20')
    pi.devices(devices_text(media=None))
    running = pi.start()
    time.sleep(0.3)
    assert running.poll() is None                           # still waiting for the media driver
    pi.devices(devices_text())
    out, _ = running.communicate(timeout=20)
    assert running.returncode == 0, out
    assert pi.values()['CAMERA_MEDIA_MAJOR'] == '237'
    assert 'CAMERA_MEDIA_MAJOR: 510 → 237' in out


def test_a_name_missing_from_env_is_added(pi):
    text = pi.env_path.read_text(encoding='utf-8')
    pi.env_path.write_text(re.sub(r'^CAMERA_DMA_HEAP_MAJOR=.*\n', '', text, flags=re.M).rstrip('\n'),
                           encoding='utf-8')                # and no newline at the end, either
    result = pi.run()
    assert result.code == 0, result.out
    values = pi.values()
    assert values['CAMERA_DMA_HEAP_MAJOR'] == '253' and values['CAMERA_MEDIA_MAJOR'] == '237'
    assert pi.env_path.read_text(encoding='utf-8').count('CAMERA_DMA_HEAP_MAJOR=') == 1
    assert 'CAMERA_DMA_HEAP_MAJOR: 253 (added to .env)' in result.out


def test_a_name_in_env_with_no_number_is_filled_in_not_added(pi):
    pi.set_env('CAMERA_MEDIA_MAJOR', '')
    result = pi.run()
    assert result.code == 0, result.out
    assert pi.values()['CAMERA_MEDIA_MAJOR'] == '237'
    assert 'CAMERA_MEDIA_MAJOR: 237 (filled in' in result.out
    assert 'added' not in result.out
    assert pi.env_path.read_text(encoding='utf-8').count('CAMERA_MEDIA_MAJOR=') == 1


def test_a_name_in_env_with_no_number_that_is_not_listed_says_it_is_empty(pi):
    pi.set_env('CAMERA_MEDIA_MAJOR', '')
    pi.devices(devices_text(media=None))
    result = pi.run()
    assert result.code == 0, result.out
    assert 'CAMERA_MEDIA_MAJOR: kept (empty in .env)' in result.out and 'not in .env' not in result.out


def test_a_stop_part_way_leaves_env_as_it_was_and_no_temporary_file(pi):
    """mv stands in for anything that fails between making the new .env and swapping it in."""
    before = pi.env_path.read_text(encoding='utf-8')
    pi.fake_command('mv', 'exit 1')
    result = pi.run()
    assert result.code != 0, result.out
    assert pi.env_path.read_text(encoding='utf-8') == before
    assert {path.name for path in pi.folder.iterdir()} == {SCRIPT, '.env', '.env.example'}


def test_the_folder_is_synced_after_the_new_env_takes_its_place(pi):
    """So the rename itself is on the card before a power cut."""
    log = pi.elsewhere / 'synced'
    written = f'$(grep -c "^CAMERA_MEDIA_MAJOR=237$" "{pi.env_path}")'     # 1 once the new .env is in place
    pi.fake_command('sync', f'printf "%s %s\\n" "$1" "{written}" >> "{log}"')
    result = pi.run()
    assert result.code == 0, result.out
    assert log.read_text(encoding='utf-8').splitlines()[-1] == f'{pi.folder} 1'


def test_no_env_says_so_and_exits_0(pi):
    pi.env_path.unlink()
    result = pi.run()
    assert result.code == 0, result.out
    assert '.env' in result.out
    assert not pi.env_path.exists()


def test_the_rest_of_env_is_kept_as_it_was(pi):
    pi.set_env('DETECTOR_MEMORY_LIMIT', '1200m')
    with pi.env_path.open('a', encoding='utf-8') as env:
        env.write('\n# my own note\nMY_OWN=a=b c\n')
    before = pi.env_path.read_text(encoding='utf-8')
    assert pi.run().code == 0
    after = pi.env_path.read_text(encoding='utf-8')
    assert after == before.replace('CAMERA_MEDIA_MAJOR=510', 'CAMERA_MEDIA_MAJOR=237')
