"""
Reads the processor, memory, disks, network, sensors and programs from the files Linux keeps in /proc and /sys
(a SystemStatsReader, desktalk/ports.py). It costs next to nothing and needs no extra package.

Each kind of number has its own small function. A file that's missing (no fan, no Wi-Fi, a Mac) or that it can't
make sense of leaves only that number empty: read() never raises for it.

Inside the app's container, /proc/net/dev and /proc/<pid> only show the container's own network and programs, so
those two come from host_proc_folder: on the Pi that's the Pi's real /proc, mounted at /host/proc (compose.yaml).
Everything else in /proc, and all of /sys, is the whole Pi's already. `root` is "/" on the Pi; tests point it at a
folder made to look like /proc and /sys.
"""

from __future__ import annotations

import os
import re
import shutil
from collections.abc import Sequence
from pathlib import Path

from desktalk.config.app import SystemStatsSettings
from desktalk.domain import (
    Cooling,
    CpuTimes,
    DiskCounters,
    DiskSpace,
    Fan,
    MemoryNow,
    Named,
    NetCounters,
    ProcessTicks,
    RawReading,
    StatsInfo,
    WifiNow,
)

# How Linux writes its numbers (these are facts about the file formats, not settings)
KB = 1024                      # meminfo counts in kB
SECTOR_BYTES = 512             # diskstats counts sectors of 512 bytes, whatever the disk's own size
MILLI = 1000                   # temperatures in thousandths of a °C, voltages in millivolts
KHZ_PER_MHZ = 1000             # cpufreq speeds are in kHz
PWM_FULL = 255                 # a fan's pwm file goes from 0 (off) to 255 (full power)

# /proc/stat's cpu lines: user nice system idle iowait irq softirq steal (guest time is already in user)
STAT_FIELDS = 8
STAT_LEAST = 4                 # very old kernels stop after idle
# /proc/diskstats, counting from 0: name, reads, sectors read, writes, sectors written, time busy (ms)
DISK_NAME, DISK_READS, DISK_READ_SECTORS, DISK_WRITES, DISK_WRITE_SECTORS, DISK_BUSY_MS = 2, 3, 5, 7, 9, 12
# /proc/net/dev after "name:": received bytes, errors, drops; sent bytes, errors, drops
NET_RX_BYTES, NET_RX_ERRORS, NET_RX_DROPS, NET_TX_BYTES, NET_TX_ERRORS, NET_TX_DROPS = 0, 2, 3, 8, 10, 11
# /proc/net/wireless after "name:": status, link quality, signal level
WIFI_QUALITY, WIFI_LEVEL = 1, 2
# /proc/<pid>/stat after the name's last ")": field 3 (the state) is index 0, so field N is index N - 3
PROC_UTIME, PROC_STIME, PROC_STARTED = 14 - 3, 15 - 3, 22 - 3
STATM_RESIDENT = 1             # /proc/<pid>/statm: pages in memory are the second number

# Where the firmware's slow-down flags are on older kernels (the same as raspberry_pi/camera/health.py)
THROTTLED_FILE = 'devices/platform/soc*/*firmware*/get_throttled'
# The power sensor on a Pi 5 kernel, and its file that is 1 while the power supply is too weak
POWER_SENSOR_NAME = 'rpi_volt'
POWER_ALARM_FILE = 'in0_lcrit_alarm'


class ProcReader:
    def __init__(self, settings: SystemStatsSettings, *, root: Path = Path('/'),
                 disk_paths: Sequence[tuple[str, Path]], ticks_per_sec: int | None = None,
                 page_bytes: int | None = None):
        self.proc = _under(root, settings.proc_folder)
        self.sys = _under(root, settings.sys_folder)
        self.host_proc = _under(root, settings.host_proc_folder)
        self.disk_paths = tuple(disk_paths)
        self.command_chars = settings.command_chars
        self.skip_networks = tuple(settings.skip_networks)
        self.skip_disks = tuple(settings.skip_disks)
        self.ticks_per_sec = ticks_per_sec if ticks_per_sec is not None else os.sysconf('SC_CLK_TCK')
        self.page_bytes = page_bytes if page_bytes is not None else os.sysconf('SC_PAGE_SIZE')

    def _model(self) -> str:
        """What machine this is. Docker hides /sys/firmware in a container, so then the Model line of /proc/cpuinfo,
        which a Pi also writes."""
        model = _read(self.sys / 'firmware/devicetree/base/model').replace('\x00', '').strip()
        if model:
            return model
        for line in _read(self.proc / 'cpuinfo').splitlines():
            key, _, value = line.partition(':')
            if key.strip() == 'Model':
                return value.strip()
        return ''

    def read(self, now: float) -> RawReading:
        cpu, cores = self._cpu()
        load, running, processes = self._load()
        freq_mhz, max_freq_mhz = self._freq()
        zone_temps = self._thermal_zones()
        same_sensor = {t.value for t in zone_temps}
        hwmon_temps = tuple(t for t in self._hwmon_temps() if t.value not in same_sensor)
        info = StatsInfo(model=self._model(),
                         kernel=_read(self.proc / 'sys/kernel/osrelease'), cores=len(cores))
        return RawReading(
            at=now, uptime_sec=self._uptime(), cpu=cpu, cores=cores, freq_mhz=freq_mhz, max_freq_mhz=max_freq_mhz,
            load=load, running=running, processes=processes, memory=self._memory(),
            temps=zone_temps + hwmon_temps, fans=self._fans(), cooling=self._cooling(), voltages=self._voltages(),
            under_voltage=self._under_voltage(), throttled=self._throttled(), disks=self._disk_space(),
            disk_io=self._disk_io(), network=self._network(), wifi=self._wifi(), process_ticks=self._programs(),
            ticks_per_sec=self.ticks_per_sec, info=info)

    # ── /proc ─────────────────────────────────────────────────────────
    def _cpu(self) -> tuple[CpuTimes, tuple[CpuTimes, ...]]:
        """The `cpu` line (all cores together) and each `cpuN` line of /proc/stat."""
        total, cores = CpuTimes(), []
        for line in _read(self.proc / 'stat').splitlines():
            label, _, numbers = line.partition(' ')
            if not label.startswith('cpu'):
                continue
            times = _cpu_times(numbers)
            if times is None:
                continue
            if label == 'cpu':
                total = times
            elif label[3:].isdigit():
                cores.append(times)
        return total, tuple(cores)

    def _load(self) -> tuple[tuple[float, ...], int | None, int | None]:
        """/proc/loadavg: `2.44 2.57 2.62 3/481 12345` is the load over 1, 5 and 15 minutes, then running/all."""
        try:
            parts = _read(self.proc / 'loadavg').split()
            load = tuple(float(x) for x in parts[:3])
            running, processes = (int(x) for x in parts[3].split('/'))
        except (ValueError, IndexError):
            return (), None, None
        return load, running, processes

    def _uptime(self) -> float | None:
        try:
            return float(_read(self.proc / 'uptime').split()[0])
        except (ValueError, IndexError):
            return None

    def _memory(self) -> MemoryNow:
        """/proc/meminfo, in bytes. Used is what programs hold and can't give back: total minus available."""
        kb: dict[str, int] = {}
        for line in _read(self.proc / 'meminfo').splitlines():
            key, _, rest = line.partition(':')
            try:
                kb[key.strip()] = int(rest.split()[0])
            except (ValueError, IndexError):
                continue
        total, available = kb.get('MemTotal', 0), kb.get('MemAvailable')
        swap_total = kb.get('SwapTotal', 0)
        return MemoryNow(
            total_bytes=total * KB,
            used_bytes=max(total - available, 0) * KB if available is not None else 0,
            available_bytes=(available or 0) * KB,
            cached_bytes=kb.get('Cached', 0) * KB,
            buffers_bytes=kb.get('Buffers', 0) * KB,
            free_bytes=kb.get('MemFree', 0) * KB,
            swap_total_bytes=swap_total * KB,
            swap_used_bytes=max(swap_total - kb.get('SwapFree', swap_total), 0) * KB,
        )

    def _disk_io(self) -> tuple[DiskCounters, ...]:
        """/proc/diskstats for whole disks only: a whole disk has its own folder in /sys/block, a partition
        (mmcblk0p1) doesn't. Disks named like skip_disks (loop0, ram0) aren't real disks."""
        whole = {p.name for p in _list(self.sys / 'block') if p.is_dir()}
        disks = []
        for line in _read(self.proc / 'diskstats').splitlines():
            parts = line.split()
            if len(parts) <= DISK_BUSY_MS:
                continue
            name = parts[DISK_NAME]
            if name not in whole or name.startswith(self.skip_disks):
                continue
            try:
                disks.append(DiskCounters(
                    name=name, read_bytes=int(parts[DISK_READ_SECTORS]) * SECTOR_BYTES,
                    write_bytes=int(parts[DISK_WRITE_SECTORS]) * SECTOR_BYTES, reads=int(parts[DISK_READS]),
                    writes=int(parts[DISK_WRITES]), busy_ms=int(parts[DISK_BUSY_MS])))
            except ValueError:
                continue
        return tuple(disks)

    # ── /sys ──────────────────────────────────────────────────────────
    def _freq(self) -> tuple[tuple[float, ...], float | None]:
        """Each core's speed now, and the fastest it can go, in MHz."""
        speeds, tops = [], []
        for _, cpu in _numbered(self.sys / 'devices/system/cpu', 'cpu'):
            now = _number(cpu / 'cpufreq/scaling_cur_freq')
            if now is not None:
                speeds.append(now / KHZ_PER_MHZ)
            top = _number(cpu / 'cpufreq/cpuinfo_max_freq')
            if top is not None:
                tops.append(top / KHZ_PER_MHZ)
        return tuple(speeds), max(tops) if tops else None

    def _thermal_zones(self) -> tuple[Named, ...]:
        temps = []
        for _, zone in _numbered(self.sys / 'class/thermal', 'thermal_zone'):
            value = _number(zone / 'temp')
            if value is not None:
                temps.append(Named(_read(zone / 'type') or zone.name, value / MILLI))
        return tuple(temps)

    def _cooling(self) -> tuple[Cooling, ...]:
        steps = []
        for _, device in _numbered(self.sys / 'class/thermal', 'cooling_device'):
            state, top = _number(device / 'cur_state'), _number(device / 'max_state')
            if state is not None and top is not None:
                steps.append(Cooling(_read(device / 'type') or device.name, int(state), int(top)))
        return tuple(steps)

    def _sensors(self) -> list[tuple[str, Path]]:
        """Each hwmon sensor's name and folder, in number order."""
        return [(_read(folder / 'name') or folder.name, folder)
                for _, folder in _numbered(self.sys / 'class/hwmon', 'hwmon')]

    def _hwmon_temps(self) -> list[Named]:
        """Named after the sensor, or "<sensor> tempN" when the sensor has more than one."""
        temps = []
        for name, folder in self._sensors():
            inputs = _numbered(folder, 'temp', '_input')
            for n, file in inputs:
                value = _number(file)
                if value is not None:
                    temps.append(Named(name if len(inputs) == 1 else f'{name} temp{n}', value / MILLI))
        return temps

    def _fans(self) -> tuple[Fan, ...]:
        """Each fan's speed, and how much power it gets (pwmN, 0 to 255) as a percent."""
        fans = []
        for name, folder in self._sensors():
            inputs = _numbered(folder, 'fan', '_input')
            for n, file in inputs:
                rpm = _number(file)
                if rpm is None:
                    continue
                pwm = _number(folder / f'pwm{n}')
                fans.append(Fan(name if len(inputs) == 1 else f'{name} fan{n}', rpm,
                                pwm / PWM_FULL * 100 if pwm is not None else None))
        return tuple(fans)

    def _voltages(self) -> tuple[Named, ...]:
        volts = []
        for name, folder in self._sensors():
            for n, file in _numbered(folder, 'in', '_input'):
                value = _number(file)
                if value is not None:
                    volts.append(Named(f'{name} in{n}', value / MILLI))
        return tuple(volts)

    def _under_voltage(self) -> bool | None:
        """Whether the Pi's power sensor says the power supply is too weak right now; None when there's none."""
        for name, folder in self._sensors():
            if name != POWER_SENSOR_NAME:
                continue
            alarm = _number(folder / POWER_ALARM_FILE)
            if alarm is not None:
                return bool(alarm)
        return None

    def _throttled(self) -> str:
        """The firmware's slow-down flags like 0x50005, or '' when there's no such file (a Pi 5 kernel)."""
        for file in sorted(self.sys.glob(THROTTLED_FILE)):
            try:
                return hex(int(_read(file), 16))
            except ValueError:
                continue
        return ''

    # ── The whole machine's /proc ─────────────────────────────────────
    def _network(self) -> tuple[NetCounters, ...]:
        nets = []
        for name, numbers in _named_lines(self.host_proc / '1/net/dev'):
            if name.startswith(self.skip_networks):
                continue
            try:
                n = [int(x) for x in numbers]
                nets.append(NetCounters(name, rx_bytes=n[NET_RX_BYTES], tx_bytes=n[NET_TX_BYTES],
                                        rx_errors=n[NET_RX_ERRORS], tx_errors=n[NET_TX_ERRORS],
                                        rx_drops=n[NET_RX_DROPS], tx_drops=n[NET_TX_DROPS]))
            except (ValueError, IndexError):
                continue
        return tuple(nets)

    def _wifi(self) -> tuple[WifiNow, ...]:
        """/proc/net/wireless writes `46.` and `-64.`: the trailing dot goes."""
        wifi = []
        for name, numbers in _named_lines(self.host_proc / '1/net/wireless'):
            try:
                wifi.append(WifiNow(name, float(numbers[WIFI_QUALITY].rstrip('.')),
                                    float(numbers[WIFI_LEVEL].rstrip('.'))))
            except (ValueError, IndexError):
                continue
        return tuple(wifi)

    def _programs(self) -> tuple[ProcessTicks, ...]:
        """Every program running. One that ends while it's being read (its files gone) is skipped."""
        programs = []
        for folder in _list(self.host_proc):
            if folder.name.isdigit():
                program = self._program(int(folder.name), folder)
                if program is not None:
                    programs.append(program)
        return tuple(sorted(programs, key=lambda p: p.pid))

    def _program(self, pid: int, folder: Path) -> ProcessTicks | None:
        """/proc/<pid>/stat is `1234 (name) S ...`. The name may have spaces and brackets in it, so the numbers
        are the ones after its last `)`."""
        stat = _read(folder / 'stat')
        start, end = stat.find('('), stat.rfind(')')
        if start < 0 or end < start:
            return None
        name, fields = stat[start + 1:end], stat[end + 1:].split()
        try:
            ticks = int(fields[PROC_UTIME]) + int(fields[PROC_STIME])
            started = int(fields[PROC_STARTED])
            resident = int(_read(folder / 'statm').split()[STATM_RESIDENT])
        except (ValueError, IndexError):
            return None
        command = _read_bytes(folder / 'cmdline').replace(b'\x00', b' ').decode(errors='replace').strip()
        return ProcessTicks(pid=pid, started=started, name=name, command=(command or f'[{name}]')[:self.command_chars],
                            ticks=ticks, memory_bytes=resident * self.page_bytes)

    # ── Disk space ────────────────────────────────────────────────────
    def _disk_space(self) -> tuple[DiskSpace, ...]:
        disks = []
        for label, path in self.disk_paths:
            try:
                usage = shutil.disk_usage(path)
            except OSError:
                continue
            disks.append(DiskSpace(label, str(path), usage.total, usage.used, usage.free))
        return tuple(disks)


def _under(root: Path, folder: Path) -> Path:
    """A folder from the settings, under `root` (an absolute one loses its leading /)."""
    return root / folder.relative_to(folder.anchor) if folder.is_absolute() else root / folder


def _read(path: Path) -> str:
    try:
        return path.read_text(errors='replace').strip()
    except OSError:
        return ''


def _read_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError:
        return b''


def _list(folder: Path) -> list[Path]:
    try:
        return list(folder.iterdir())
    except OSError:
        return []


def _number(path: Path) -> float | None:
    """A file holding one number, or None when it's missing or isn't a number."""
    try:
        return float(_read(path))
    except ValueError:
        return None


def _numbered(folder: Path, prefix: str, suffix: str = '') -> list[tuple[int, Path]]:
    """The files or folders named prefix + a number + suffix (cpu0, hwmon3, temp1_input), in number order, so cpu10
    comes after cpu2."""
    pattern = re.compile(rf'{re.escape(prefix)}(\d+){re.escape(suffix)}')
    found = []
    for path in _list(folder):
        match = pattern.fullmatch(path.name)
        if match:
            found.append((int(match.group(1)), path))
    return sorted(found)


def _named_lines(path: Path) -> list[tuple[str, list[str]]]:
    """The `name: numbers ...` lines of /proc/net/dev and /proc/net/wireless (the headings have no colon)."""
    lines = []
    for line in _read(path).splitlines():
        name, colon, rest = line.partition(':')
        if colon:
            lines.append((name.strip(), rest.split()))
    return lines


def _cpu_times(numbers: str) -> CpuTimes | None:
    try:
        n = [float(x) for x in numbers.split()[:STAT_FIELDS]]
    except ValueError:
        return None
    if len(n) < STAT_LEAST:
        return None
    user, nice, system, idle, iowait, irq, softirq, steal = n + [0.0] * (STAT_FIELDS - len(n))
    return CpuTimes(user=user + nice, system=system, iowait=iowait, idle=idle, other=irq + softirq + steal)
