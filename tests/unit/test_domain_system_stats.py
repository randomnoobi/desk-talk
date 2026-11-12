"""The plain data for the Pi's system numbers (desktalk/domain/system_stats.py)."""

from __future__ import annotations

from desktalk.domain import (
    CpuNow,
    CpuTimes,
    DiskRate,
    DiskSpace,
    Fan,
    MemoryNow,
    Named,
    NetRate,
    RawReading,
    StatsInfo,
    StatsSample,
    StatsUnavailable,
    TopProcess,
    WifiNow,
)

SAMPLE_KEYS = {'at', 'uptime_sec', 'cpu', 'memory', 'temps', 'fans', 'cooling', 'voltages', 'under_voltage',
               'throttled', 'disks', 'disk_io', 'network', 'wifi', 'top_processes'}
CPU_KEYS = {'busy_pct', 'user_pct', 'system_pct', 'iowait_pct', 'other_pct', 'cores_pct', 'freq_mhz', 'max_freq_mhz',
            'load', 'running', 'processes'}
MEMORY_KEYS = {'total_bytes', 'used_bytes', 'available_bytes', 'cached_bytes', 'buffers_bytes', 'free_bytes',
               'swap_total_bytes', 'swap_used_bytes'}


def test_cpu_times_busy_leaves_out_idle_and_waiting_for_the_disk():
    times = CpuTimes(user=40, system=10, iowait=1, idle=47, other=2)
    assert times.busy == 52
    assert times.total == 100


def test_an_empty_sample_has_every_key_of_the_reply():
    d = StatsSample().to_dict()
    assert set(d) == SAMPLE_KEYS
    assert set(d['cpu']) == CPU_KEYS
    assert set(d['memory']) == MEMORY_KEYS
    assert d['under_voltage'] is None
    assert d['temps'] == [] and d['top_processes'] == []


def test_a_full_sample_turns_into_the_json_shape():
    sample = StatsSample(
        at=10.0, uptime_sec=5.0,
        cpu=CpuNow(busy_pct=53.1, cores_pct=(50.0, 60.0), freq_mhz=(2400.0, 1500.0), max_freq_mhz=2400.0,
                   load=(2.44, 2.57, 2.62), running=3, processes=481),
        memory=MemoryNow(total_bytes=8, used_bytes=3, available_bytes=5),
        temps=(Named('cpu-thermal', 55.1),), fans=(Fan('pwmfan', 3416, 29.4),), voltages=(Named('rp1_adc in1', 1.477),),
        under_voltage=True, disks=(DiskSpace('data folder', '/data', 100, 25, 75),),
        disk_io=(DiskRate('mmcblk0', read_bps=1.0, write_bps=2.0, reads_per_sec=0.5, writes_per_sec=0.25,
                          busy_pct=3.0),),
        network=(NetRate('wlan0', rx_bps=10.0, tx_bps=20.0),), wifi=(WifiNow('wlan0', 46.0, -64.0),),
        top_processes=(TopProcess(1234, 'python', 'python -m desktalk_detector', 95.0, 700),))
    d = sample.to_dict()
    assert d['cpu']['cores_pct'] == [50.0, 60.0]
    assert d['cpu']['load'] == [2.44, 2.57, 2.62]
    assert d['temps'] == [{'name': 'cpu-thermal', 'c': 55.1}]
    assert d['voltages'] == [{'name': 'rp1_adc in1', 'v': 1.477}]
    assert d['fans'] == [{'name': 'pwmfan', 'rpm': 3416, 'pwm_pct': 29.4}]
    assert d['disks'][0] == {'label': 'data folder', 'path': '/data', 'total_bytes': 100, 'used_bytes': 25,
                             'free_bytes': 75}
    assert d['wifi'] == [{'name': 'wlan0', 'quality': 46.0, 'signal_dbm': -64.0}]
    assert d['top_processes'][0] == {'pid': 1234, 'name': 'python', 'command': 'python -m desktalk_detector',
                                     'cpu_pct': 95.0, 'memory_bytes': 700}


def test_an_empty_reading_is_valid():
    reading = RawReading(at=3.0)
    assert reading.cores == () and reading.under_voltage is None and reading.info == StatsInfo()
    assert StatsInfo(model='Pi', kernel='6.18', cores=4).to_dict() == {'model': 'Pi', 'kernel': '6.18', 'cores': 4}


def test_stats_unavailable_says_the_problem():
    assert str(StatsUnavailable('no answer')) == 'no answer'
