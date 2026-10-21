"""
How the Pi is doing, sent with every picture: processor temperature, the firmware's slow-down
("throttled") flags, free disk space and how many pictures are stored.

Read from the files Linux keeps in /sys, so it costs next to nothing. `root` is "/" on the Pi;
tests point it at a folder that looks like /sys.
"""

from __future__ import annotations

from pathlib import Path

from desktalk_shared.wire import PiHealth
from raspberry_pi.camera.photo_store import PhotoStore

# Where the firmware's slow-down flags are (soc/soc:firmware on a Pi 4, soc@.../soc@...:firmware on a Pi 5)
THROTTLED_FILE = 'sys/devices/platform/soc*/*firmware*/get_throttled'

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
        """The firmware's slow-down flags like 0x0 (fine) or 0x50005, or '' when it can't be read."""
        for file in sorted(self.root.glob(THROTTLED_FILE)):
            try:
                return hex(int(_read(file), 16))
            except ValueError:
                continue
        return ''

    def read(self, camera: str, store: PhotoStore) -> PiHealth:
        return PiHealth(camera=camera, photos_stored=store.count(), disk_free_mb=int(store.free_mb()),
                        last_cleanup_ts=store.last_cleanup, cpu_temp_c=self.cpu_temp_c(), throttled=self.throttled())


def _read(path: Path) -> str:
    try:
        return path.read_text().strip()
    except OSError:
        return ''
