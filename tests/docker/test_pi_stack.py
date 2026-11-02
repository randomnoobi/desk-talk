"""
Starts the Pi's three containers (raspberry_pi/compose.yaml) on this computer, with the camera playing pictures from
a folder, and checks them from the outside.

Opt-in: it needs Docker and the internet, and takes several minutes the first time.

    uv run pytest -m docker tests/docker/test_pi_stack.py

It has its own compose project name, app port, data folder and time zone, so it can't touch a stack you already run.
Its containers and volumes are removed at the end. The detector has no model here (none is on this computer), so the
pictures reach the app with no detections. The real picture flow is checked on the real Pi.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import cv2
import httpx
import numpy as np
import pytest

from tests.unit.test_pi_compose_file import env_file

pytestmark = pytest.mark.docker

ROOT = Path(__file__).resolve().parents[2]
PI = ROOT / 'raspberry_pi'
PROJECT = 'desktalk-pi-test'
BUILD_AND_START_SEC = 3600       # the first build downloads PyTorch and friends
ARRIVE_SEC = 60                  # pictures reaching the app once all three are healthy
TIME_ZONE = 'America/New_York'
TEST_MOMENT = 1_700_000_000      # 2023-11-14 22:13:20 UTC, 17:13 in New York
HOUR_THERE = 17
QUICK_HEALTH = '3s'              # so the test doesn't wait for the Pi's slower health checks
LOCAL_HOUR = ('from desktalk.runtime import SystemClock; '
              f'print(SystemClock().local_hour({TEST_MOMENT}))')
ANALYTICS = ('from ultralytics.utils import ONLINE, SETTINGS; from ultralytics.utils.events import events; '
             "print(ONLINE, SETTINGS['sync'], events.enabled)")
HANG = ('import os, signal; [os.kill(int(p), signal.SIGSTOP) for p in os.listdir("/proc") if p.isdigit() '
        'and open(f"/proc/{p}/stat").read().rsplit(")", 1)[1].split()[1] == "1"]')
BACK_SEC = 120                   # a hung program noticed, ended, started again and healthy
ASK_THE_DETECTOR = ('import json, urllib.request; print(json.load(urllib.request.urlopen('
                    "'http://detector:' + __import__('os').environ['ASK_PORT'] + '/health'))['state'])")


@dataclass
class Stack:
    url: str
    data: Path
    env: dict[str, str]

    def compose(self, *args: str, timeout: float = 300, check: bool = True) -> subprocess.CompletedProcess[str]:
        command = ['docker', 'compose', '-p', PROJECT, '-f', str(PI / 'compose.yaml'),
                   '-f', str(ROOT / 'tests' / 'docker' / 'pi_folder_camera.compose.yaml'), *args]
        done = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False, env=self.env)
        if check and done.returncode != 0:
            raise AssertionError(f'docker compose {" ".join(args)} failed:\n{done.stdout}\n{done.stderr}')
        return done


def free_port() -> int:
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def some_pictures(folder: Path) -> None:
    for number in range(3):
        picture = np.full((120, 160, 3), 60 * number, np.uint8)
        (folder / f'{number}.jpg').write_bytes(cv2.imencode('.jpg', picture)[1].tobytes())


@pytest.fixture(scope='module')
def stack(request) -> Iterator[Stack]:
    if shutil.which('docker') is None:
        pytest.skip("Docker isn't installed")
    marks = request.config.getoption('markexpr') or ''
    if 'docker' not in marks or 'not docker' in marks:
        pytest.skip('opt-in: uv run pytest -m docker tests/docker/test_pi_stack.py')
    if subprocess.run(['docker', 'info'], capture_output=True, check=False).returncode != 0:
        pytest.skip("Docker isn't running")
    # In the repo, not the system's temporary folder: Docker on a Mac can only see folders under your home folder.
    cache = ROOT / '.pytest_cache'
    cache.mkdir(exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='pi-stack-', dir=cache))
    pictures = work / 'pictures'
    pictures.mkdir()
    some_pictures(pictures)
    no_sound, no_cards = work / 'no-sound', work / 'no-cards'     # a computer's Docker has no sound card to share
    no_sound.mkdir()
    no_cards.write_text('--- no soundcards ---\n', encoding='utf-8')
    port = free_port()
    env = {**os.environ, 'TZ': TIME_ZONE, 'APP_HOST_PORT': str(port), 'DESKTALK_DATA': str(work / 'data'),
           'PI_TEST_PICTURES': str(pictures), 'SOUND_DEVICES': str(no_sound), 'SOUND_CARDS_FILE': str(no_cards),
           'APP_HEALTH_INTERVAL': QUICK_HEALTH,
           'DETECTOR_HEALTH_INTERVAL': QUICK_HEALTH, 'CAMERA_HEALTH_INTERVAL': QUICK_HEALTH,
           'APP_HEALTH_GRACE_SEC': '10', 'DETECTOR_HEALTH_GRACE_SEC': '10',
           'DETECTOR_HEALTH_FAILS_BEFORE_RESTART': '3', 'APP_HEALTH_FAILS_BEFORE_RESTART': '3'}
    stack = Stack(f'http://127.0.0.1:{port}', work / 'data', env)
    try:
        stack.compose('up', '--detach', '--build', '--wait', timeout=BUILD_AND_START_SEC)
        yield stack
    finally:
        logs = stack.compose('logs', '--tail', '40', check=False).stdout
        print(logs)
        stack.compose('down', '--volumes', check=False)
        shutil.rmtree(work, ignore_errors=True)


def test_stack_starts_healthy(stack):
    lines = stack.compose('ps', '--format', 'json').stdout.splitlines()
    health = {row['Service']: row['Health'] for row in map(json.loads, lines)}
    assert health == {'camera': 'healthy', 'detector': 'healthy', 'app': 'healthy'}


def test_pictures_arrive_at_the_app(stack):
    give_up = time.monotonic() + ARRIVE_SEC
    while True:
        live = httpx.get(f'{stack.url}/api/live').json()
        received = httpx.get(f'{stack.url}/api/health').json()['camera']['pictures_received']
        if live['camera'] and live['camera_source'] == 'pi' and received:
            break
        assert time.monotonic() < give_up, f'no pictures: {live}'
        time.sleep(1)
    time.sleep(2)
    assert httpx.get(f'{stack.url}/api/health').json()['camera']['pictures_received'] > received


def test_the_camera_got_the_detector_address_and_its_folder_from_the_pi_settings(stack):
    logs = stack.compose('logs', 'camera').stdout
    assert 'saved in /photos' in logs
    assert 'Sending to http://app:' in logs
    assert 'Using the settings in config/settings.yaml' not in logs


def test_app_uses_the_users_time_zone(stack):
    done = stack.compose('exec', '-T', 'app', 'python', '-c', LOCAL_HOUR)
    assert int(done.stdout.strip()) == HOUR_THERE


def test_detector_port_is_not_open_outside(stack):
    port = int(env_file(PI / '.env.example')['DETECTOR_PORT'])
    with pytest.raises(OSError), socket.create_connection(('127.0.0.1', port), timeout=2):
        pass
    assert stack.compose('port', 'detector', str(port), check=False).stdout.strip() in ('', ':0')


def test_the_app_can_reach_the_detector_by_name(stack):
    port = env_file(PI / '.env.example')['DETECTOR_PORT']
    done = stack.compose('exec', '-T', '-e', f'ASK_PORT={port}', 'app', 'python', '-c', ASK_THE_DETECTOR)
    assert done.stdout.strip()


def test_the_app_saves_in_the_data_folder_as_the_pi_user(stack):
    assert (stack.data / 'logs').is_dir()
    done = stack.compose('exec', '-T', 'app', 'sh', '-c', 'stat -c %u /proc/$(cut -d" " -f1 /proc/1/task/1/children)')
    assert done.stdout.strip() == env_file(PI / '.env.example')['PI_USER_ID']


def test_the_danger_zones_start_in_the_data_folder(stack):
    assert (stack.data / 'safety_rules.json').is_file()


def test_the_camera_can_read_the_pairing_key_but_not_change_it(stack):
    assert stack.compose('exec', '-T', 'camera', 'test', '-s', '/pairing/pair-key.txt', check=False).returncode == 0
    assert stack.compose('exec', '-T', 'camera', 'touch', '/pairing/nope', check=False).returncode != 0


def test_the_app_image_holds_no_keys(stack):
    assert stack.compose('exec', '-T', 'app', 'test', '-e', '/desk-talk/.env', check=False).returncode != 0
    assert stack.compose('exec', '-T', 'app', 'test', '-e', '/desk-talk/certs', check=False).returncode != 0


def test_the_app_has_no_detector_of_its_own(stack):
    done = stack.compose('exec', '-T', 'app', 'python', '-c',
                         "import importlib.util as u; print(u.find_spec('torch'), u.find_spec('ultralytics'))")
    assert done.stdout.strip() == 'None None'


def test_no_container_but_the_camera_has_all_the_devices(stack):
    for name in ('app', 'detector'):
        container = stack.compose('ps', '--quiet', name).stdout.strip()
        shown = '{{json .HostConfig.Binds}} {{.HostConfig.Privileged}}'
        found = subprocess.run(['docker', 'inspect', '--format', shown, container],
                               capture_output=True, text=True, check=True).stdout
        assert '/dev:/dev' not in found and found.strip().endswith('false')


def test_the_app_image_can_speak_with_no_network(stack):
    """Piper and its voice are in the image: the words come out as sound data, ready for aplay."""
    voice = 'import yaml; print(yaml.safe_load(open("config/settings.yaml"))["notifications"]["voice_model"])'
    model = stack.compose('exec', '-T', 'app', 'python', '-c', voice).stdout.strip()
    said = stack.compose('exec', '-T', 'app', 'bash', '-c',
                         f'echo Mind the cup | piper --quiet --model {model} --output_raw | wc -c').stdout
    assert int(said.split()[-1]) > 10_000
    assert stack.compose('exec', '-T', 'app', 'which', 'aplay').stdout.strip()


def test_the_app_keeps_the_audio_group_when_it_gives_up_root(stack):
    find = ("for p in /proc/[0-9]*; do tr '\\0' ' ' < $p/cmdline 2>/dev/null | grep -q '^python server.py' "
            '&& grep Groups $p/status; done; true')
    assert stack.compose('exec', '-T', 'app', 'sh', '-c', find).stdout.split() == ['Groups:', '29']


def test_ultralytics_sends_nothing_home(stack):
    assert stack.compose('exec', '-T', 'detector', 'python', '-c', ANALYTICS).stdout.split()[-3:] == ['False'] * 3


def test_the_camera_runs_with_no_rights_and_private_folders_over_the_shared_ones(stack):
    found = stack.compose('exec', '-T', 'camera', 'sh', '-c', 'grep CapEff /proc/1/status; cat /proc/mounts').stdout
    assert 'CapEff:\t0000000000000000' in found
    for folder in ('/dev/shm', '/dev/pts', '/dev/mqueue'):
        assert f'tmpfs {folder} tmpfs' in found


@pytest.mark.parametrize('name', ['detector', 'app'])
def test_a_hung_program_comes_back_by_itself(stack, name):
    """Runs last: it restarts containers."""
    container = stack.compose('ps', '--quiet', name).stdout.strip()

    def state() -> tuple[int, str]:
        shown = '{{.RestartCount}} {{.State.Health.Status}}'
        out = subprocess.run(['docker', 'inspect', '--format', shown, container], capture_output=True, text=True,
                             check=True).stdout.split()
        return int(out[0]), out[1]

    before, _ = state()
    time.sleep(12)                # past the grace time, so the failures count
    stack.compose('exec', '-T', name, 'python', '-c', HANG)
    give_up = time.monotonic() + BACK_SEC
    while True:
        count, health = state()
        if count > before and health == 'healthy':
            break
        assert time.monotonic() < give_up, f'{name}: restarts {count}, {health}'
        time.sleep(2)
