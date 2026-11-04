"""
How the Pi is doing, sent with every picture: processor temperature, the firmware's slow-down
("throttled") flags, free disk space and how many pictures are stored.

Read from the files Linux keeps in /sys, so it costs next to nothing, and it works inside a container
without any extra device (/sys can be read in a container as it is). `root` is "/" on the Pi; tests point it
at a folder that looks like /sys.

The slow-down flags are the one thing that depends on the kernel. Older kernels (Pi 4) have the firmware's
get_throttled file with all the flags. A Pi 5 kernel doesn't: it only has the power sensor (hwmon `rpi_volt`),
which says whether the power supply is too weak and nothing else. vcgencmd would need /dev/vcio, which only root
can open, so it isn't used. When the flags can't be read the answer is '' ("can't tell"), never "0x0" ("fine").
"""

from __future__ import annotations

from pathlib import Path

from desktalk_shared.wire import PiHealth
from raspberry_pi.camera.photo_store import PhotoStore

# Where the firmware's slow-down flags are (soc/soc:firmware on a Pi 4, soc@.../soc@...:firmware on a Pi 5)
THROTTLED_FILE = 'sys/devices/platform/soc*/*firmware*/get_throttled'

# The power sensor on a Pi 5 kernel: its name, and the file that is 1 while the power supply is too weak
POWER_SENSOR_NAME = 'rpi_volt'
POWER_ALARM_FILE = 'in0_lcrit_alarm'
POWER_TOO_WEAK_NOW = 0x1       # the same flag as bit 0 of get_throttled

# The firmware's flag bits: (bit for "right now", bit for "since the Pi started", now words, earlier words)
THROTTLE_BITS = (
    (0, 16, 'the power supply is too weak', 'the power supply was too weak'),
    (1, 17, 'the processor speed is capped', 'the processor speed was capped'),
    (2, 18, 'the processor is slowed down', 'the processor was slowed down'),
    (3, 19, 'the processor is close to too hot', 'the processor was close to too hot'),
)


def throttle_words(flags: str) -> tuple[list[str], list[str]]:
    """The slow-down flags (e.g. 0x50005) in plain words: (what's happening now, what happened earlier)."""
    try:
        value = int(flags, 16)
    except ValueError:
        return [], []
    now = [words for bit, _, words, _ in THROTTLE_BITS if value >> bit & 1]
    earlier = [words for _, bit, _, words in THROTTLE_BITS if value >> bit & 1]
    return now, earlier


class HealthReader:
    def __init__(self, root: str | Path = '/'):
        self.root = Path(root)

    def cpu_temp_c(self) -> float | None:
        """The processor's temperature in °C, or None when there's no thermal sensor (not a Pi)."""
        zones = sorted(self.root.glob('sys/class/thermal/thermal_zone*'))
        cpu = [z for z in zones if 'cpu' in _read(z / 'type').lower()]
        for zone in cpu + zones:
            try:
                return round(int(_read(zone / 'temp')) / 1000, 1)
            except ValueError:
                continue
        return None

    def throttled(self) -> str:
        """The firmware's slow-down flags like 0x0 (fine) or 0x50005, or '' when it can't be read.
        With only the power sensor it is 0x1 while the power is too weak, and '' otherwise: the sensor can't say
        whether the Pi slowed itself down, so '' is not "fine"."""
        for file in sorted(self.root.glob(THROTTLED_FILE)):
            try:
                return hex(int(_read(file), 16))
            except ValueError:
                continue
        alarm = self._power_alarm()
        return hex(POWER_TOO_WEAK_NOW) if alarm else ''

    def throttle_coverage(self) -> str:
        """What throttled() can tell: 'all' (every flag), 'power' (only whether the power is too weak) or 'none'."""
        if any(_is_number(_read(file), 16) for file in self.root.glob(THROTTLED_FILE)):
            return 'all'
        return 'power' if self._power_alarm() is not None else 'none'

    def _power_alarm(self) -> bool | None:
        """Whether the power sensor says the power supply is too weak; None when there is no such sensor."""
        for sensor in sorted(self.root.glob('sys/class/hwmon/hwmon*')):
            if _read(sensor / 'name') != POWER_SENSOR_NAME:
                continue
            try:
                return bool(int(_read(sensor / POWER_ALARM_FILE)))
            except ValueError:
                continue
        return None

    def read(self, camera: str, store: PhotoStore) -> PiHealth:
        return PiHealth(camera=camera, photos_stored=store.count(), disk_free_mb=int(store.free_mb()),
                        last_cleanup_ts=store.last_cleanup, cpu_temp_c=self.cpu_temp_c(), throttled=self.throttled())


def _read(path: Path) -> str:
    try:
        return path.read_text().strip()
    except OSError:
        return ''


def _is_number(text: str, base: int) -> bool:
    try:
        int(text, base)
    except ValueError:
        return False
    return True
