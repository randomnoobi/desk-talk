"""
The whole chain, as on a real desk: the camera program (raspberry_pi/camera, playing pictures from a folder)
→ the detector container (Docker) → the app on this computer (tests/e2e_kit/server.py) → /api/live.

Opt-in: it needs Docker and the internet (the container downloads yolo11n.pt from Ultralytics and turns it
into an ncnn model the first time), and takes several minutes the first time.

    uv run pytest -m docker tests/e2e

It uses its own compose project name, so it never touches a detector you already run (but it does need the
container's port in raspberry_pi/detector_service/.env to be free); the test's containers and models volume
are removed at the end.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest

from desktalk.config import AppConfig
from desktalk.domain import BBox, Detection
from desktalk_shared.wire import DetectorState
from tests.e2e_kit.server import AppServer, app_server
from tests.integration.app_kit import PAIR_KEY, changed

pytestmark = pytest.mark.docker

ROOT = Path(__file__).resolve().parents[2]
SERVICE = ROOT / 'raspberry_pi' / 'detector_service'
PROJECT = 'desktalk-e2e-test'
MODEL = {'model': 'yolo11n.pt', 'format': 'ncnn', 'image_size': 320}     # small, and Ultralytics hands it out
BUILD_AND_START_SEC = 3600       # the first build downloads PyTorch and friends
READY_SEC = 900                  # downloading yolo11n.pt and turning it into an ncnn model
FALL_BACK_SEC = 120              # the camera program gives up on a stopped container and says "off"
DETECTION_SEC = 60               # a picture's detections reach /api/live
CAMERA_STOP_SEC = 30
POLL_SEC = 1
PI_FINDS = {'bus', 'person'}     # what YOLO finds in Ultralytics' bus.jpg (the camera program's only picture)
COMPUTER_FINDS = Detection('cup', 0.9, BBox(300, 600, 380, 720))       # what this computer's fake detector finds


def env_file() -> dict[str, str]:
    lines = (SERVICE / '.env').read_text(encoding='utf-8').splitlines()
    return dict(line.split('=', 1) for line in lines if line.strip() and not line.lstrip().startswith('#'))


def compose(*args: str, timeout: float = 300, check: bool = True) -> str:
    done = subprocess.run(['docker', 'compose', '-p', PROJECT, '-f', str(SERVICE / 'compose.yaml'), *args],
                          capture_output=True, text=True, timeout=timeout, check=False)
    if check and done.returncode != 0:
        raise AssertionError(f'docker compose {" ".join(args)} failed:\n{done.stdout}\n{done.stderr}')
    return done.stdout.strip()


def wait_until(check: Callable[[], Any], within: float, what: str, log: Path | None = None) -> Any:
    """Asks check() every POLL_SEC until it gives something true; fails after `within` seconds."""
    give_up = time.monotonic() + within
    while True:
        try:
            found = check()
        except httpx.HTTPError:
            found = None
        if found:
            return found
        if time.monotonic() > give_up:
            said = log.read_text(encoding='utf-8')[-3000:] if log is not None and log.exists() else ''
            raise AssertionError(f'{what} did not happen within {within:g} s.\nThe camera program said:\n{said}')
        time.sleep(POLL_SEC)


def pi_detector(url: str) -> str | None:
    """What the newest upload said about the Pi's detector: ok, busy or off."""
    return (httpx.get(f'{url}/api/health').json().get('pi') or {}).get('detector')


def labels_on_live(url: str) -> set[str]:
    """Every label /api/live shows, agents and other things alike."""
    found = httpx.get(f'{url}/api/live').json()['detections']
    return ({o['class'] for o in found['objects'].values()} | {e['yolo_class'] for e in found['extras']})


@dataclass
class Stack:
    url: str
    server: AppServer
    camera_log: Path


@pytest.fixture(scope='module')
def stack(request, tmp_path_factory) -> Iterator[Stack]:
    if shutil.which('docker') is None:
        pytest.skip("Docker isn't installed")
    marks = request.config.getoption('markexpr') or ''
    if 'docker' not in marks or 'not docker' in marks:
        pytest.skip('opt-in: uv run pytest -m docker tests/e2e')
    if subprocess.run(['docker', 'info'], capture_output=True, check=False).returncode != 0:
        pytest.skip("Docker isn't running")
    from ultralytics.utils import ASSETS

    tmp = tmp_path_factory.mktemp('full-stack')
    photos = tmp / 'camera-pictures'
    photos.mkdir()
    shutil.copy(ASSETS / 'bus.jpg', photos / 'bus.jpg')
    detector_url = f'http://127.0.0.1:{env_file()["DETECTOR_PORT"]}'

    def to_the_container(config: AppConfig) -> AppConfig:
        return changed(config, 'pi.detector', url=detector_url, **MODEL)

    compose('up', '--detach', '--build', '--wait', timeout=BUILD_AND_START_SEC)
    try:
        with app_server(tmp, pictures=[photos / 'bus.jpg'], found={1: [COMPUTER_FINDS]},
                        change=to_the_container) as server:
            camera_log = tmp / 'camera-program.log'
            with camera_log.open('w', encoding='utf-8') as out:
                camera = subprocess.Popen(
                    [sys.executable, '-m', 'raspberry_pi.camera', '--server', server.url, '--key', PAIR_KEY,
                     '--camera', f'folder:{photos}', '--photo-dir', str(tmp / 'pi-photos')],
                    cwd=ROOT, stdout=out, stderr=subprocess.STDOUT)
                try:
                    yield Stack(server.url, server, camera_log)
                finally:
                    camera.terminate()
                    try:
                        camera.wait(CAMERA_STOP_SEC)
                    except subprocess.TimeoutExpired:
                        camera.kill()
                        camera.wait()
    finally:
        compose('down', '--volumes', check=False)


def test_camera_program_to_page_through_the_container(stack):
    wait_until(lambda: pi_detector(stack.url) == DetectorState.OK.value, READY_SEC,
               "The Pi's detector saying ok", stack.camera_log)
    seen = wait_until(lambda: labels_on_live(stack.url) & PI_FINDS, DETECTION_SEC,
                      "The container's detections reaching /api/live", stack.camera_log)
    assert seen <= PI_FINDS


def test_container_stopped_falls_back_to_off(stack):
    wait_until(lambda: pi_detector(stack.url) == DetectorState.OK.value, READY_SEC,
               "The Pi's detector saying ok", stack.camera_log)
    analysed = httpx.get(f'{stack.url}/api/health').json()['detection']['pictures_analysed']
    compose('stop')
    wait_until(lambda: pi_detector(stack.url) == DetectorState.OFF.value, FALL_BACK_SEC,
               "The Pi's uploads saying its detector is off", stack.camera_log)
    wait_until(lambda: COMPUTER_FINDS.label in labels_on_live(stack.url), DETECTION_SEC,
               "This computer's own detections reaching /api/live", stack.camera_log)
    health = httpx.get(f'{stack.url}/api/health').json()
    assert health['detection']['pictures_analysed'] > analysed
    assert health['pi']['detector_reason']          # the Pi says why, in plain words
