"""The detector container's port (raspberry_pi/.env) and the URLs the camera program may use (pi.detector.url in
raspberry_pi/settings.pi.yaml on the Pi, in config/settings.yaml on a computer) must be the same port."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

import yaml

from desktalk.config import load_config

ROOT = Path(__file__).resolve().parents[2]


def env_value(path: Path, name: str) -> str:
    for line in path.read_text(encoding='utf-8').splitlines():
        key, _, value = line.partition('=')
        if key.strip() == name:
            return value.split('#')[0].strip()
    raise AssertionError(f'{name} is not in {path}')


def detector_port() -> int:
    return int(env_value(ROOT / 'raspberry_pi' / '.env', 'DETECTOR_PORT'))


def test_the_pi_asks_the_detector_by_its_container_name_on_that_port():
    settings = yaml.safe_load((ROOT / 'raspberry_pi' / 'settings.pi.yaml').read_text(encoding='utf-8'))
    url = urlparse(settings['pi']['detector']['url'])
    assert (url.hostname, url.port) == ('detector', detector_port())


def test_the_settings_for_a_computer_keep_127_0_0_1_on_that_port():
    url = urlparse(load_config(root=ROOT, environ={}).pi.detector.url)
    assert (url.hostname, url.port) == ('127.0.0.1', detector_port())
