"""
Builds the detector service's Docker image, starts it with docker compose, and checks /health and /detect
with a real model (yolo11n.pt, which the service downloads from Ultralytics the first time).

Opt-in: it needs Docker and the internet, and takes several minutes the first time.

    uv run pytest -m docker tests/docker

It uses its own compose project name, so it never touches a detector you already run; the test's
containers and models volume are removed at the end.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

import pytest

from desktalk_shared.detector_api import DetectorHealth, DetectReply, ModelSettings, ServiceState

pytestmark = pytest.mark.docker

SERVICE = Path(__file__).resolve().parents[2] / 'raspberry_pi' / 'detector_service'
PROJECT = 'desktalk-detector-test'
MODEL = ModelSettings(model='yolo11n.pt', format='ncnn', image_size=320, confidence=0.35, half=True)
BUILD_AND_START_SEC = 3600       # the first build downloads PyTorch and friends
READY_SEC = 900                  # downloading yolo11n.pt and converting it to ncnn


def env_file() -> dict[str, str]:
    lines = (SERVICE / '.env').read_text(encoding='utf-8').splitlines()
    return dict(line.split('=', 1) for line in lines if line.strip() and not line.lstrip().startswith('#'))


def compose(*args: str, timeout: float = 300, check: bool = True) -> str:
    done = subprocess.run(['docker', 'compose', '-p', PROJECT, '-f', str(SERVICE / 'compose.yaml'), *args],
                          capture_output=True, text=True, timeout=timeout, check=False)
    if check and done.returncode != 0:
        raise AssertionError(f'docker compose {" ".join(args)} failed:\n{done.stdout}\n{done.stderr}')
    return done.stdout.strip()


@pytest.fixture(scope='module')
def url(request):
    if shutil.which('docker') is None:
        pytest.skip("Docker isn't installed")
    marks = request.config.getoption('markexpr') or ''
    if 'docker' not in marks or 'not docker' in marks:
        pytest.skip('opt-in: uv run pytest -m docker tests/docker')
    if subprocess.run(['docker', 'info'], capture_output=True, check=False).returncode != 0:
        pytest.skip("Docker isn't running")
    compose('up', '--detach', '--build', '--wait', timeout=BUILD_AND_START_SEC)
    try:
        yield f'http://127.0.0.1:{env_file()["DETECTOR_PORT"]}'
    finally:
        compose('down', '--volumes', check=False)


def test_health_answers_before_it_has_a_model(url):
    import httpx
    health = DetectorHealth.from_dict(httpx.get(f'{url}/health').json())
    assert health.state is ServiceState.STARTING and health.model is None


def test_the_port_is_open_on_127_0_0_1_only(url):
    port = env_file()['DETECTOR_PORT']
    assert compose('port', 'detector', port) == f'127.0.0.1:{port}'


def test_the_container_has_a_memory_limit(url):
    container = compose('ps', '--quiet', 'detector')
    limit = subprocess.run(['docker', 'inspect', '--format', '{{.HostConfig.Memory}}', container],
                           capture_output=True, text=True, check=True).stdout.strip()
    assert int(limit) > 0


def test_detects_things_once_told_which_model_to_run(url):
    import httpx
    from ultralytics.utils import ASSETS
    assert httpx.put(f'{url}/model', json=MODEL.to_dict()).status_code == 200
    give_up = time.monotonic() + READY_SEC
    while (health := DetectorHealth.from_dict(httpx.get(f'{url}/health').json())).state is not ServiceState.READY:
        assert time.monotonic() < give_up, health.message
        time.sleep(2)
    r = httpx.post(f'{url}/detect', content=(ASSETS / 'bus.jpg').read_bytes(),
                   headers={'Content-Type': 'image/jpeg'}, timeout=60)
    assert r.status_code == 200, r.text
    assert {'bus', 'person'} <= {d.label for d in DetectReply.from_dict(r.json()).detections}
