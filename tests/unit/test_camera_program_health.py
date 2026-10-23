"""raspberry_pi/camera/health.py: the Pi's temperature, slow-down flags and disk, from a fake /sys tree."""

import time

import pytest

from raspberry_pi.camera.health import HealthReader, throttle_words
from raspberry_pi.camera.photo_store import PhotoStore


def write(root, path, text):
    file = root / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(text)


def test_cpu_temperature_from_the_cpu_thermal_zone(tmp_path):
    write(tmp_path, 'sys/class/thermal/thermal_zone0/type', 'battery\n')
    write(tmp_path, 'sys/class/thermal/thermal_zone0/temp', '30000\n')
    write(tmp_path, 'sys/class/thermal/thermal_zone1/type', 'cpu-thermal\n')
    write(tmp_path, 'sys/class/thermal/thermal_zone1/temp', '52312\n')
    assert HealthReader(tmp_path).cpu_temp_c() == 52.3


def test_cpu_temperature_falls_back_to_the_first_zone(tmp_path):
    write(tmp_path, 'sys/class/thermal/thermal_zone0/type', 'soc_thermal\n')
    write(tmp_path, 'sys/class/thermal/thermal_zone0/temp', '61000\n')
    assert HealthReader(tmp_path).cpu_temp_c() == 61.0


def test_no_temperature_when_this_is_not_a_pi(tmp_path):
    assert HealthReader(tmp_path).cpu_temp_c() is None


@pytest.mark.parametrize('firmware', ['soc/soc:firmware', 'soc@107c000000/soc@107c000000:firmware'])
def test_slow_down_flags_from_the_firmware(tmp_path, firmware):
    write(tmp_path, f'sys/devices/platform/{firmware}/get_throttled', '50005\n')
    assert HealthReader(tmp_path).throttled() == '0x50005'


def test_no_slow_down_flags_when_this_is_not_a_pi(tmp_path):
    assert HealthReader(tmp_path).throttled() == ''


def test_slow_down_flags_in_plain_words():
    now, earlier = throttle_words('0x50005')
    assert now == ['the power supply is too weak', 'the processor is slowed down']
    assert earlier == ['the power supply was too weak', 'the processor was slowed down']
    assert throttle_words('0x0') == ([], [])
    assert throttle_words('') == ([], [])
    assert throttle_words('rubbish') == ([], [])


def test_read_gives_everything_the_pi_reports_with_a_picture(tmp_path):
    write(tmp_path, 'sys/class/thermal/thermal_zone0/type', 'cpu-thermal\n')
    write(tmp_path, 'sys/class/thermal/thermal_zone0/temp', '48000\n')
    write(tmp_path, 'sys/devices/platform/soc/soc:firmware/get_throttled', '0\n')
    store = PhotoStore(tmp_path / 'photos', keep_last_sec=0, min_free_mb=0, free_mb=lambda: 1234.7)
    store.save(b'x', time.time(), 'pi-1')
    store.save(b'x', time.time(), 'pi-2')
    store.cleanup(time.time() - 3600)                           # deletes nothing, but counts as a cleanup
    health = HealthReader(tmp_path).read('picamera2', store)
    assert health.camera == 'picamera2'
    assert health.photos_stored == 2
    assert health.disk_free_mb == 1234
    assert health.last_cleanup_ts == store.last_cleanup
    assert health.cpu_temp_c == 48.0
    assert health.throttled == '0x0'
