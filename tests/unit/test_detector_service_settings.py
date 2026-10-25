"""The detector service reads its settings from the environment (raspberry_pi/detector_service/.env in Docker)."""

from __future__ import annotations

from pathlib import Path

import pytest

from desktalk_detector.settings import ServiceSettings, SettingsError

ENV = {
    'DETECTOR_HOST': '0.0.0.0',
    'DETECTOR_PORT': '8765',
    'DETECTOR_MODELS_DIR': '/models',
    'DETECTOR_MAX_PICTURE_MB': '2.5',
    'DETECTOR_RETRY_FIRST_SEC': '5',
    'DETECTOR_RETRY_MAX_SEC': '300',
}


def test_reads_every_setting_from_the_environment():
    assert ServiceSettings.from_env(ENV) == ServiceSettings(
        host='0.0.0.0', port=8765, models_dir=Path('/models'), max_picture_bytes=int(2.5 * 1024 * 1024),
        retry_first_sec=5.0, retry_max_sec=300.0)


def test_a_megabyte_is_1024_times_1024_bytes():
    assert ServiceSettings.from_env({**ENV, 'DETECTOR_MAX_PICTURE_MB': '1'}).max_picture_bytes == 1_048_576


@pytest.mark.parametrize('name', sorted(ENV))
def test_a_missing_setting_is_named(name):
    env = {k: v for k, v in ENV.items() if k != name}
    with pytest.raises(SettingsError, match=name):
        ServiceSettings.from_env(env)


@pytest.mark.parametrize('name, value', [
    ('DETECTOR_PORT', 'eighty'),
    ('DETECTOR_PORT', '0'),
    ('DETECTOR_PORT', '7.5'),
    ('DETECTOR_MAX_PICTURE_MB', '-1'),
    ('DETECTOR_RETRY_FIRST_SEC', 'soon'),
    ('DETECTOR_RETRY_MAX_SEC', '0'),
])
def test_a_bad_number_is_named(name, value):
    with pytest.raises(SettingsError, match=name):
        ServiceSettings.from_env({**ENV, name: value})


def test_the_longest_wait_cannot_be_shorter_than_the_first():
    with pytest.raises(SettingsError, match='DETECTOR_RETRY_MAX_SEC'):
        ServiceSettings.from_env({**ENV, 'DETECTOR_RETRY_FIRST_SEC': '60', 'DETECTOR_RETRY_MAX_SEC': '10'})
