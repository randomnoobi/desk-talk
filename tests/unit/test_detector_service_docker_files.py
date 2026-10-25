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
NAME = re.compile(r'\$\{(\w+)(?::-([^}]*))?\}')     # ${NAME} or ${NAME:-what it is when .env doesn't say}


def env_file() -> dict[str, str]:
    lines = (SERVICE / '.env').read_text(encoding='utf-8').splitlines()
    return dict(line.split('=', 1) for line in lines if line.strip() and not line.lstrip().startswith('#'))


def dockerfile_env() -> dict[str, str]:
    return dict(re.findall(r'\b(DETECTOR_\w+)=(\S+)', (SERVICE / 'Dockerfile').read_text(encoding='utf-8')))


@pytest.fixture
def detector():
    return yaml.safe_load((SERVICE / 'compose.yaml').read_text(encoding='utf-8'))['services']['detector']


def fill(text: str, values: dict[str, str]) -> str:
    """As docker compose does it: the .env value, or the fallback after :- when .env doesn't have that name."""
    return NAME.sub(lambda m: values[m.group(1)] if m.group(1) in values else m.group(2), str(text))


def compose_names() -> list[tuple[str, str | None]]:
    """Every ${NAME} or ${NAME:-fallback} in compose.yaml."""
    text = (SERVICE / 'compose.yaml').read_text(encoding='utf-8')
    return [(m.group(1), m.group(2)) for m in NAME.finditer(text)]


def test_every_value_compose_uses_is_in_the_env_file():
    used = {name for name, _ in compose_names()}
    assert used and used <= set(env_file())


def test_every_value_compose_uses_has_a_fallback_that_matches_the_env_file():
    """A Pi whose .env was edited by hand, without a newer name, still starts: compose uses the fallback,
    which is the same as .env here (and not '', which docker compose up refuses for a health check)."""
    values = env_file()
    for name, fallback in compose_names():
        assert fallback is not None, f'${{{name}}} in compose.yaml needs a fallback: ${{{name}:-{values[name]}}}'
        assert fallback == values[name], f'${{{name}:-{fallback}}} in compose.yaml, but .env says {values[name]}'


def test_with_an_empty_env_file_the_container_gets_the_same_values(detector):
    assert fill(detector, {}) == fill(detector, env_file())


def test_the_container_gets_settings_the_service_accepts(detector):
    values = env_file()
    container_env = {**dockerfile_env(), **{k: fill(v, values) for k, v in detector['environment'].items()}}
    settings = ServiceSettings.from_env(container_env)
    assert settings.host == '0.0.0.0'                         # reachable through Docker's port mapping
    assert [fill(p, values) for p in detector['ports']] == [f'127.0.0.1:{settings.port}:{settings.port}']


def test_the_models_volume_is_where_the_service_keeps_its_models(detector):
    assert f'models:{dockerfile_env()["DETECTOR_MODELS_DIR"]}' in detector['volumes']


def test_healthcheck_timings_and_log_sizes_come_from_the_env_file(detector):
    values = env_file()
    health, logs = detector['healthcheck'], detector['logging']['options']
    for raw in (health['interval'], health['timeout'], health['start_period'], health['retries'],
                logs['max-size'], logs['max-file']):
        assert NAME.fullmatch(str(raw)), f'{raw!r} is written into compose.yaml: put it in .env'
    filled = {key: fill(health[key], values) for key in ('interval', 'timeout', 'start_period', 'retries')}
    assert filled == {'interval': '30s', 'timeout': '5s', 'start_period': '60s', 'retries': '3'}    # as before
    assert (fill(logs['max-size'], values), fill(logs['max-file'], values)) == ('10m', '3')
