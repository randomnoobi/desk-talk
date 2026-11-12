"""StatsHistory and sample_between (desktalk/core/system/stats_history.py): two readings in a row become one sample
of rates and percents, and the job keeps the last system_stats.keep_min minutes of them."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from desktalk.config import load_config
from desktalk.config.app import SystemStatsSettings
from desktalk.core.system.stats_history import StatsHistory, sample_between
from desktalk.domain import (
    CpuTimes,
    DiskCounters,
    MemoryNow,
    Named,
    NetCounters,
    ProcessTicks,
    RawReading,
    StatsInfo,
    StatsSample,
)
from tests.fakes import FakeStatsReader

SETTINGS: SystemStatsSettings = load_config(environ={}, dotenv=Path('/nonexistent')).system_stats


def settings(**changes) -> SystemStatsSettings:
    return SETTINGS.model_copy(update={'enabled': True, 'every_sec': 2.0, 'keep_min': 30.0, **changes})


def process(pid: int, ticks: float, *, started: int = 10, name: str = 'python', memory: int = 1000) -> ProcessTicks:
    return ProcessTicks(pid=pid, started=started, name=name, command=f'{name} -m thing', ticks=ticks,
                        memory_bytes=memory)


BEFORE = RawReading(
    at=100.0,
    uptime_sec=5000.0,
    cpu=CpuTimes(user=1000, system=500, iowait=100, idle=8000, other=50),
    cores=(CpuTimes(user=500, idle=4000), CpuTimes(user=500, idle=4000)),
    disk_io=(DiskCounters('mmcblk0', read_bytes=1_000_000, write_bytes=2_000_000, reads=100, writes=200,
                          busy_ms=10_000),),
    network=(NetCounters('wlan0', rx_bytes=50_000, tx_bytes=20_000, rx_errors=1, tx_drops=2),),
    process_ticks=(process(1, 100), process(2, 500, name='detector'), process(3, 50, name='sleepy')),
    ticks_per_sec=100,
)

# 2 seconds later: 200 ticks went by on the whole processor (100 a second × 2 cores, say).
AFTER = RawReading(
    at=102.0,
    uptime_sec=5002.0,
    cpu=CpuTimes(user=1080, system=520, iowait=110, idle=8180, other=60),   # +80 +20 +10 +180 +10 = 300
    cores=(CpuTimes(user=550, idle=4050), CpuTimes(user=500, idle=4100)),   # core 0: 50 of 100, core 1: 0 of 100
    freq_mhz=(2400.0, 1500.0),
    max_freq_mhz=2400.0,
    load=(2.44, 2.57, 2.62),
    running=3,
    processes=481,
    memory=MemoryNow(total_bytes=8000, used_bytes=3000, available_bytes=5000),
    temps=(Named('cpu-thermal', 55.1),),
    under_voltage=True,
    disk_io=(DiskCounters('mmcblk0', read_bytes=1_400_000, write_bytes=2_200_000, reads=140, writes=210,
                          busy_ms=10_500),),
    network=(NetCounters('wlan0', rx_bytes=60_000, tx_bytes=24_000, rx_errors=3, tx_drops=2),),
    process_ticks=(process(1, 120), process(2, 690, name='detector'), process(3, 50, name='sleepy'),
                   process(9, 400, name='brand new')),
    ticks_per_sec=100,
    info=StatsInfo(model='Raspberry Pi 5 Model B Rev 1.1', kernel='6.18.50', cores=2),
)


def made(before: RawReading, after: RawReading, top: int = 8) -> StatsSample:
    """sample_between, for a pair that must make a sample."""
    sample = sample_between(before, after, top_processes=top)
    assert sample is not None
    return sample


# ── sample_between ───────────────────────────────────────────────────
def test_two_readings_two_seconds_apart_give_the_right_rates():
    sample = sample_between(BEFORE, AFTER, top_processes=8)
    assert sample is not None
    assert sample.at == 102.0
    assert sample.uptime_sec == 5002.0

    disk = sample.disk_io[0]
    assert (disk.name, disk.read_bps, disk.write_bps) == ('mmcblk0', 200_000.0, 100_000.0)
    assert (disk.reads_per_sec, disk.writes_per_sec) == (20.0, 5.0)
    assert disk.busy_pct == pytest.approx(25.0)             # 500 ms busy of 2000

    net = sample.network[0]
    assert (net.name, net.rx_bps, net.tx_bps) == ('wlan0', 5_000.0, 2_000.0)
    assert (net.rx_errors, net.tx_drops) == (3, 2)          # since the Pi started, as they are now


def test_processor_percents_are_each_parts_share_of_the_ticks():
    cpu = made(BEFORE, AFTER).cpu
    assert cpu.user_pct == pytest.approx(80 / 300 * 100)
    assert cpu.system_pct == pytest.approx(20 / 300 * 100)
    assert cpu.iowait_pct == pytest.approx(10 / 300 * 100)
    assert cpu.other_pct == pytest.approx(10 / 300 * 100)
    assert cpu.busy_pct == pytest.approx(110 / 300 * 100)   # user + system + other
    assert cpu.cores_pct == pytest.approx((50.0, 0.0))
    assert (cpu.freq_mhz, cpu.max_freq_mhz, cpu.load) == ((2400.0, 1500.0), 2400.0, (2.44, 2.57, 2.62))
    assert (cpu.running, cpu.processes) == (3, 481)


def test_what_is_not_a_rate_is_the_newer_readings():
    sample = sample_between(BEFORE, AFTER, top_processes=8)
    assert sample is not None
    assert sample.memory == AFTER.memory
    assert sample.temps == AFTER.temps
    assert sample.under_voltage is True


def test_the_busiest_programs_come_first_as_a_share_of_one_core():
    top = made(BEFORE, AFTER).top_processes
    assert [(p.pid, p.cpu_pct) for p in top] == [(2, pytest.approx(95.0)), (1, pytest.approx(10.0)),
                                                 (3, pytest.approx(0.0))]
    assert top[0].name == 'detector' and top[0].command == 'detector -m thing' and top[0].memory_bytes == 1000


def test_a_program_with_no_reading_before_is_left_out():
    top = made(BEFORE, AFTER).top_processes
    assert 9 not in [p.pid for p in top]


def test_only_the_top_few_programs_are_kept_and_ties_go_to_the_bigger_one():
    before = RawReading(at=0.0, process_ticks=(process(1, 0, memory=10), process(2, 0, memory=30),
                                               process(3, 0, memory=20)))
    after = RawReading(at=1.0, process_ticks=(process(1, 50, memory=10), process(2, 50, memory=30),
                                              process(3, 50, memory=20)))
    top = made(before, after, top=2).top_processes
    assert [p.pid for p in top] == [2, 3]
    assert made(before, after, top=0).top_processes == ()


# Review Focus 1: a counter that goes down (a reboot, a reset Wi-Fi adapter, a reused pid) makes no negative rate.
def test_a_counter_that_went_down_gives_zero_not_a_negative_rate():
    rebooted = RawReading(
        at=102.0,
        cpu=CpuTimes(user=10, system=5, iowait=1, idle=80, other=0),
        cores=(CpuTimes(user=5, idle=40), CpuTimes(user=5, idle=40)),
        disk_io=(DiskCounters('mmcblk0', read_bytes=10, write_bytes=20, reads=1, writes=2, busy_ms=5),),
        network=(NetCounters('wlan0', rx_bytes=100, tx_bytes=50),),
        process_ticks=(process(1, 3),),
    )
    sample = sample_between(BEFORE, rebooted, top_processes=8)
    assert sample is not None
    assert (sample.cpu.busy_pct, sample.cpu.user_pct, sample.cpu.system_pct) == (0.0, 0.0, 0.0)
    assert (sample.cpu.iowait_pct, sample.cpu.other_pct) == (0.0, 0.0)
    assert sample.cpu.cores_pct == (0.0, 0.0)
    disk = sample.disk_io[0]
    assert (disk.read_bps, disk.write_bps, disk.reads_per_sec, disk.writes_per_sec, disk.busy_pct) == (0, 0, 0, 0, 0)
    assert (sample.network[0].rx_bps, sample.network[0].tx_bps) == (0.0, 0.0)
    assert [p.cpu_pct for p in sample.top_processes] == [0.0]


def test_one_part_that_went_down_while_the_total_went_up_counts_as_zero():
    after = RawReading(at=102.0, cpu=CpuTimes(user=1200, system=400, iowait=100, idle=8000, other=50))
    cpu = made(BEFORE, after).cpu
    assert cpu.system_pct == 0.0
    assert cpu.user_pct == pytest.approx(100.0)


def test_a_reused_pid_with_a_new_start_time_is_a_new_program_and_left_out():
    after = RawReading(at=102.0, process_ticks=(process(2, 9000, started=999, name='someone else'),))
    sample = sample_between(BEFORE, after, top_processes=8)
    assert sample is not None
    assert sample.top_processes == ()


def test_a_disk_or_network_that_was_not_there_before_is_left_out():
    after = RawReading(at=102.0, disk_io=(DiskCounters('sda', read_bytes=5),), network=(NetCounters('eth0'),))
    sample = sample_between(BEFORE, after, top_processes=8)
    assert sample is not None
    assert (sample.disk_io, sample.network) == ((), ())


def test_disk_busy_is_at_most_100():
    after = RawReading(at=102.0, disk_io=(DiskCounters('mmcblk0', busy_ms=10_000 + 2_500),))
    assert made(BEFORE, after).disk_io[0].busy_pct == 100.0


def test_no_ticks_going_by_gives_zero_percents():
    after = RawReading(at=102.0, cpu=BEFORE.cpu, cores=BEFORE.cores)
    cpu = made(BEFORE, after).cpu
    assert (cpu.busy_pct, cpu.cores_pct) == (0.0, (0.0, 0.0))


# Review Focus 2: two readings at the same moment (or the clock stepping back) never divide by zero.
@pytest.mark.parametrize('at', [100.0, 99.0])
def test_readings_at_the_same_moment_or_earlier_make_no_sample(at):
    assert sample_between(BEFORE, RawReading(at=at, cpu=AFTER.cpu), top_processes=8) is None


def test_empty_readings_make_an_empty_sample():
    sample = sample_between(RawReading(at=1.0), RawReading(at=3.0), top_processes=8)
    assert sample is not None
    assert sample.at == 3.0
    assert sample.cpu.busy_pct == 0.0
    assert (sample.disk_io, sample.network, sample.top_processes) == ((), (), ())


# ── StatsHistory ─────────────────────────────────────────────────────
def test_it_is_called_system_stats():
    assert StatsHistory(FakeStatsReader(), settings()).name == 'system stats'


def test_a_tick_returns_every_sec():
    stats = StatsHistory(FakeStatsReader(), settings(every_sec=5.0))
    assert stats.tick(100.0) == 5.0


def test_the_first_reading_makes_no_sample_and_the_second_does():
    reader = FakeStatsReader([BEFORE, AFTER])
    stats = StatsHistory(reader, settings())
    stats.tick(100.0)
    assert stats.history(None)['samples'] == []
    stats.tick(102.0)
    samples = stats.history(None)['samples']
    assert [s['at'] for s in samples] == [102.0]
    assert samples[0]['disk_io'][0]['read_bps'] == 200_000.0
    assert reader.asked == [100.0, 102.0]


def test_the_reply_has_the_settings_and_the_newest_info():
    stats = StatsHistory(FakeStatsReader([BEFORE, AFTER]), settings(every_sec=2.0, keep_min=30.0))
    assert stats.history(None)['info'] == {}
    stats.tick(100.0)
    stats.tick(102.0)
    reply = stats.history(None)
    assert (reply['collecting'], reply['every_sec'], reply['keep_min']) == (True, 2.0, 30.0)
    assert reply['info'] == {'model': 'Raspberry Pi 5 Model B Rev 1.1', 'kernel': '6.18.50', 'cores': 2}


def test_readings_at_the_same_moment_keep_no_sample():
    stats = StatsHistory(FakeStatsReader([BEFORE]), settings())
    stats.tick(100.0)
    stats.tick(100.0)
    assert stats.history(None)['samples'] == []


def test_it_keeps_only_keep_min_of_samples():
    # 1 minute every 20 seconds: 3 samples.
    stats = StatsHistory(FakeStatsReader(), settings(every_sec=20.0, keep_min=1.0))
    for n in range(10):
        stats.tick(1000.0 + 20 * n)
    assert [s['at'] for s in stats.history(None)['samples']] == [1140.0, 1160.0, 1180.0]


def test_it_keeps_at_least_one_sample():
    stats = StatsHistory(FakeStatsReader(), settings(every_sec=60.0, keep_min=0.1))
    for n in range(4):
        stats.tick(1000.0 + 60 * n)
    assert [s['at'] for s in stats.history(None)['samples']] == [1180.0]


def test_since_keeps_only_newer_samples_oldest_first():
    stats = StatsHistory(FakeStatsReader(), settings())
    for n in range(5):
        stats.tick(100.0 + 2 * n)
    assert [s['at'] for s in stats.history(None)['samples']] == [102.0, 104.0, 106.0, 108.0]
    assert [s['at'] for s in stats.history(104.0)['samples']] == [106.0, 108.0]
    assert stats.history(108.0)['samples'] == []


def test_when_the_clock_steps_back_samples_from_the_future_are_dropped():
    stats = StatsHistory(FakeStatsReader(), settings())
    for at in (100.0, 102.0, 104.0, 106.0):
        stats.tick(at)
    stats.tick(50.0)                     # the clock went back: no sample, and 102 ... 106 are now "in the future"
    stats.tick(52.0)
    assert [s['at'] for s in stats.history(None)['samples']] == [52.0]


def test_after_the_clock_steps_back_a_page_asking_since_its_newest_gets_everything_again():
    """The page asks with since = the newest sample it holds. After the clock steps back, every new sample is older
    than that, so it gets all kept samples (and replaces what it has) instead of nothing for as long as the step."""
    stats = StatsHistory(FakeStatsReader(), settings())
    for at in (100.0, 102.0, 104.0, 106.0):
        stats.tick(at)
    page_since = stats.history(None)['samples'][-1]['at']
    for at in (50.0, 52.0, 54.0):
        stats.tick(at)
    assert [s['at'] for s in stats.history(page_since)['samples']] == [52.0, 54.0]
    assert [s['at'] for s in stats.history(52.0)['samples']] == [54.0]
    assert stats.history(54.0)['samples'] == []


def test_each_sample_is_turned_into_its_dict_only_once(monkeypatch):
    """The first reply can hold system_stats.keep_min minutes of samples: the Pi turns each into a dict once, when
    it's made, not again for every reply."""
    made_dicts: list[float] = []
    to_dict = StatsSample.to_dict

    def counting(sample: StatsSample):
        made_dicts.append(sample.at)
        return to_dict(sample)

    monkeypatch.setattr(StatsSample, 'to_dict', counting)
    stats = StatsHistory(FakeStatsReader(), settings())
    for n in range(4):
        stats.tick(100.0 + 2 * n)
    for _ in range(3):
        stats.history(None)
    stats.history(102.0)
    assert made_dicts == [102.0, 104.0, 106.0]


def test_changing_a_reply_list_does_not_change_what_is_kept():
    stats = StatsHistory(FakeStatsReader(), settings())
    for n in range(3):
        stats.tick(100.0 + 2 * n)
    stats.history(None)['samples'].clear()
    assert [s['at'] for s in stats.history(None)['samples']] == [102.0, 104.0]


def test_when_switched_off_it_reads_nothing_and_says_so():
    reader = FakeStatsReader([BEFORE, AFTER])
    stats = StatsHistory(reader, settings(enabled=False, every_sec=4.0))
    assert stats.tick(100.0) == 4.0
    assert stats.tick(104.0) == 4.0
    assert reader.asked == []
    reply = stats.history(None)
    assert (reply['collecting'], reply['info'], reply['samples']) == (False, {}, [])
    assert (reply['every_sec'], reply['keep_min']) == (4.0, 30.0)


def test_reading_while_the_job_writes_is_safe():
    stats = StatsHistory(FakeStatsReader(), settings(every_sec=1.0, keep_min=1.0))
    errors: list[Exception] = []

    def read_a_lot():
        try:
            for _ in range(2000):
                stats.history(None)
        except Exception as error:      # noqa: BLE001 - any error at all fails the test
            errors.append(error)

    reader = threading.Thread(target=read_a_lot)
    reader.start()
    for n in range(2000):
        stats.tick(float(n))
    reader.join()
    assert errors == []
