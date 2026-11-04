"""raspberry_pi/settings.pi.yaml laid over config/settings.yaml, the way compose mounts it, and the numbers in
raspberry_pi/.env.example that must agree with what that gives."""

from __future__ import annotations

import logging
import re
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


BUSY_DESK_PICTURE_MB = 0.1    # the Pi camera's 1280 x 720 pictures of a full desk were 64 to 85 KB at jpeg_quality 80
CAMERA_PROGRAM_MB = 150       # the camera program itself used at most 125 MB on the Pi 5 (Task 10)


def megabytes(size: str) -> float:
    """A Docker size like 300m or 1g, in MB."""
    return float(size[:-1]) * {'m': 1, 'g': 1024}[size[-1].lower()]


def test_the_pictures_kept_in_memory_fit_between_two_cleanups(tmp_path):
    """The camera keeps its pictures in memory (a tmpfs), which counts towards its container's memory limit. One
    cleanup's worth of busy desk pictures must fit, with pi.min_free_mb still free, or the camera would delete
    pictures all the time; and the pictures plus the program must fit under the limit, or Docker would stop it."""
    pi = config_on_the_pi(tmp_path).pi
    env = env_file(PI / '.env.example')
    photos_mb = megabytes(env['CAMERA_PHOTOS_SIZE'])
    one_cleanup = pi.fps * pi.delete_every_min * 60 * BUSY_DESK_PICTURE_MB
    assert pi.delete_every_min > 0 and pi.keep_last_min == 0
    assert one_cleanup + pi.min_free_mb <= photos_mb
    assert photos_mb + CAMERA_PROGRAM_MB <= megabytes(env['CAMERA_MEMORY_LIMIT'])


def test_the_model_the_pi_runs_has_a_checksum(tmp_path):
    """install.sh fetches pi.detector.model as settings.pi.yaml says it, and checks it against this list."""
    model = config_on_the_pi(tmp_path).pi.detector.model
    listed = (PI / 'model_checksums.txt').read_text(encoding='utf-8')
    assert re.search(rf'^[0-9a-f]{{64}}  {re.escape(model)}$', listed, re.M)
