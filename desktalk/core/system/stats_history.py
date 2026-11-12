"""
Turns the Pi's system readings into samples and keeps the last system_stats.keep_min minutes of them, for the
developer page's Pi tab (GET /api/system/stats).

    stats = StatsHistory(reader, config.system_stats)
    supervisor.add(stats)             # a PeriodicWorker: each round reads once, every system_stats.every_sec
    stats.history(since)              # the reply: the samples newer than `since`, oldest first

A reading holds counters that only go up (the processor's ticks, the bytes a disk has read), so one sample needs two
readings in a row: sample_between(before, after) says how busy and how fast things were in between. The first
reading after a start makes no sample.

Counters can still go down (the Pi restarted, a Wi-Fi adapter was reset): a rate that would be below 0 is 0. A
program is known by its pid and when it started, so a pid used again by a new program counts as new, and a program
with no reading before is left out of that sample. Two readings at the same moment, or the clock going back, make no
sample; when the clock goes back, the samples that now look like they're from the future are dropped, so the
history stays oldest first. The page asks with `since` = the newest sample it holds, which after a step back is
newer than every kept sample: then it gets all of them again, and replaces what it has.

Each sample is turned into its dict once, when it's made, so a reply (up to keep_min minutes of samples) only
picks and copies.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from typing import Any

from desktalk.config.app import SystemStatsSettings
from desktalk.domain import (
    CpuNow,
    CpuTimes,
    DiskRate,
    NetRate,
    RawReading,
    StatsInfo,
    StatsSample,
    TopProcess,
)
from desktalk.ports import SystemStatsReader

log = logging.getLogger(__name__)


def _rate(change: float, elapsed: float) -> float:
    """How much a counter went up per second; 0 when it went down."""
    return max(change, 0) / elapsed


def _cpu_percents(before: CpuTimes, after: CpuTimes) -> tuple[float, float, float, float, float]:
    """(busy, user, system, iowait, other) as a share of the ticks that went by. A part that went down counts as 0,
    and with no ticks going by everything is 0."""
    parts = [max(new - old, 0) for new, old in (
        (after.user, before.user), (after.system, before.system), (after.iowait, before.iowait),
        (after.idle, before.idle), (after.other, before.other))]
    total = sum(parts)
    if total <= 0:
        return 0.0, 0.0, 0.0, 0.0, 0.0
    user, system, iowait, _idle, other = (part / total * 100 for part in parts)
    return user + system + other, user, system, iowait, other


def _cpu(before: RawReading, after: RawReading) -> CpuNow:
    busy, user, system, iowait, other = _cpu_percents(before.cpu, after.cpu)
    cores = tuple(_cpu_percents(old, new)[0] for old, new in zip(before.cores, after.cores, strict=False))
    return CpuNow(busy_pct=busy, user_pct=user, system_pct=system, iowait_pct=iowait, other_pct=other,
                  cores_pct=cores, freq_mhz=after.freq_mhz, max_freq_mhz=after.max_freq_mhz, load=after.load,
                  running=after.running, processes=after.processes)


def _disk_io(before: RawReading, after: RawReading, elapsed: float) -> tuple[DiskRate, ...]:
    earlier = {disk.name: disk for disk in before.disk_io}
    rates = []
    for disk in after.disk_io:
        old = earlier.get(disk.name)
        if old is None:
            continue
        busy = _rate(disk.busy_ms - old.busy_ms, elapsed) / 1000 * 100
        rates.append(DiskRate(name=disk.name,
                              read_bps=_rate(disk.read_bytes - old.read_bytes, elapsed),
                              write_bps=_rate(disk.write_bytes - old.write_bytes, elapsed),
                              reads_per_sec=_rate(disk.reads - old.reads, elapsed),
                              writes_per_sec=_rate(disk.writes - old.writes, elapsed),
                              busy_pct=min(busy, 100.0)))
    return tuple(rates)


def _network(before: RawReading, after: RawReading, elapsed: float) -> tuple[NetRate, ...]:
    earlier = {net.name: net for net in before.network}
    rates = []
    for net in after.network:
        old = earlier.get(net.name)
        if old is None:
            continue
        rates.append(NetRate(name=net.name,
                             rx_bps=_rate(net.rx_bytes - old.rx_bytes, elapsed),
                             tx_bps=_rate(net.tx_bytes - old.tx_bytes, elapsed),
                             rx_errors=net.rx_errors, tx_errors=net.tx_errors,
                             rx_drops=net.rx_drops, tx_drops=net.tx_drops))
    return tuple(rates)


def _top_processes(before: RawReading, after: RawReading, elapsed: float, keep: int) -> tuple[TopProcess, ...]:
    """The `keep` busiest programs, as a share of one core like `top`; ties go to the one holding more memory."""
    if keep <= 0 or after.ticks_per_sec <= 0:
        return ()
    earlier = {(p.pid, p.started): p for p in before.process_ticks}
    found = []
    for program in after.process_ticks:
        old = earlier.get((program.pid, program.started))
        if old is None:
            continue
        share = _rate(program.ticks - old.ticks, elapsed) / after.ticks_per_sec * 100
        found.append(TopProcess(pid=program.pid, name=program.name, command=program.command, cpu_pct=share,
                                memory_bytes=program.memory_bytes))
    found.sort(key=lambda p: (p.cpu_pct, p.memory_bytes), reverse=True)
    return tuple(found[:keep])


def sample_between(before: RawReading, after: RawReading, top_processes: int) -> StatsSample | None:
    """What happened between two readings: rates per second, the processor's percents and the `top_processes`
    busiest programs. None when `after` isn't later than `before` (no time went by to divide by)."""
    elapsed = after.at - before.at
    if elapsed <= 0:
        return None
    return StatsSample(
        at=after.at,
        uptime_sec=after.uptime_sec,
        cpu=_cpu(before, after),
        memory=after.memory,
        temps=after.temps,
        fans=after.fans,
        cooling=after.cooling,
        voltages=after.voltages,
        under_voltage=after.under_voltage,
        throttled=after.throttled,
        disks=after.disks,
        disk_io=_disk_io(before, after, elapsed),
        network=_network(before, after, elapsed),
        wifi=after.wifi,
        top_processes=_top_processes(before, after, elapsed, top_processes),
    )


class StatsHistory:
    """The background job that reads the numbers every system_stats.every_sec and keeps the last
    system_stats.keep_min minutes of samples. The web layer reads history() while the job writes, so both hold
    the lock."""

    name = 'system stats'

    def __init__(self, reader: SystemStatsReader, settings: SystemStatsSettings):
        self._reader = reader
        self._settings = settings
        self._lock = threading.Lock()
        self._before: RawReading | None = None
        self._info: StatsInfo | None = None
        # Each sample as StatsSample.to_dict() (its 'at' is the sample's time), oldest first
        self._samples: deque[dict[str, Any]] = deque(
            maxlen=max(1, round(settings.keep_min * 60 / settings.every_sec)))

    def tick(self, now: float) -> float:
        if not self._settings.enabled:
            return self._settings.every_sec
        reading = self._reader.read(now)
        with self._lock:
            before, self._before, self._info = self._before, reading, reading.info
            if before is not None and reading.at < before.at:
                log.info('The clock went back %.0f seconds: starting the system numbers over from now',
                         before.at - reading.at)
                while self._samples and self._samples[-1]['at'] >= reading.at:
                    self._samples.pop()
            sample = sample_between(before, reading, self._settings.top_processes) if before is not None else None
            if sample is not None:
                self._samples.append(sample.to_dict())
        return self._settings.every_sec

    def history(self, since: float | None) -> dict[str, Any]:
        settings = self._settings
        reply: dict[str, Any] = {'collecting': settings.enabled, 'every_sec': settings.every_sec,
                                 'keep_min': settings.keep_min, 'info': {}, 'samples': []}
        if not settings.enabled:
            return reply
        with self._lock:
            info = self._info
            newest = self._samples[-1]['at'] if self._samples else None
            if since is None or newest is None or since > newest:      # after the clock went back: all of them
                kept = list(self._samples)
            else:
                kept = [sample for sample in self._samples if sample['at'] > since]
        reply['info'] = info.to_dict() if info is not None else {}
        reply['samples'] = kept
        return reply
