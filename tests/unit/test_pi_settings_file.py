"""raspberry_pi/settings.pi.yaml laid over config/settings.yaml, the way compose mounts it, and the numbers in
raspberry_pi/.env.example that must agree with what that gives."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from desktalk.config import AppConfig, load_config
from tests.unit.test_pi_compose_file import env_file

ROOT = Path(__file__).resolve().parents[2]
PI = ROOT / 'raspberry_pi'


def config_on_the_pi(tmp_path: Path) -> AppConfig:
    """The app's config with settings.pi.yaml in the place compose mounts it (config/settings.local.yaml)."""
    (tmp_path / 'config').mkdir()
    for name in ('settings.yaml', 'protocols.yaml', 'agents.yaml', 'prompts.yaml'):
        shutil.copy(ROOT / 'config' / name, tmp_path / 'config' / name)
    shutil.copy(PI / 'settings.pi.yaml', tmp_path / 'config' / 'settings.local.yaml')
    return load_config(root=tmp_path, environ={})


def test_pi_settings_load(tmp_path, caplog):
    with caplog.at_level(logging.WARNING):
        config = config_on_the_pi(tmp_path)
    assert 'no longer used' not in caplog.text and 'is now' not in caplog.text
    assert config.camera.fps == config.pi.fps        # pictures are taken as fast as they are looked at


def test_the_port_you_open_from_a_phone_is_the_one_the_app_listens_on(tmp_path):
    """APP_HOST_PORT is what the links show; web.port is what the app answers on. The example keeps them the same, so
    the links work before anyone changes either."""
    config = config_on_the_pi(tmp_path)
    assert int(env_file(PI / '.env.example')['APP_HOST_PORT']) == config.web.port
