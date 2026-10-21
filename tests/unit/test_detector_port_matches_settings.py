"""The detector container's port (raspberry_pi/detector_service/.env) and the URL the computer tells the
camera program to use (config/settings.yaml → pi.detector.url) must be the same port."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from desktalk.config import load_config

ROOT = Path(__file__).resolve().parents[2]


def env_value(path: Path, name: str) -> str:
    for line in path.read_text(encoding='utf-8').splitlines():
        key, _, value = line.partition('=')
        if key.strip() == name:
            return value.split('#')[0].strip()
    raise AssertionError(f'{name} is not in {path}')


def test_detector_port_matches_the_settings():
    port = int(env_value(ROOT / 'raspberry_pi' / 'detector_service' / '.env', 'DETECTOR_PORT'))
    url = urlparse(load_config(root=ROOT, environ={}).pi.detector.url)
    assert (url.hostname, url.port) == ('127.0.0.1', port)
