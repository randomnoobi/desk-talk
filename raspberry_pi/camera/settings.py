"""
The camera program's settings: the `pi:` section of config/settings.yaml. Later wins:

    config/settings.yaml in this repo on the Pi  →  the computer's settings (/api/pi/config)  →  flags

So settings only need changing on the computer: the Pi asks for them when it starts, and again every
pi.settings_check_every_sec. New detector settings go to the container right away; anything else
needs a restart of the camera program (it says so in its log).
"""

from __future__ import annotations

import dataclasses
import json
import logging
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from desktalk_shared.detector_api import ModelSettings
from desktalk_shared.wire import WireError
from raspberry_pi.camera import net
from raspberry_pi.camera.clock import Clock
from raspberry_pi.camera.detector_client import DetectorClient, DetectorConfig

log = logging.getLogger(__name__)


class SettingsError(ValueError):
    """A setting is missing or has a bad value. The text names it."""


class ComputerProblem(Exception):
    """The computer can't be reached or said no. The text says why, in plain words."""


@dataclass(frozen=True, kw_only=True)
class CameraSettings:
    """pi: in config/settings.yaml (what each one means is in the comments there)."""

    fps: float
    width: int
    height: int
    jpeg_quality: int
    photo_dir: str
    delete_every_min: float
    keep_last_min: float
    min_free_mb: float
    upload_timeout_sec: float
    upload_retry_start_sec: float
    upload_retry_max_sec: float
    hot_warning_c: float
    camera_warmup_sec: float
    pause_after_error_sec: float
    status_every_sec: float
    health_every_sec: float
    settings_check_every_sec: float
    restart_after_missed_pictures: int
    detector: DetectorConfig


# ── what each setting may be ──────────────────────────────────────────
@dataclass(frozen=True)
class _Kind:
    words: str
    ok: Callable[[Any], bool]
    whole: bool = False


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


POSITIVE = _Kind('a number above 0', lambda v: _number(v) and v > 0)
ZERO_OR_MORE = _Kind('a number, 0 or more', lambda v: _number(v) and v >= 0)
WHOLE = _Kind('a whole number above 0', lambda v: _number(v) and v == int(v) and v > 0, whole=True)
QUALITY = _Kind('a whole number from 1 to 100', lambda v: _number(v) and v == int(v) and 1 <= v <= 100, whole=True)
TEXT = _Kind('text', lambda v: isinstance(v, str) and v != '')

FIELDS = {
    'fps': POSITIVE, 'width': WHOLE, 'height': WHOLE, 'jpeg_quality': QUALITY, 'photo_dir': TEXT,
    'delete_every_min': ZERO_OR_MORE, 'keep_last_min': ZERO_OR_MORE, 'min_free_mb': ZERO_OR_MORE,
    'upload_timeout_sec': POSITIVE, 'upload_retry_start_sec': POSITIVE, 'upload_retry_max_sec': POSITIVE,
    'hot_warning_c': POSITIVE, 'camera_warmup_sec': ZERO_OR_MORE, 'pause_after_error_sec': ZERO_OR_MORE,
    'status_every_sec': POSITIVE, 'health_every_sec': POSITIVE, 'settings_check_every_sec': POSITIVE,
    'restart_after_missed_pictures': WHOLE,
}
DETECTOR_FIELDS = {
    'url': TEXT, 'wait_per_picture_sec': POSITIVE, 'failures_before_pause': WHOLE, 'pause_start_sec': POSITIVE,
    'pause_max_sec': POSITIVE, 'health_check_every_sec': POSITIVE,
}
MODEL_FIELDS = ('model', 'format', 'image_size', 'confidence', 'half')
IN_ORDER = (('upload_retry_start_sec', 'upload_retry_max_sec'),)        # (smaller, larger)
DETECTOR_IN_ORDER = (('pause_start_sec', 'pause_max_sec'),)


def merge(*layers: Mapping[str, Any] | None) -> dict[str, Any]:
    """Later layers win, key by key (sections like detector: are merged too)."""
    out: dict[str, Any] = {}
    for layer in layers:
        for key, value in (layer or {}).items():
            if isinstance(value, Mapping) and isinstance(out.get(key), Mapping):
                out[key] = merge(out[key], value)
            else:
                out[key] = value
    return out


def from_layers(*layers: Mapping[str, Any] | None) -> CameraSettings:
    """The settings from `pi:` sections, later ones winning. Raises SettingsError naming a bad or missing one."""
    pi = merge(*layers)
    detector = pi.get('detector')
    if not isinstance(detector, Mapping):
        raise SettingsError('pi.detector is missing' + _WHERE)
    values = _check(pi, FIELDS, 'pi.', IN_ORDER)
    values['detector'] = DetectorConfig(model=_model(detector),
                                        **_check(detector, DETECTOR_FIELDS, 'pi.detector.', DETECTOR_IN_ORDER))
    return CameraSettings(**values)


def upload_timeout(pi: Mapping[str, Any]) -> float:
    """pi.upload_timeout_sec on its own: needed to ask the computer for the rest."""
    return _check(pi, {'upload_timeout_sec': POSITIVE}, 'pi.', ())['upload_timeout_sec']


_WHERE = ': it belongs in config/settings.yaml on the computer (git pull gets the newest one, on the Pi too)'


def _check(section: Mapping[str, Any], fields: Mapping[str, _Kind], where: str,
           in_order: tuple[tuple[str, str], ...]) -> dict[str, Any]:
    values = {}
    for name, kind in fields.items():
        if name not in section or section[name] is None:
            raise SettingsError(f'{where}{name} is missing{_WHERE}')
        value = section[name]
        if not kind.ok(value):
            raise SettingsError(f'{where}{name} must be {kind.words} (got {value!r})')
        values[name] = int(value) if kind.whole else value
    for small, large in in_order:
        if values[small] > values[large]:
            raise SettingsError(f'{where}{small} ({values[small]:g}) must not be more than '
                                f'{where}{large} ({values[large]:g})')
    return values


def _model(detector: Mapping[str, Any]) -> ModelSettings:
    for name in MODEL_FIELDS:
        if name not in detector or detector[name] is None:
            raise SettingsError(f'pi.detector.{name} is missing{_WHERE}')
    try:
        model = ModelSettings.from_dict({name: detector[name] for name in MODEL_FIELDS})
    except WireError as exc:
        raise SettingsError(f'pi.detector: {exc}') from None
    if not model.model.endswith('.pt'):
        raise SettingsError(f'pi.detector.model must be a .pt file name, like yolo11s.pt (got {model.model!r}); '
                            'the detector makes the ncnn model from it itself')
    return model


# ── where the settings come from ──────────────────────────────────────
def read_repo_settings(path: str | Path) -> dict[str, Any]:
    """The pi: section of config/settings.yaml in this repo ({} if there's no such file)."""
    try:
        text = Path(path).read_text(encoding='utf-8')
    except FileNotFoundError:
        return {}
    try:
        import yaml
    except ImportError:
        raise SettingsError('reading config/settings.yaml needs PyYAML: sudo apt install python3-yaml') from None
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise SettingsError(f'{path} is not valid YAML: {exc}') from None
    pi = data.get('pi') if isinstance(data, Mapping) else None
    return dict(pi) if isinstance(pi, Mapping) else {}


def computer_config(server: str, key: str, timeout: float) -> dict[str, Any]:
    """The computer's /api/pi/config reply: {"pi": {...}, "camera_source": ...}. Raises ComputerProblem."""
    server = server.rstrip('/')
    try:
        status, body = net.call('GET', server + '/api/pi/config', headers={'X-Desk-Key': key}, timeout=timeout)
    except net.NoAnswer as exc:
        raise ComputerProblem(net.computer_away(server, exc)) from None
    if status != 200:
        raise ComputerProblem(net.computer_said_no(status, body))
    try:
        data = json.loads(body)
    except ValueError:
        data = None
    if not isinstance(data, dict):
        raise ComputerProblem(f"The computer at {server} sent settings this program can't read")
    return data


class SettingsWatcher:
    """Asks the computer for its settings every `every_sec` (load() does the asking and layering).
    New detector model settings go to the container; other changes need a restart, and it says so."""

    def __init__(self, load: Callable[[], CameraSettings], current: CameraSettings, detector: DetectorClient | None,
                 *, every_sec: float, clock: Clock):
        self.load = load
        self.current = current
        self.detector = detector
        self.every_sec = every_sec
        self.clock = clock
        self._next = clock.monotonic() + every_sec

    def tick(self) -> float:
        now = self.clock.monotonic()
        if now < self._next:
            return self._next - now
        self._next = now + self.every_sec
        try:
            new = self.load()
        except ComputerProblem as exc:
            log.debug('Could not ask the computer for new settings: %s', exc)
            return self.every_sec
        except SettingsError as exc:
            log.warning("The computer's new settings have a problem, so they're not used: %s", exc)
            return self.every_sec
        if new.detector.model != self.current.detector.model and self.detector is not None:
            model = new.detector.model
            log.info('New detector settings from the computer: %s (%s, %d pixels)', model.model, model.format,
                     model.image_size)
            self.detector.want_model(model)
        if _without_model(new) != _without_model(self.current):
            log.info('The computer has new camera settings: restart the camera program to use them')
        self.current = new
        return self.every_sec


def _without_model(settings: CameraSettings) -> CameraSettings:
    return dataclasses.replace(settings, detector=dataclasses.replace(settings.detector, model=None))
