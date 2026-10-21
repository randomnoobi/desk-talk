"""
Lens, the camera's own character, posting in the group chat.

Every lens.check_every_sec this asks the camera check (CameraHealthView: brightness, blur, glare, the
camera moving or going quiet) what needs fixing, and says it as Lens. With the Pi as the camera it
also mentions a Pi that runs hot or slows itself down, a Pi clock that's off, and the Pi's own object
detection being off (and why: this computer then detects instead). No AI is used, so Lens works even
without a key. Everything Lens says is also published as a CameraNotice for the log.
Nothing is said during quiet hours.

    lens = Lens(camera_health, source, desk_layout, preferences, notifier, bus, proof_photos, config.lens, config.pi,
                phone=<the phone source, if it's the camera>, pi_uploads=<the Pi source, if it's the camera>)
    supervisor.add(lens, start_after=config.lens.check_every_sec)
"""

from __future__ import annotations

import logging
import time
from typing import Any

from desktalk.config import LensSettings, PiSettings
from desktalk.core.agents.speakers import LENS
from desktalk.core.chat.workers.needs import DeskLayout, PhoneCamera, PiUploads
from desktalk.core.vision.proof_photos import ProofPhotos
from desktalk.domain import CameraNotice, DetectorState, PiHealth, UploadInfo
from desktalk.ports import CameraHealthView, EventBus, FrameSource, Notifier, Preferences
from desktalk.util import in_hour_window

log = logging.getLogger(__name__)

PHOTO_KEYS = {'cutoff', 'bumped', 'empty', 'glare'}     # problems you can see in a photo, so one goes with them
FPS_KINDS = {'phone', 'pi'}                              # cameras whose picture rate is checked

# The Pi's slow-down flags (vcgencmd get_throttled). The low bits are "right now"; higher ones mean
# "happened since the Pi started" and stay set, so only the low ones count.
UNDER_VOLTAGE_NOW = 0x1
SLOWED_NOW = 0xE            # speed capped, slowed down, or soft temperature limit

PI_FIXED = {
    'pi_hot': 'The Raspberry Pi has cooled down and is running normally again. Thanks!',
    'pi_power': 'The Raspberry Pi has enough power again. Thanks!',
    'pi_clock': "The Raspberry Pi's clock matches this computer's again.",
    'pi_detector': 'The Raspberry Pi is finding the objects itself again.',
}


def _flags(throttled: str) -> int:
    try:
        return int(throttled, 16) if throttled else 0
    except ValueError:
        return 0


class _PiWatch:
    """One Pi problem: said once it has lasted a while, repeated now and then, thanks once it's gone a while."""

    def __init__(self, key: str, *, wait: float, repeat: float, fixed_after: float):
        self.key = key
        self._wait, self._repeat, self._fixed_after = wait, repeat, fixed_after
        self._since: float | None = None        # when it started (None = not happening now)
        self._said_at: float | None = None
        self._open = False                      # said, and not fixed yet
        self._gone_since: float | None = None

    def update(self, now: float, text: str) -> dict[str, Any] | None:
        if text:
            self._gone_since = None
            if self._since is None:
                self._since = now
            if now - self._since >= self._wait and (self._said_at is None or now - self._said_at >= self._repeat):
                self._said_at, self._open = now, True
                return {'key': self.key, 'kind': 'problem', 'text': text}
            return None
        self._since = None
        if self._open:
            if self._gone_since is None:
                self._gone_since = now
            if now - self._gone_since >= self._fixed_after:
                self._open, self._gone_since = False, None
                return {'key': self.key, 'kind': 'fixed', 'text': PI_FIXED[self.key]}
        return None


class Lens:
    """A PeriodicWorker. `phone` / `pi_uploads`: pass the camera source when it's the phone / the Pi."""

    name = 'lens'

    def __init__(self, health: CameraHealthView, source: FrameSource, layout: DeskLayout, prefs: Preferences,
                 notifier: Notifier, bus: EventBus, photos: ProofPhotos, settings: LensSettings,
                 pi_settings: PiSettings,
                 *, phone: PhoneCamera | None = None, pi_uploads: PiUploads | None = None):
        """How long the Pi must be hot (or its clock off) before Lens says so: lens.wait_before_saying_sec.pi;
        how long its own detection must be off: lens.wait_before_saying_sec.pi_detector."""
        self._health = health
        self._source = source
        self._layout = layout
        self._prefs = prefs
        self._notifier = notifier
        self._bus = bus
        self._photos = photos
        self._settings = settings
        self._pi_settings = pi_settings
        self._phone = phone
        self._pi = pi_uploads
        waits = settings.wait_before_saying_sec
        timing = {'repeat': settings.repeat_after_min * 60, 'fixed_after': settings.fixed_after_sec}
        self._pi_watches = [_PiWatch(key, wait=waits.pi, **timing) for key in ('pi_hot', 'pi_power', 'pi_clock')]
        self._pi_watches.append(_PiWatch('pi_detector', wait=waits.pi_detector, **timing))

    def tick(self, now: float) -> float:
        source, phone = self._source, self._phone
        connected = source.connected
        quiet = self._quiet_hours(now)
        found = self._health.check(now, connected=connected, fps=source.fps if source.kind in FPS_KINDS else None,
                                   gravity=phone.recent_gravity() if phone else None,
                                   reference_gravity=self._layout.reference_gravity(),
                                   has_outline=self._layout.has_outline(),
                                   stopped=phone.stopped_by_user if phone else False, quiet=quiet)
        if quiet:
            return self._settings.check_every_sec       # (the Pi isn't checked either: no thanks for unseen warnings)
        for message in list(found) + self._pi_messages(now, connected):
            self._say(message, now)
        return self._settings.check_every_sec

    # ── the Pi ──
    def _pi_messages(self, now: float, connected: bool) -> list[dict[str, Any]]:
        latest = self._pi.latest_upload() if self._pi is not None and connected else None
        if latest is None:
            return []
        info, _ = latest
        texts = {'pi_hot': self._hot_text(info.health), 'pi_power': self._power_text(info.health),
                 'pi_clock': self._clock_text(self._pi.clock_off_sec()), 'pi_detector': self._detector_text(info)}
        said = [watch.update(now, texts[watch.key]) for watch in self._pi_watches]
        return [message for message in said if message]

    def _hot_text(self, health: PiHealth) -> str:
        temp = health.cpu_temp_c
        if temp is not None and temp >= self._pi_settings.hot_warning_c:
            return (f'The Raspberry Pi is running hot ({temp:.0f}°C), so it may slow down. '
                    f'Give it some air or a small fan.')
        flags = _flags(health.throttled)
        if flags & SLOWED_NOW and not flags & UNDER_VOLTAGE_NOW:
            return "The Raspberry Pi is slowing itself down because it's too hot. Give it some air or a small fan."
        return ''

    @staticmethod
    def _power_text(health: PiHealth) -> str:
        if _flags(health.throttled) & UNDER_VOLTAGE_NOW:
            return ("The Raspberry Pi's power supply is too weak, so it's slowing itself down. "
                    "The official Raspberry Pi power supply fixes this.")
        return ''

    def _clock_text(self, off: float | None) -> str:
        """`off`: the Pi's clock minus this computer's, as the Pi source judges it (negative = behind)."""
        if off is None or abs(off) <= self._pi_settings.clock_off_warning_sec:
            return ''
        way = 'behind' if off < 0 else 'ahead of'
        return (f"The Raspberry Pi's clock is {round(abs(off))} seconds {way} this computer's. "
                f"I go by this computer's time, but turning on network time on the Pi fixes it: "
                f"sudo timedatectl set-ntp true")

    @staticmethod
    def _detector_text(info: UploadInfo) -> str:
        if info.detector is not DetectorState.OFF:
            return ''
        why = f': {info.detector_reason}' if info.detector_reason else ''
        return (f"The Raspberry Pi isn't finding the objects itself right now{why}. "
                f'This computer is doing it instead, so everything still works.')

    # ── saying it ──
    def _quiet_hours(self, now: float) -> bool:
        prefs = self._prefs
        if not prefs.get('quiet_enabled'):
            return False
        return in_hour_window(time.localtime(now).tm_hour, int(prefs.get('quiet_start_hour')),
                              int(prefs.get('quiet_end_hour')))

    def _say(self, found: dict[str, Any], now: float) -> None:
        kind, text = found['kind'], found['text']
        message = {'type': 'group_message', 'character': LENS, 'text': text, 'lens': kind, 'toast': kind == 'problem'}
        if kind == 'problem' and found['key'] in PHOTO_KEYS:
            photo = self._photos.save('Lens: camera view')
            if photo:
                message['snapshot'] = photo
        self._notifier.send(message)
        self._bus.publish(CameraNotice(ts=now, key=found['key'], kind=kind, text=text))
        log.info('Lens: %s', text[:90])
