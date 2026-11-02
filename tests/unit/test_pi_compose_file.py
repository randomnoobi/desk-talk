"""raspberry_pi/compose.yaml, .env and settings.pi.yaml: what must hold before Docker is even started."""

from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml

from desktalk.config import load_config

ROOT = Path(__file__).resolve().parents[2]
PI = ROOT / 'raspberry_pi'
SERVICES = {'camera', 'detector', 'app'}


def env_file(path: Path) -> dict[str, str]:
    lines = path.read_text(encoding='utf-8').splitlines()
    return dict(line.split('=', 1) for line in lines if line.strip() and not line.lstrip().startswith('#'))


@pytest.fixture(scope='module')
def compose() -> dict[str, Any]:
    return yaml.safe_load((PI / 'compose.yaml').read_text(encoding='utf-8'))


@pytest.fixture(scope='module')
def services(compose) -> dict[str, Any]:
    return compose['services']


@pytest.fixture(scope='module')
def pi_settings() -> dict[str, Any]:
    return yaml.safe_load((PI / 'settings.pi.yaml').read_text(encoding='utf-8'))


def no_fallbacks(text: str) -> str:
    """${NAME:-value} → ${NAME}. (test_detector_service_docker_files.py checks the fallbacks match .env.)"""
    return re.sub(r'\$\{(\w+):-[^}]*\}', r'${\1}', str(text))


def mounts(service: dict[str, Any]) -> dict[str, str]:
    """Where in the container → the whole volume line."""
    lines = [no_fallbacks(line) for line in service['volumes']]
    return {line.split(':')[1]: line for line in lines}


def test_it_has_the_three_containers(services):
    assert set(services) == SERVICES


@pytest.mark.parametrize('name', sorted(SERVICES))
def test_each_container_restarts_has_a_memory_limit_small_logs_and_a_health_check(services, name):
    service = services[name]
    assert service['restart'] == 'unless-stopped'
    assert service['mem_limit'].startswith('${') and service['memswap_limit'] == service['mem_limit']
    assert service['logging']['options']['max-size'].startswith('${')
    assert service['logging']['options']['max-file'].startswith('${')
    assert service['healthcheck']['test']
    assert no_fallbacks(service['environment']['TZ']) == '${TZ}'


def test_every_name_the_compose_file_uses_is_in_the_env_file():
    text = (PI / 'compose.yaml').read_text(encoding='utf-8')
    used = set(re.findall(r'\$\{(\w+)', '\n'.join(line.split(' #')[0] for line in text.splitlines())))
    assert used <= set(env_file(PI / '.env'))


def test_env_example_has_the_same_names_as_env():
    assert set(env_file(PI / '.env.example')) == set(env_file(PI / '.env'))


def test_only_the_camera_gets_the_devices_and_nothing_is_privileged(services):
    assert '/dev' in mounts(services['camera'])
    assert mounts(services['camera'])['/run/udev'].endswith(':ro')
    assert len(services['camera']['device_cgroup_rules']) == 3
    assert services['camera']['group_add']
    for name, service in services.items():
        assert 'privileged' not in service
        assert 'devices' not in service
        if name != 'camera':
            assert '/dev' not in mounts(service)
            assert 'device_cgroup_rules' not in service


def test_the_detector_has_no_open_port_and_only_the_app_does(services):
    assert 'ports' not in services['detector'] and 'ports' not in services['camera']
    assert [no_fallbacks(port) for port in services['app']['ports']] == ['${APP_HOST_PORT}:${APP_PORT}']


def test_the_app_port_is_the_one_in_the_settings():
    assert int(env_file(PI / '.env')['APP_PORT']) == load_config(root=ROOT, environ={}).web.port


def test_the_pairing_key_is_shared_read_only_for_the_camera(services, pi_settings):
    key = Path(pi_settings['camera']['pair_key_file'])
    assert services['camera']['environment']['DESKTALK_PAIR_KEY_FILE'] == str(key)
    assert mounts(services['app'])[str(key.parent)].split(':') == ['pairing', str(key.parent)]
    assert mounts(services['camera'])[str(key.parent)].split(':') == ['pairing', str(key.parent), 'ro']
    assert 'pairing' not in str(services['detector']['volumes'])


def test_the_camera_waits_for_the_app_to_be_healthy_and_uses_its_address(services):
    camera = services['camera']
    assert camera['depends_on']['app']['condition'] == 'service_healthy'
    assert no_fallbacks(camera['environment']['DESKTALK_SERVER']) == 'http://app:${APP_PORT}'


def test_the_cameras_pictures_are_kept_where_the_settings_say(services, pi_settings):
    folder = pi_settings['pi']['photo_dir']
    assert mounts(services['camera'])[folder] == f'photos:{folder}'


def test_both_programs_that_read_the_settings_get_the_pi_settings_read_only(services):
    for name in ('app', 'camera'):
        line = mounts(services[name])['/desk-talk/config/settings.local.yaml']
        assert line == './settings.pi.yaml:/desk-talk/config/settings.local.yaml:ro'


def test_the_data_folder_location_comes_from_the_env_file(services):
    assert mounts(services['app'])['/desk-talk/data'] == '${DESKTALK_DATA}:/desk-talk/data'
    assert env_file(PI / '.env')['DESKTALK_DATA'] == '../data'      # next to the repo's raspberry_pi/ folder


def test_the_app_with_the_pi_settings_loads_without_a_word_about_old_settings(tmp_path, caplog):
    (tmp_path / 'config').mkdir()
    for name in ('settings.yaml', 'protocols.yaml', 'agents.yaml', 'prompts.yaml'):
        shutil.copy(ROOT / 'config' / name, tmp_path / 'config' / name)
    shutil.copy(PI / 'settings.pi.yaml', tmp_path / 'config' / 'settings.local.yaml')
    with caplog.at_level(logging.WARNING):
        config = load_config(root=tmp_path, environ={})
    assert 'no longer used' not in caplog.text
    assert config.detector.enabled is False and config.dev.enabled is False
    assert config.camera.source == 'pi'
    assert config.pi.detector.url.startswith('http://detector:')
    assert not config.secrets.gemini_api_key.get_secret_value()


def test_the_pi_settings_set_no_ai(pi_settings):
    assert 'llm' not in pi_settings and 'chat' not in pi_settings


def test_the_app_image_can_see_no_keys_or_data():
    lines = [line for line in (PI / 'app' / 'Dockerfile.dockerignore').read_text(encoding='utf-8').splitlines()
             if not line.startswith('#')]
    assert lines[0] == '*'
    let_in = {line[1:] for line in lines if line.startswith('!')}
    assert not {'.env', 'data', 'certs', 'models'} & let_in
    assert 'config/settings.local.yaml' in lines


def test_without_buildkit_the_top_list_keeps_the_same_things_out():
    lines = (ROOT / '.dockerignore').read_text(encoding='utf-8').splitlines()
    assert {'**/.env', 'config/settings.local.yaml', 'data', 'certs', 'models'} <= set(lines)
