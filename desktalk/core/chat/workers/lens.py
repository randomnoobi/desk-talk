"""
Lens, the camera's own character, posting in the group chat.

Every lens.check_every_sec this asks the camera check (CameraHealthView: brightness, blur, glare, the
camera moving or going quiet) what needs fixing, and says it as Lens. With the Pi as the camera it
also mentions a Pi that runs hot or slows itself down, a Pi clock that's off, and the Pi's own object
detection being off because it failed (and why: this computer then detects instead). No AI is used, so Lens works even
without a key. Everything Lens says is also published as a CameraNotice for the log.
Nothing is said during quiet hours (by the clock's local hour).

    lens = Lens(camera_health, source, desk_layout, preferences, notifier, bus, proof_photos, config.lens, config.pi,
                clock=clock, phone=<the phone source, if it's the camera>,
                pi_uploads=<the Pi source, if it's the camera>, prompts=config.prompts.lens,
                preview_chars=config.logging.preview_chars)
    supervisor.add(lens, start_after=config.lens.check_every_sec)
"""

from __future__ import annotations

import logging
from typing import Any

from desktalk.config import LensSettings, PiSettings
from desktalk.config.prompts import LensPrompts
from desktalk.core.agents.speakers import LENS
from desktalk.core.habits.preferences import quiet_hours_now
from desktalk.domain import CameraNotice, DetectorState, PiHealth, UploadInfo
from desktalk.ports import (
    CameraHealthView,
    Clock,
    DeskLayout,
    EventBus,
    FrameSource,
    Notifier,
    PhoneCamera,
    PhotoTaker,
    PiUploads,
    Preferences,
)
from desktalk_shared.wire import OFF_ON_PURPOSE

log = logging.getLogger(__name__)

PHOTO_KEYS = {'cutoff', 'bumped', 'empty', 'glare'}     # problems you can see in a photo, so one goes with them
FPS_KINDS = {'phone', 'pi'}                              # cameras whose picture rate is checked

# The Pi's slow-down flags (vcgencmd get_throttled). The low bits are "right now"; higher ones mean
# "happened since the Pi started" and stay set, so only the low ones count.
UNDER_VOLTAGE_NOW = 0x1
SLOWED_NOW = 0xE            # speed capped, slowed down, or soft temperature limit


def _flags(throttled: str) -> int:
    try:
        return int(throttled, 16) if throttled else 0
    except ValueError:
        return 0


class _PiWatch:
    """One Pi problem: said once it has lasted a while, repeated now and then, thanks once it's gone a while
    (with `fixed`, the words for that)."""

    def __init__(self, key: str, fixed: str, *, wait: float, repeat: float, fixed_after: float):
        self.key = key
        self._fixed = fixed
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
                return {'key': self.key, 'kind': 'fixed', 'text': self._fixed}
        return None

    def forget(self) -> None:
        """Drop it without a thank-you (it wasn't fixed, it just isn't a problem any more)."""
        self._since = self._said_at = self._gone_since = None
        self._open = False


class Lens:
    """A PeriodicWorker. `phone` / `pi_uploads`: pass the camera source when it's the phone / the Pi."""

    name = 'lens'

    def __init__(self, health: CameraHealthView, source: FrameSource, layout: DeskLayout, prefs: Preferences,
                 notifier: Notifier, bus: EventBus, photos: PhotoTaker, settings: LensSettings,
                 pi_settings: PiSettings, *, clock: Clock, prompts: LensPrompts, preview_chars: int,
                 phone: PhoneCamera | None = None, pi_uploads: PiUploads | None = None):
        """How long the Pi must be hot (or its clock off) before Lens says so: lens.wait_before_saying_sec.pi;
        how long its own detection must be off: lens.wait_before_saying_sec.pi_detector. `prompts`:
        config/prompts.yaml → lens (what Lens says about the Pi, and its photos' label). `preview_chars`:
        settings → logging.preview_chars, the longest line quoted in the log. `clock` gives the local hour, for
        quiet hours."""
        self._health = health
        self._source = source
        self._layout = layout
        self._prefs = prefs
        self._notifier = notifier
        self._bus = bus
        self._photos = photos
        self._settings = settings
        self._pi_settings = pi_settings
        self._clock = clock
        self._words = prompts
        self._preview_chars = preview_chars
        self._phone = phone
        self._pi = pi_uploads
        waits = settings.wait_before_saying_sec
        timing = {'repeat': settings.repeat_after_min * 60, 'fixed_after': settings.fixed_after_sec}
        fixed = prompts.pi_fixed
        self._pi_watches = [_PiWatch('pi_hot', fixed.pi_hot, wait=waits.pi, **timing),
                            _PiWatch('pi_power', fixed.pi_power, wait=waits.pi, **timing),
                            _PiWatch('pi_clock', fixed.pi_clock, wait=waits.pi, **timing),
                            _PiWatch('pi_detector', fixed.pi_detector, wait=waits.pi_detector, **timing)]

    def tick(self, now: float) -> float:
        source, phone = self._source, self._phone
        connected = source.connected
        quiet = quiet_hours_now(self._prefs, now, self._clock)
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
        pi = self._pi
        texts = {'pi_hot': self._hot_text(info.health), 'pi_power': self._power_text(info.health),
                 'pi_clock': self._clock_text(pi.clock_off_sec() if pi is not None else None),
                 'pi_detector': self._detector_text(info)}
        off_on_purpose = info.detector is DetectorState.OFF and info.detector_reason in OFF_ON_PURPOSE
        said = []
        for watch in self._pi_watches:
            if watch.key == 'pi_detector' and off_on_purpose:
                watch.forget()
            else:
                said.append(watch.update(now, texts[watch.key]))
        return [message for message in said if message]

    def _hot_text(self, health: PiHealth) -> str:
        temp = health.cpu_temp_c
        if temp is not None and temp >= self._pi_settings.hot_warning_c:
            return self._words.pi_hot.format(temp=temp)
        flags = _flags(health.throttled)
        if flags & SLOWED_NOW and not flags & UNDER_VOLTAGE_NOW:
            return self._words.pi_slowed
        return ''

    def _power_text(self, health: PiHealth) -> str:
        if _flags(health.throttled) & UNDER_VOLTAGE_NOW:
            return self._words.pi_power
        return ''

    def _clock_text(self, off: float | None) -> str:
        """`off`: the Pi's clock minus this computer's, as the Pi source judges it (negative = behind)."""
        if off is None or abs(off) <= self._pi_settings.clock_off_warning_sec:
            return ''
        way = self._words.pi_clock_behind if off < 0 else self._words.pi_clock_ahead
        return self._words.pi_clock.format(seconds=round(abs(off)), way=way)

    def _detector_text(self, info: UploadInfo) -> str:
        """Only when the Pi's detector is off because something went wrong: not when it was turned off on
        purpose (--no-detector) or the Pi runs the old camera program, which has none."""
        if info.detector is not DetectorState.OFF or info.detector_reason in OFF_ON_PURPOSE:
            return ''
        why = self._words.pi_detector_why.format(reason=info.detector_reason) if info.detector_reason else ''
        return self._words.pi_detector.format(why=why)

    # ── saying it ──
    def _say(self, found: dict[str, Any], now: float) -> None:
        kind, text = found['kind'], found['text']
        message = {'type': 'group_message', 'character': LENS, 'text': text, 'lens': kind, 'toast': kind == 'problem'}
        if kind == 'problem' and found['key'] in PHOTO_KEYS:
            photo = self._photos.save(self._words.photo_label)
            if photo:
                message['snapshot'] = photo
        self._notifier.send(message)
        self._bus.publish(CameraNotice(ts=now, key=found['key'], kind=kind, text=text))
        log.info('Lens: %s', text[:self._preview_chars])
