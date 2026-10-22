from pathlib import Path

import pytest

from deskbench import probes


def fake_pi(root: Path, temp_milli='52350', zone_type='cpu-thermal', cpu_khz=('2400000',) * 4, top_khz='2400000'):
    zone = root / 'sys/class/thermal/thermal_zone0'
    zone.mkdir(parents=True)
    (zone / 'type').write_text(zone_type + '\n')
    (zone / 'temp').write_text(temp_milli + '\n')
    for n, khz in enumerate(cpu_khz):
        freq = root / f'sys/devices/system/cpu/cpu{n}/cpufreq'
        freq.mkdir(parents=True)
        (freq / 'scaling_cur_freq').write_text(khz + '\n')
        (freq / 'cpuinfo_max_freq').write_text(top_khz + '\n')
    model = root / 'proc/device-tree'
    model.mkdir(parents=True)
    (model / 'model').write_bytes(b'Raspberry Pi 5 Model B Rev 1.0\x00')
    return root


def test_reads_pi_temperature_in_celsius(tmp_path):
    assert probes.read_cpu_temp(fake_pi(tmp_path)) == pytest.approx(52.35)


def test_no_temperature_on_a_mac(tmp_path):
    assert probes.read_cpu_temp(tmp_path) is None


def test_broken_temperature_file_is_not_fatal(tmp_path):
    assert probes.read_cpu_temp(fake_pi(tmp_path, temp_milli='oops')) is None


def test_prefers_the_cpu_zone(tmp_path):
    fake_pi(tmp_path, zone_type='gpu-thermal', temp_milli='90000')
    cpu = tmp_path / 'sys/class/thermal/thermal_zone1'
    cpu.mkdir()
    (cpu / 'type').write_text('cpu-thermal')
    (cpu / 'temp').write_text('48000')
    assert probes.read_cpu_temp(tmp_path) == pytest.approx(48.0)


def test_cpu_speed_is_the_slowest_core_in_mhz(tmp_path):
    fake_pi(tmp_path, cpu_khz=('2400000', '1500000', '2400000', '2400000'))
    assert probes.read_cpu_mhz(tmp_path) == pytest.approx(1500.0)
    assert probes.read_cpu_top_mhz(tmp_path) == pytest.approx(2400.0)


def test_no_cpu_speed_on_a_mac(tmp_path):
    assert probes.read_cpu_mhz(tmp_path) is None
    assert probes.read_cpu_top_mhz(tmp_path) is None


def test_broken_cpu_speed_file_is_not_fatal(tmp_path):
    fake_pi(tmp_path, cpu_khz=('oops', '2400000'))
    assert probes.read_cpu_mhz(tmp_path) == pytest.approx(2400.0)


def test_device_label(tmp_path):
    assert probes.device_label(fake_pi(tmp_path / 'pi')) == 'pi'
    assert probes.device_label(tmp_path / 'mac') == 'laptop'
    assert 'Raspberry Pi 5' in probes.hardware_name(tmp_path / 'pi')


def test_peak_memory_units():
    assert probes.peak_memory_mb(512 * 1024 * 1024, 'darwin') == pytest.approx(512)   # bytes on macOS
    assert probes.peak_memory_mb(512 * 1024, 'linux') == pytest.approx(512)           # KiB on Linux


def test_git_commit_from_a_branch(tmp_path):
    git = tmp_path / '.git'
    (git / 'refs/heads').mkdir(parents=True)
    (git / 'HEAD').write_text('ref: refs/heads/main\n')
    (git / 'refs/heads/main').write_text('abc123def456\n')
    assert probes.git_commit(tmp_path) == 'abc123def456'
    assert probes.git_commit(tmp_path / 'none') is None
