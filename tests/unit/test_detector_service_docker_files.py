"""
The detector service's Docker files agree with each other and with the service, checked without Docker:
the container's environment, as compose builds it from .env and the Dockerfile, must be settings the
service accepts. (tests/docker/ builds and runs the real image, when Docker is there.)
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from desktalk_detector.settings import ServiceSettings

SERVICE = Path(__file__).resolve().parents[2] / 'raspberry_pi' / 'detector_service'
NAME = re.compile(r'\$\{(\w+)\}')


def env_file() -> dict[str, str]:
    lines = (SERVICE / '.env').read_text(encoding='utf-8').splitlines()
    return dict(line.split('=', 1) for line in lines if line.strip() and not line.lstrip().startswith('#'))


def dockerfile_env() -> dict[str, str]:
    return dict(re.findall(r'\b(DETECTOR_\w+)=(\S+)', (SERVICE / 'Dockerfile').read_text(encoding='utf-8')))


@pytest.fixture
def detector():
    return yaml.safe_load((SERVICE / 'compose.yaml').read_text(encoding='utf-8'))['services']['detector']


def fill(text: str, values: dict[str, str]) -> str:
    return NAME.sub(lambda m: values[m.group(1)], str(text))


def test_every_value_compose_uses_is_in_the_env_file():
    used = set(NAME.findall((SERVICE / 'compose.yaml').read_text(encoding='utf-8')))
    assert used and used <= set(env_file())


def test_the_container_gets_settings_the_service_accepts(detector):
    values = env_file()
    container_env = {**dockerfile_env(), **{k: fill(v, values) for k, v in detector['environment'].items()}}
    settings = ServiceSettings.from_env(container_env)
    assert settings.host == '0.0.0.0'                         # reachable through Docker's port mapping
    assert [fill(p, values) for p in detector['ports']] == [f'127.0.0.1:{settings.port}:{settings.port}']


def test_the_models_volume_is_where_the_service_keeps_its_models(detector):
    assert f'models:{dockerfile_env()["DETECTOR_MODELS_DIR"]}' in detector['volumes']
