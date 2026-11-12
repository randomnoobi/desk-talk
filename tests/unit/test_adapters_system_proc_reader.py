"""Reading Linux's own files for the Pi tab (desktalk/adapters/system/proc_reader.py: ProcReader).

Every test builds a folder in tmp_path that looks like the Pi's /proc and /sys, with lines like the real Pi 5's
(read over ssh on 2026-11-11). The whole Pi's /proc is at /host/proc, like inside the app's container.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from desktalk.adapters.system import ProcReader
from desktalk.domain import Cooling, CpuTimes, DiskCounters, Fan, Named, NetCounters, RawReading, WifiNow
from tests.unit.test_core_vision_kit import app_config

SETTINGS = app_config().system_stats.model_copy(update={'host_proc_folder': Path('/host/proc')})
PAGE_BYTES = 16384     # the Pi 5's page size
TICKS = 100

STAT = """\
cpu  10132153 290696 3084719 46828483 16683 0 25195 0 0 0
cpu0 2533038 72674 771179 11707120 4170 0 12597 0 0 0
cpu1 2533038 72674 771180 11707121 4171 0 4199 0 0 0
cpu2 2533038 72674 771180 11707121 4171 0 4199 0 0 0
cpu3 2533039 72674 771180 11707121 4171 0 4200 0 0 0
intr 123456789 0 0 0
ctxt 987654321
btime 1762813600
processes 1234567
procs_running 3
procs_blocked 0
"""

MEMINFO = """\
MemTotal:        8245168 kB
MemFree:          512000 kB
MemAvailable:    6000000 kB
Buffers:          100000 kB
Cached:          3000000 kB
SwapCached:            0 kB
SwapTotal:        204800 kB
SwapFree:         200000 kB
"""

DISKSTATS = """\
   1       0 ram0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0
   7       0 loop0 52 0 2104 12 0 0 0 0 0 40 12 0 0 0 0 0 0
 179       0 mmcblk0 12345 678 987654 4321 23456 789 1234568 98765 0 54321 103086 0 0 0 0 1234 567
 179       1 mmcblk0p1 300 0 9000 100 2 0 4 1 0 120 101 0 0 0 0 0 0
 179       2 mmcblk0p2 12000 678 978000 4200 23454 789 1234564 98764 0 54200 102985 0 0 0 0 1234 567
"""

NET_DEV = """\
Inter-|   Receive                                                |  Transmit
 face |bytes packets errs drop fifo frame compressed multicast|bytes packets errs drop fifo colls carrier compressed
    lo: 123456 1000 0 0 0 0 0 0 123456 1000 0 0 0 0 0 0
 wlan0: 987654321 123456 2 12 0 0 0 0 123456789 65432 1 3 0 0 0 0
  eth0: 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0
docker0: 5000 50 0 0 0 0 0 0 6000 60 0 0 0 0 0 0
br-5f2a1c: 7000 70 0 0 0 0 0 0 8000 80 0 0 0 0 0 0
veth1a2b3c: 9000 90 0 0 0 0 0 0 10000 100 0 0 0 0 0 0
"""

WIRELESS = """\
Inter-| sta-|   Quality        |   Discarded packets               | Missed | WE
 face | tus | link level noise |  nwid  crypt   frag  retry   misc | beacon | 22
 wlan0: 0000   46.  -64.  -256        0      0      0      0     32        0
"""


def _write(path: Path, text: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(text, bytes):
        path.write_bytes(text)
    else:
        path.write_text(text)


def _process(proc: Path, pid: int, name: str, utime: int, stime: int, started: int, resident: int,
             cmdline: bytes) -> None:
    rest = ['S', '1', str(pid), str(pid), '0', '-1', '4194560', '12345', '0', '0', '0', str(utime), str(stime),
            '0', '0', '20', '0', '5', '0', str(started), '123456789', '2048']
    _write(proc / str(pid) / 'stat', f'{pid} ({name}) ' + ' '.join(rest) + '\n')
    _write(proc / str(pid) / 'statm', f'5000 {resident} 300 10 0 900 0\n')
    _write(proc / str(pid) / 'cmdline', cmdline)


def make_pi(root: Path) -> Path:
    """A folder that looks like the Pi 5's /proc, /sys and the container's /host/proc."""
    proc, sys, host = root / 'proc', root / 'sys', root / 'host' / 'proc'
    _write(proc / 'stat', STAT)
    _write(proc / 'loadavg', '2.44 2.57 2.62 3/481 12345\n')
    _write(proc / 'uptime', '86400.12 300000.50\n')
    _write(proc / 'meminfo', MEMINFO)
    _write(proc / 'sys' / 'kernel' / 'osrelease', '6.18.50+rpt-rpi-2712\n')
    _write(proc / 'diskstats', DISKSTATS)
    # The container's own network: it must not be the one read
    _write(proc / '1' / 'net' / 'dev', NET_DEV.replace('wlan0', 'eth9'))
    for name in ('mmcblk0', 'loop0', 'ram0'):
        (sys / 'block' / name).mkdir(parents=True)
    (sys / 'block' / 'mmcblk0' / 'mmcblk0p1').mkdir()

    _write(sys / 'firmware' / 'devicetree' / 'base' / 'model', b'Raspberry Pi 5 Model B Rev 1.1\x00')
    cpus = sys / 'devices' / 'system' / 'cpu'
    for n, khz in enumerate((2400000, 2400000, 1500000, 2400000)):
        _write(cpus / f'cpu{n}' / 'cpufreq' / 'scaling_cur_freq', f'{khz}\n')
        _write(cpus / f'cpu{n}' / 'cpufreq' / 'cpuinfo_max_freq', '2400000\n')
    (cpus / 'cpufreq').mkdir()
    (cpus / 'cpuidle').mkdir()

    thermal = sys / 'class' / 'thermal'
    _write(thermal / 'thermal_zone0' / 'type', 'cpu-thermal\n')
    _write(thermal / 'thermal_zone0' / 'temp', '55100\n')
    _write(thermal / 'cooling_device0' / 'type', 'pwm-fan\n')
    _write(thermal / 'cooling_device0' / 'cur_state', '1\n')
    _write(thermal / 'cooling_device0' / 'max_state', '4\n')

    hwmon = sys / 'class' / 'hwmon'
    _write(hwmon / 'hwmon0' / 'name', 'cpu_thermal\n')
    _write(hwmon / 'hwmon0' / 'temp1_input', '55100\n')
    _write(hwmon / 'hwmon1' / 'name', 'rp1_adc\n')
    for n, mv in ((1, 1477), (2, 1475), (3, 1474), (4, 1480)):
        _write(hwmon / 'hwmon1' / f'in{n}_input', f'{mv}\n')
    _write(hwmon / 'hwmon1' / 'temp1_input', '49030\n')
    _write(hwmon / 'hwmon2' / 'name', 'pwmfan\n')
    _write(hwmon / 'hwmon2' / 'fan1_input', '3416\n')
    _write(hwmon / 'hwmon2' / 'pwm1', '75\n')
    _write(hwmon / 'hwmon3' / 'name', 'rpi_volt\n')
    _write(hwmon / 'hwmon3' / 'in0_lcrit_alarm', '1\n')

    _write(host / 'stat', STAT)
    _write(host / '1' / 'net' / 'dev', NET_DEV)
    _write(host / '1' / 'net' / 'wireless', WIRELESS)
    _process(host, 1, 'systemd', 900, 400, 2, 3000, b'/sbin/init\x00splash\x00')
    _process(host, 2, 'kthreadd', 0, 15, 2, 0, b'')
    _process(host, 1234, 'python', 150000, 30000, 98765, 20000,
             b'python\x00-m\x00desktalk_detector\x00')
    _process(host, 777, 'tmux: server', 10, 20, 500, 100, b'tmux\x00')
    (host / 'self').mkdir()
    return root


def reader_for(root: Path, *, settings=SETTINGS, disk_paths=()) -> ProcReader:
    return ProcReader(settings, root=root, disk_paths=list(disk_paths), ticks_per_sec=TICKS, page_bytes=PAGE_BYTES)


@pytest.fixture
def pi(tmp_path) -> Path:
    return make_pi(tmp_path)


@pytest.fixture
def reading(pi) -> RawReading:
    return reader_for(pi).read(1762900000.0)


# ── /proc ─────────────────────────────────────────────────────────────
def test_the_processor_ticks_add_nice_to_user_and_the_interrupts_to_other(reading):
    assert reading.cpu == CpuTimes(user=10132153 + 290696, system=3084719, iowait=16683, idle=46828483,
                                   other=25195)
    assert len(reading.cores) == 4
    assert reading.cores[0] == CpuTimes(user=2533038 + 72674, system=771179, iowait=4170, idle=11707120,
                                        other=12597)


def test_load_running_and_processes_come_from_loadavg(reading):
    assert reading.load == (2.44, 2.57, 2.62)
    assert reading.running == 3
    assert reading.processes == 481


def test_uptime(reading):
    assert reading.uptime_sec == 86400.12


def test_memory_is_in_bytes_and_used_is_what_isnt_available(reading):
    m = reading.memory
    assert m.total_bytes == 8245168 * 1024
    assert m.available_bytes == 6000000 * 1024
    assert m.used_bytes == (8245168 - 6000000) * 1024
    assert m.free_bytes == 512000 * 1024
    assert m.cached_bytes == 3000000 * 1024
    assert m.buffers_bytes == 100000 * 1024
    assert m.swap_total_bytes == 204800 * 1024
    assert m.swap_used_bytes == (204800 - 200000) * 1024


def test_a_garbled_meminfo_line_is_skipped(pi):
    (pi / 'proc' / 'meminfo').write_text(MEMINFO.replace('Cached:          3000000 kB', 'Cached: lots kB')
                                         + 'nonsense\n')
    m = reader_for(pi).read(1.0).memory
    assert m.cached_bytes == 0
    assert m.total_bytes == 8245168 * 1024


def test_what_the_pi_is(reading):
    assert reading.info.model == 'Raspberry Pi 5 Model B Rev 1.1'
    assert reading.info.kernel == '6.18.50+rpt-rpi-2712'
    assert reading.info.cores == 4
    assert reading.ticks_per_sec == TICKS


def test_inside_docker_the_model_comes_from_cpuinfo(pi):
    """Docker hides /sys/firmware in a container, but the Pi also writes its model in /proc/cpuinfo."""
    (pi / 'sys' / 'firmware' / 'devicetree' / 'base' / 'model').unlink()
    _write(pi / 'proc' / 'cpuinfo', 'processor\t: 0\nBogoMIPS\t: 108.00\n\nRevision\t: d04171\n'
                                    'Model\t\t: Raspberry Pi 5 Model B Rev 1.1\n')
    assert reader_for(pi).read(1.0).info.model == 'Raspberry Pi 5 Model B Rev 1.1'


def test_only_whole_real_disks_are_counted(reading):
    assert reading.disk_io == (DiskCounters(name='mmcblk0', read_bytes=987654 * 512, write_bytes=1234568 * 512,
                                            reads=12345, writes=23456, busy_ms=54321),)


# ── /sys ──────────────────────────────────────────────────────────────
def test_processor_speed_per_core_in_mhz(reading):
    assert reading.freq_mhz == (2400.0, 2400.0, 1500.0, 2400.0)
    assert reading.max_freq_mhz == 2400.0


def test_cores_are_in_number_order_not_letter_order(tmp_path):
    cpus = tmp_path / 'sys' / 'devices' / 'system' / 'cpu'
    for n in range(12):
        _write(cpus / f'cpu{n}' / 'cpufreq' / 'scaling_cur_freq', f'{(n + 1) * 1000}\n')
    assert reader_for(tmp_path).read(1.0).freq_mhz == tuple(float(n + 1) for n in range(12))


def test_temperatures_leave_out_the_same_sensor_twice(reading):
    assert reading.temps == (Named('cpu-thermal', 55.1), Named('rp1_adc', 49.03))


def test_a_sensor_with_two_temperatures_names_each(pi):
    sensor = pi / 'sys' / 'class' / 'hwmon' / 'hwmon4'
    _write(sensor / 'name', 'nvme\n')
    _write(sensor / 'temp1_input', '40000\n')
    _write(sensor / 'temp2_input', '42500\n')
    temps = reader_for(pi).read(1.0).temps
    assert Named('nvme temp1', 40.0) in temps
    assert Named('nvme temp2', 42.5) in temps


def test_cooling_steps(reading):
    assert reading.cooling == (Cooling('pwm-fan', 1, 4),)


def test_the_fan_and_its_power(reading):
    (fan,) = reading.fans
    assert (fan.name, fan.rpm) == ('pwmfan', 3416.0)
    assert fan.pwm_pct == pytest.approx(75 / 255 * 100)


def test_voltages_in_volts(reading):
    assert reading.voltages == (Named('rp1_adc in1', 1.477), Named('rp1_adc in2', 1.475),
                                Named('rp1_adc in3', 1.474), Named('rp1_adc in4', 1.48))


def test_under_voltage_follows_the_power_sensor(pi):
    assert reader_for(pi).read(1.0).under_voltage is True
    (pi / 'sys' / 'class' / 'hwmon' / 'hwmon3' / 'in0_lcrit_alarm').write_text('0\n')
    assert reader_for(pi).read(1.0).under_voltage is False


def test_no_power_sensor_means_cant_tell(pi):
    (pi / 'sys' / 'class' / 'hwmon' / 'hwmon3' / 'name').write_text('something_else\n')
    assert reader_for(pi).read(1.0).under_voltage is None


def test_no_throttle_file_on_a_pi_5(reading):
    assert reading.throttled == ''


def test_the_throttle_file_when_there_is_one(pi):
    _write(pi / 'sys' / 'devices' / 'platform' / 'soc' / 'soc:firmware' / 'get_throttled', '50005\n')
    assert reader_for(pi).read(1.0).throttled == '0x50005'


# ── The whole Pi's /proc (/host/proc in the container) ───────────────
def test_networks_are_the_pis_without_dockers_own(reading):
    assert reading.network == (
        NetCounters('wlan0', rx_bytes=987654321, tx_bytes=123456789, rx_errors=2, tx_errors=1, rx_drops=12,
                    tx_drops=3),
        NetCounters('eth0'),
    )


def test_wifi_signal(reading):
    assert reading.wifi == (WifiNow('wlan0', 46.0, -64.0),)


def test_programs(reading):
    by_pid = {p.pid: p for p in reading.process_ticks}
    assert set(by_pid) == {1, 2, 777, 1234}
    python = by_pid[1234]
    assert python.name == 'python'
    assert python.ticks == 150000 + 30000
    assert python.started == 98765
    assert python.memory_bytes == 20000 * PAGE_BYTES
    assert python.command == 'python -m desktalk_detector'
    assert by_pid[777].name == 'tmux: server'
    assert by_pid[2].command == '[kthreadd]'


def test_a_program_name_with_a_bracket_in_it(pi):
    _process(pi / 'host' / 'proc', 4321, 'odd) (name', 7, 8, 9, 10, b'odd\x00')
    odd = next(p for p in reader_for(pi).read(1.0).process_ticks if p.pid == 4321)
    assert odd.name == 'odd) (name'
    assert odd.ticks == 15
    assert odd.started == 9


def test_a_long_command_is_cut(pi):
    settings = SETTINGS.model_copy(update={'command_chars': 10})
    python = next(p for p in reader_for(pi, settings=settings).read(1.0).process_ticks if p.pid == 1234)
    assert python.command == 'python -m '


def test_a_program_that_ended_while_being_read_is_skipped(pi):
    (pi / 'host' / 'proc' / '1234' / 'stat').unlink()
    (pi / 'host' / 'proc' / '777' / 'statm').unlink()
    pids = {p.pid for p in reader_for(pi).read(1.0).process_ticks}
    assert pids == {1, 2}


# ── Disk space ────────────────────────────────────────────────────────
def test_disk_space_for_the_folders_that_are_there(pi):
    data = pi / 'data'
    data.mkdir()
    disks = reader_for(pi, disk_paths=[('data folder', data), ('gone', pi / 'nowhere')]).read(1.0).disks
    assert [d.label for d in disks] == ['data folder']
    disk = disks[0]
    assert disk.path == str(data)
    assert disk.total_bytes > 0
    assert disk.used_bytes + disk.free_bytes <= disk.total_bytes


# ── Nothing there (a Mac, or a broken file) ──────────────────────────
def test_an_empty_folder_gives_an_empty_reading(tmp_path):
    reading = reader_for(tmp_path).read(5.0)
    assert reading == RawReading(at=5.0, ticks_per_sec=TICKS)
    assert reading.under_voltage is None


def test_garbled_files_leave_their_numbers_empty(pi):
    (pi / 'proc' / 'loadavg').write_text('not numbers\n')
    (pi / 'proc' / 'uptime').write_text('soon\n')
    (pi / 'proc' / 'stat').write_text('cpu  a b c d e f g\ncpu0 1 2\n')
    (pi / 'sys' / 'class' / 'thermal' / 'thermal_zone0' / 'temp').write_text('hot\n')
    (pi / 'sys' / 'class' / 'hwmon' / 'hwmon2' / 'pwm1').write_text('?\n')
    reading = reader_for(pi).read(1.0)
    assert reading.load == ()
    assert reading.running is None
    assert reading.uptime_sec is None
    assert reading.cpu == CpuTimes()
    assert reading.cores == ()
    assert Named('cpu-thermal', 55.1) not in reading.temps
    assert reading.fans == (Fan('pwmfan', 3416.0, None),)


def test_ticks_and_page_size_default_to_the_machines(tmp_path):
    reading = ProcReader(SETTINGS, root=tmp_path, disk_paths=[]).read(1.0)
    assert reading.ticks_per_sec == os.sysconf('SC_CLK_TCK')


def test_each_garbled_file_leaves_out_only_its_own_number(pi):
    """A half-written or odd line in any of the files is skipped; the good lines next to it are still read."""
    proc, sys, host = pi / 'proc', pi / 'sys', pi / 'host' / 'proc'
    _write(proc / 'stat', STAT + 'cpufoo 1 2 3 4\n')
    _write(proc / 'diskstats', DISKSTATS + ' 179 0 mmcblk0 1 2\n 179 0 mmcblk0 x y z 1 2 3 4 5 6 7 8\n')
    (sys / 'devices' / 'system' / 'cpu' / 'cpu3' / 'cpufreq' / 'scaling_cur_freq').unlink()
    (sys / 'devices' / 'system' / 'cpu' / 'cpu3' / 'cpufreq' / 'cpuinfo_max_freq').unlink()
    thermal, hwmon = sys / 'class' / 'thermal', sys / 'class' / 'hwmon'
    _write(thermal / 'cooling_device1' / 'cur_state', '2\n')                     # no max_state
    _write(hwmon / 'hwmon1' / 'temp2_input', 'hot\n')
    _write(hwmon / 'hwmon1' / 'in5_input', 'n/a\n')
    _write(hwmon / 'hwmon2' / 'fan2_input', 'spinning\n')
    _write(hwmon / 'hwmon4' / 'name', 'rpi_volt\n')                              # a second one, with no alarm file
    _write(sys / 'devices' / 'platform' / 'soc' / 'soc:firmware' / 'get_throttled', 'not hex\n')
    _write(sys / 'devices' / 'platform' / 'soc' / 'soc:zfirmware' / 'get_throttled', '50005\n')
    _write(host / '1' / 'net' / 'dev', NET_DEV + '  wlan9: 1 2 3\n  wlan8: a b c d e f g h i j k l m n o p\n')
    _write(host / '1' / 'net' / 'wireless', WIRELESS + ' wlan9: 0000 strong\n')
    (host / '1234' / 'cmdline').unlink()
    (host / '1234' / 'cmdline').mkdir()                                           # can't be read as a file

    reading = reader_for(pi).read(1.0)
    assert len(reading.cores) == 4
    assert [d.name for d in reading.disk_io] == ['mmcblk0'] and reading.disk_io[0].reads == 12345
    assert reading.freq_mhz == (2400.0, 2400.0, 1500.0) and reading.max_freq_mhz == 2400.0
    assert [c.name for c in reading.cooling] == ['pwm-fan']
    assert 'rp1_adc temp2' not in [t.name for t in reading.temps]
    assert [v.name for v in reading.voltages] == [f'rp1_adc in{n}' for n in (1, 2, 3, 4)]
    assert [f.name for f in reading.fans] == ['pwmfan fan1']
    assert reading.under_voltage is True
    assert reading.throttled == '0x50005'
    assert [n.name for n in reading.network] == ['wlan0', 'eth0']
    assert [w.name for w in reading.wifi] == ['wlan0']
    detector = next(p for p in reading.process_ticks if p.pid == 1234)
    assert detector.command == '[python]'
