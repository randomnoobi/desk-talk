"""
What machine this is, and how hot and busy it is.

Everything takes a `root` folder so tests can point it at a fake Pi. In the Pi's
Docker container, /sys/firmware is hidden, so compose.yaml mounts the Pi's model
name at /host/model.
"""

from __future__ import annotations

import os
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path('/')
MODEL_FILES = ('proc/device-tree/model', 'host/model')


def _read(path: Path) -> str | None:
    try:
        return path.read_text(errors='replace').strip().strip('\x00').strip()
    except OSError:
        return None


def read_cpu_temp(root: Path = ROOT) -> float | None:
    """CPU temperature in °C (Linux / Pi). None where it can't be read without admin rights (macOS)."""
    zones = sorted((root / 'sys/class/thermal').glob('thermal_zone*'))
    cpu = [z for z in zones if any(k in (_read(z / 'type') or '') for k in ('cpu', 'soc', 'x86_pkg'))]
    for zone in cpu or zones[:1]:
        try:
            return int(_read(zone / 'temp') or '') / 1000
        except ValueError:
            return None
    return None


def read_throttled(root: Path = ROOT) -> str | None:
    """The Pi's throttling flags, e.g. 0x0 (fine) or 0x50005 (too hot / low voltage, now and earlier)."""
    text = _read(root / 'sys/devices/platform/soc/soc:firmware/get_throttled')
    try:
        return hex(int(text, 16)) if text else None
    except ValueError:
        return None


def hardware_name(root: Path = ROOT) -> str:
    for rel in MODEL_FILES:
        text = _read(root / rel)
        if text:
            return text
    if root == ROOT and sys.platform == 'darwin':
        try:
            return subprocess.run(['sysctl', '-n', 'machdep.cpu.brand_string'], capture_output=True, text=True,
                                  check=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            pass
    return platform.processor() or platform.machine()


def device_label(root: Path = ROOT) -> str:
    return 'pi' if 'Raspberry Pi' in hardware_name(root) else 'laptop'


def peak_memory_mb(ru_maxrss: int, system: str = sys.platform) -> float:
    """resource.getrusage().ru_maxrss in MB: macOS reports bytes, Linux reports KiB."""
    return ru_maxrss / (1024 * 1024) if system == 'darwin' else ru_maxrss / 1024


def git_commit(repo: Path) -> str | None:
    """The current commit, read straight from .git (git itself isn't in the Docker image)."""
    git = repo / '.git'
    head = _read(git / 'HEAD')
    if not head:
        return None
    if not head.startswith('ref: '):
        return head
    ref = head[5:]
    sha = _read(git / ref)
    if sha:
        return sha
    for line in (_read(git / 'packed-refs') or '').splitlines():
        if line.endswith(' ' + ref):
            return line.split()[0]
    return None


def memory_total_mb() -> float | None:
    try:
        import psutil
        return round(psutil.virtual_memory().total / (1024 * 1024))
    except Exception:
        return None


def environment(repo: Path, root: Path = ROOT) -> dict:
    """Facts about this machine and the software versions, saved next to each run's results."""
    versions = {}
    for name in ('ultralytics', 'torch', 'ncnn', 'onnxruntime', 'onnx', 'numpy', 'cv2'):
        try:
            versions[name] = getattr(__import__(name), '__version__', '?')
        except Exception:
            versions[name] = None
    return {
        'hardware': hardware_name(root),
        'cpu_count': os.cpu_count(),
        'memory_total_mb': memory_total_mb(),
        'os': platform.platform(),
        'python': platform.python_version(),
        'in_docker': (root / '.dockerenv').exists(),
        'commit': git_commit(repo),
        'versions': versions,
    }
