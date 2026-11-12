"""
The system numbers of the machine the app runs on (on the Pi: the Pi's), for the developer page's Pi tab.

Two kinds: a RawReading is what Linux's files say at one moment (counters that only go up, like the processor's
ticks or the bytes sent), and a StatsSample is what two readings in a row mean (how busy, how fast, per second).
desktalk/adapters/system/ reads them, desktalk/core/system/ turns them into samples and keeps the last few.

    CpuTimes, MemoryNow, Named, Fan, Cooling, DiskSpace, DiskCounters, NetCounters, WifiNow, ProcessTicks,
    StatsInfo, RawReading             one reading
    CpuNow, DiskRate, NetRate, TopProcess, StatsSample
                                      one sample (to_dict(): the shape GET /api/system/stats sends)
    StatsUnavailable                  the Pi's numbers couldn't be fetched (the computer's line to the Pi)
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


class StatsUnavailable(Exception):
    """The Pi didn't answer, or didn't send its numbers. The message says what went wrong, in plain words."""


# ── One reading ──────────────────────────────────────────────────────
@dataclass(frozen=True)
class CpuTimes:
    """The processor's ticks since the Pi started, for all cores together or one: user (with nice), system,
    waiting for the disk, idle, and the rest (interrupts, steal)."""

    user: float = 0.0
    system: float = 0.0
    iowait: float = 0.0
    idle: float = 0.0
    other: float = 0.0

    @property
    def total(self) -> float:
        return self.user + self.system + self.iowait + self.idle + self.other

    @property
    def busy(self) -> float:
        """Ticks spent working: not idle and not waiting for the disk."""
        return self.user + self.system + self.other


@dataclass(frozen=True)
class MemoryNow:
    total_bytes: int = 0
    used_bytes: int = 0                 # total - available: what programs hold and can't give back
    available_bytes: int = 0            # what programs could still get, with the cache given back
    cached_bytes: int = 0
    buffers_bytes: int = 0
    free_bytes: int = 0                 # not used at all, not even for the cache
    swap_total_bytes: int = 0
    swap_used_bytes: int = 0


@dataclass(frozen=True)
class Named:
    """One sensor's number: a temperature in °C or a voltage in volts."""

    name: str
    value: float


@dataclass(frozen=True)
class Fan:
    name: str
    rpm: float
    pwm_pct: float | None = None        # how much power the fan gets, 0 to 100 (None: not said)


@dataclass(frozen=True)
class Cooling:
    """A cooling step Linux sets, like the Pi's fan speed level (state 1 of max_state 4)."""

    name: str
    state: int
    max_state: int


@dataclass(frozen=True)
class DiskSpace:
    label: str
    path: str
    total_bytes: int
    used_bytes: int
    free_bytes: int


@dataclass(frozen=True)
class DiskCounters:
    """A whole disk's counters since the Pi started (/proc/diskstats)."""

    name: str
    read_bytes: int = 0
    write_bytes: int = 0
    reads: int = 0
    writes: int = 0
    busy_ms: int = 0                    # time the disk had something to do


@dataclass(frozen=True)
class NetCounters:
    """A network's counters since the Pi started (/proc/net/dev)."""

    name: str
    rx_bytes: int = 0
    tx_bytes: int = 0
    rx_errors: int = 0
    tx_errors: int = 0
    rx_drops: int = 0
    tx_drops: int = 0


@dataclass(frozen=True)
class WifiNow:
    name: str
    quality: float                      # the driver's own link quality (on the Pi, out of 70)
    signal_dbm: float                   # e.g. -64: closer to 0 is stronger


@dataclass(frozen=True)
class ProcessTicks:
    """One program running now, and the processor ticks it has used since it started."""

    pid: int
    started: int                        # when it started, in ticks after boot: a reused pid gets a new one
    name: str
    command: str
    ticks: float
    memory_bytes: int


@dataclass(frozen=True)
class StatsInfo:
    """What doesn't change while the Pi runs."""

    model: str = ''                     # e.g. Raspberry Pi 5 Model B Rev 1.1
    kernel: str = ''
    cores: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RawReading:
    """What Linux's files said at `at` (seconds since 1970). Anything it couldn't read is left empty or None."""

    at: float
    uptime_sec: float | None = None
    cpu: CpuTimes = field(default_factory=CpuTimes)
    cores: tuple[CpuTimes, ...] = ()
    freq_mhz: tuple[float, ...] = ()
    max_freq_mhz: float | None = None
    load: tuple[float, ...] = ()        # 1, 5 and 15 minutes
    running: int | None = None          # programs running right now
    processes: int | None = None        # programs there are
    memory: MemoryNow = field(default_factory=MemoryNow)
    temps: tuple[Named, ...] = ()
    fans: tuple[Fan, ...] = ()
    cooling: tuple[Cooling, ...] = ()
    voltages: tuple[Named, ...] = ()
    under_voltage: bool | None = None   # the Pi says its power supply is too weak right now (None: can't tell)
    throttled: str = ''                 # the firmware's slow-down flags, like 0x0, or '' when it can't be read
    disks: tuple[DiskSpace, ...] = ()
    disk_io: tuple[DiskCounters, ...] = ()
    network: tuple[NetCounters, ...] = ()
    wifi: tuple[WifiNow, ...] = ()
    process_ticks: tuple[ProcessTicks, ...] = ()
    ticks_per_sec: int = 100
    info: StatsInfo = field(default_factory=StatsInfo)


# ── One sample: two readings in a row ────────────────────────────────
@dataclass(frozen=True)
class CpuNow:
    busy_pct: float = 0.0
    user_pct: float = 0.0
    system_pct: float = 0.0
    iowait_pct: float = 0.0
    other_pct: float = 0.0
    cores_pct: tuple[float, ...] = ()
    freq_mhz: tuple[float, ...] = ()
    max_freq_mhz: float | None = None
    load: tuple[float, ...] = ()
    running: int | None = None
    processes: int | None = None


@dataclass(frozen=True)
class DiskRate:
    name: str
    read_bps: float = 0.0               # bytes a second
    write_bps: float = 0.0
    reads_per_sec: float = 0.0
    writes_per_sec: float = 0.0
    busy_pct: float = 0.0               # how much of the time the disk had something to do


@dataclass(frozen=True)
class NetRate:
    name: str
    rx_bps: float = 0.0                 # bytes a second received
    tx_bps: float = 0.0                 # and sent
    rx_errors: int = 0                  # these four: since the Pi started
    tx_errors: int = 0
    rx_drops: int = 0
    tx_drops: int = 0


@dataclass(frozen=True)
class TopProcess:
    pid: int
    name: str
    command: str
    cpu_pct: float                      # of one core, like `top`: up to 100 × the number of cores
    memory_bytes: int


@dataclass(frozen=True)
class StatsSample:
    at: float = 0.0
    uptime_sec: float | None = None
    cpu: CpuNow = field(default_factory=CpuNow)
    memory: MemoryNow = field(default_factory=MemoryNow)
    temps: tuple[Named, ...] = ()
    fans: tuple[Fan, ...] = ()
    cooling: tuple[Cooling, ...] = ()
    voltages: tuple[Named, ...] = ()
    under_voltage: bool | None = None
    throttled: str = ''
    disks: tuple[DiskSpace, ...] = ()
    disk_io: tuple[DiskRate, ...] = ()
    network: tuple[NetRate, ...] = ()
    wifi: tuple[WifiNow, ...] = ()
    top_processes: tuple[TopProcess, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """The shape GET /api/system/stats sends (tuples become lists; a temperature is {name, c}, a voltage
        {name, v})."""
        d = asdict(self)
        d['cpu'] = {key: list(value) if isinstance(value, tuple) else value for key, value in d['cpu'].items()}
        d['temps'] = [{'name': t.name, 'c': t.value} for t in self.temps]
        d['voltages'] = [{'name': v.name, 'v': v.value} for v in self.voltages]
        for key in ('fans', 'cooling', 'disks', 'disk_io', 'network', 'wifi', 'top_processes'):
            d[key] = list(d[key])
        return d
