"""The web address settings (web.public_address, dev.pi_address), and settings the camera and detector use."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from desktalk.config import ConfigError, load_config
from tests.unit.test_config import CONFIG_FILES

ROOT = Path(__file__).resolve().parents[2]


def test_the_detector_camera_and_pi_settings_load():
    c = load_config(root=ROOT, environ={})
    assert (c.detector.end2end, c.detector.rect) == (False, False)
    assert c.camera.pair_key_bytes == 6
    assert c.storage.folder == Path('data')
    assert c.agents.personality_max_chars == 400
    assert c.chat.reply_max_chars == 500
    assert c.lens.wait_before_saying_sec.pi == 10
    assert (c.pi.camera_warmup_sec, c.pi.pause_after_error_sec, c.pi.status_every_sec, c.pi.health_every_sec,
            c.pi.settings_check_every_sec) == (1, 0.5, 30, 3, 60)


def project_with_local(tmp_path, local: dict | None = None, *, copy_from: Path | None = None):
    """A project folder with the real config files, and a settings.local.yaml (a dict, or a copy of a file)."""
    (tmp_path / 'config').mkdir()
    for name in CONFIG_FILES:
        shutil.copy(ROOT / 'config' / name, tmp_path / 'config' / name)
    target = tmp_path / 'config' / 'settings.local.yaml'
    if copy_from is not None:
        shutil.copy(copy_from, target)
    elif local is not None:
        target.write_text(yaml.safe_dump(local))
    return tmp_path


def test_the_public_address_is_empty_unless_set(tmp_path):
    assert load_config(root=project_with_local(tmp_path), environ={}).web.public_address == ''


def test_the_public_address_is_a_host_and_the_web_port(tmp_path):
    project = project_with_local(tmp_path, {'web': {'public_address': ' desktalk.local:5000 '}})
    assert load_config(root=project, environ={}).web.public_address == 'desktalk.local:5000'


def test_the_pi_gets_its_address_from_the_environment_compose_gives_it(tmp_path):
    project = project_with_local(tmp_path, copy_from=ROOT / 'raspberry_pi' / 'settings.pi.yaml')
    config = load_config(root=project, environ={'PUBLIC_ADDRESS': 'desktalk-pi.local:8123', 'OUTSIDE_PORT': '8123'})
    assert config.web.public_address == 'desktalk-pi.local:8123'
    assert config.web.port == 5000 and config.web.outside_port == 8123


def test_with_no_outside_port_the_address_must_still_use_web_port(tmp_path):
    with pytest.raises(ConfigError, match='public_address must be a name and the port'):
        load_config(root=project_with_local(tmp_path), environ={'PUBLIC_ADDRESS': 'desktalk.local:8123'})


def test_an_address_with_a_port_docker_doesnt_open_is_refused(tmp_path):
    environ = {'PUBLIC_ADDRESS': 'desktalk.local:9999', 'OUTSIDE_PORT': '8123'}
    with pytest.raises(ConfigError, match='public_address must be a name and the port'):
        load_config(root=project_with_local(tmp_path), environ=environ)


@pytest.mark.parametrize('address', [
    'desktalk.local',                       # no port: the link would open port 80
    'desktalk.local:5001',                  # not the port the app listens on (web.port)
    'desktalk.local:abc', 'desktalk.local:', ':5000',
    'http://desktalk.local:5000', 'desktalk.local:5000/page', 'desktalk.local:5000?x=1',
    'desktalk.local:5000#top', 'me@desktalk.local:5000', 'desk talk.local:5000',
])
def test_a_public_address_that_would_not_open_the_app_is_refused(tmp_path, address):
    project = project_with_local(tmp_path, {'web': {'public_address': address}})
    with pytest.raises(ConfigError, match=r'public_address must be a name and the port.*like desktalk\.local:5000'):
        load_config(root=project, environ={})


@pytest.mark.parametrize('written', [
    'desktalk-pi.local:5000', ' desktalk-pi.local:5000 ', 'http://desktalk-pi.local:5000',
    'http://desktalk-pi.local:5000/', ' http://desktalk-pi.local:5000/ ',
])
def test_the_pis_address_is_kept_as_a_name_and_port(tmp_path, written):
    project = project_with_local(tmp_path, {'dev': {'pi_address': written}})
    assert load_config(root=project, environ={}).dev.pi_address == 'desktalk-pi.local:5000'
