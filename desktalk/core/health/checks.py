"""
The health checks: each measure on its own, with a level (ok, warning, serious, or unknown when it can't be told).

    camera          pictures arriving (Lens's camera check already says when they stop, so Lens doesn't again)
    detections      seconds since a picture last went through object detection, while the camera is connected:
                    covers a Pi detector that stays busy, one that fails, and one that is off.
                    health_checks.no_detections_warning_sec / no_detections_serious_sec
    pi_temperature  the Pi's processor, from its newest heartbeat or picture: pi.hot_warning_c / pi.hot_serious_c.
                    Older than health_checks.pi_heartbeat_late_sec is too old to go by
    card            free space on the disk the data folder is on (on the Pi, its SD card):
                    health_checks.card_warning_below_mb / card_serious_below_mb
    pi_heartbeat    seconds since the Pi's last heartbeat: a warning after health_checks.pi_heartbeat_late_sec.
                    Unknown before the first (an older camera program sends none)
    card_rest       free space on the rest of the SD card, outside Desk Talk's own space (only when it has one, or
                    saves nothing): health_checks.card_rest_warning_below_mb / card_rest_serious_below_mb
    saving          whether Desk Talk is saving at all: serious when it saves nothing (Saving.OFF), ok otherwise

With Saving.ROOM, `card` is the free space in Desk Talk's own space and uses health_checks.room_warning_below_mb /
room_serious_below_mb. With Saving.OFF the data folder is in memory, so `card` is unknown.

Lens says the ones in trouble (desktalk/core/chat/workers/lens.py); /api/health shows them all.

    checks = HealthChecks(source, producer, disk_space, config.health_checks, config.pi, clock=clock, pi=<the Pi
                          source, if it's the camera>, saving=<Saving>, card=<the rest of the SD card>)
"""

from __future__ import annotations

import threading

from desktalk.config import HealthChecksSettings, PiSettings
from desktalk.domain import CheckLevel, HealthMeasure, Saving
from desktalk.ports import Clock, DetectionTimes, DiskSpace, FrameSource, PiHeartbeats

SECONDS, DEGREES, MEGABYTES = 's', '°C', 'MB'


def _rising(value: float, warning: float, serious: float) -> CheckLevel:
    """The level of a number that is bad when it gets high."""
    if value >= serious:
        return CheckLevel.SERIOUS
    return CheckLevel.WARNING if value >= warning else CheckLevel.OK


class HealthChecks:
    """A HealthCheck (desktalk/ports.py). `pi`: the Pi source when the Pi is the camera."""

    def __init__(self, source: FrameSource, detections: DetectionTimes, disk: DiskSpace,
                 settings: HealthChecksSettings, pi_settings: PiSettings, *, clock: Clock,
                 pi: PiHeartbeats | None = None, saving: Saving = Saving.PLAIN, card: DiskSpace | None = None):
        self._source = source
        self._detections = detections
        self._disk = disk
        self._settings = settings
        self._pi_settings = pi_settings
        self._clock = clock
        self._pi = pi
        self._saving = saving
        self._rest = card                              # the rest of the SD card (`disk` is the data folder)
        self._lock = threading.Lock()                  # Lens and /api/health both ask
        # clock.monotonic() since when the camera has been connected, as far as these checks have seen (None = it
        # isn't): from the start, until they see it away
        self._connected_since: float | None = clock.monotonic()

    def measures(self) -> list[HealthMeasure]:
        now = self._clock.monotonic()
        connected = self._source.connected
        return [HealthMeasure(key='camera', level=CheckLevel.OK if connected else CheckLevel.SERIOUS),
                self._new_detections(now, connected), self._temperature(now), self._card(), self._heartbeat(now),
                self._card_rest(), self._saving_measure()]

    def _new_detections(self, now: float, connected: bool) -> HealthMeasure:
        with self._lock:
            if not connected:
                self._connected_since = None
            elif self._connected_since is None:
                self._connected_since = now
            since = self._connected_since
        if since is None:
            return HealthMeasure(key='detections', level=CheckLevel.UNKNOWN, unit=SECONDS)
        found = self._detections.found_at
        age = now - (since if found is None else max(found, since))
        s = self._settings
        return HealthMeasure(key='detections', value=round(age, 1), unit=SECONDS,
                             level=_rising(age, s.no_detections_warning_sec, s.no_detections_serious_sec))

    def _temperature(self, now: float) -> HealthMeasure:
        latest = self._pi.latest_health() if self._pi is not None else None
        temp = latest[0].cpu_temp_c if latest is not None else None
        if latest is None or temp is None:
            return HealthMeasure(key='pi_temperature', level=CheckLevel.UNKNOWN, unit=DEGREES)
        if now - latest[1] >= self._settings.pi_heartbeat_late_sec:
            return HealthMeasure(key='pi_temperature', level=CheckLevel.UNKNOWN, value=temp, unit=DEGREES)
        p = self._pi_settings
        return HealthMeasure(key='pi_temperature', value=temp, unit=DEGREES,
                             level=_rising(temp, p.hot_warning_c, p.hot_serious_c))

    def _card(self) -> HealthMeasure:
        if self._saving is Saving.OFF:                 # the data folder is in memory: its space says nothing
            return HealthMeasure(key='card', level=CheckLevel.UNKNOWN, unit=MEGABYTES)
        s = self._settings
        if self._saving is Saving.ROOM:
            return self._free('card', self._disk, s.room_warning_below_mb, s.room_serious_below_mb)
        return self._free('card', self._disk, s.card_warning_below_mb, s.card_serious_below_mb)

    def _card_rest(self) -> HealthMeasure:
        s = self._settings
        rest = None if self._saving is Saving.PLAIN else self._rest      # plain: the card is the data folder's disk
        return self._free('card_rest', rest, s.card_rest_warning_below_mb, s.card_rest_serious_below_mb)

    def _saving_measure(self) -> HealthMeasure:
        return HealthMeasure(key='saving',
                             level=CheckLevel.SERIOUS if self._saving is Saving.OFF else CheckLevel.OK)

    @staticmethod
    def _free(key: str, disk: DiskSpace | None, warning: float, serious: float) -> HealthMeasure:
        """A free-space measure: a warning below `warning` MB, serious below `serious`, unknown when unreadable."""
        free = disk.free_mb() if disk is not None else None
        if free is None:
            return HealthMeasure(key=key, level=CheckLevel.UNKNOWN, unit=MEGABYTES)
        level = CheckLevel.SERIOUS if free < serious else CheckLevel.WARNING if free < warning else CheckLevel.OK
        return HealthMeasure(key=key, level=level, value=round(free, 1), unit=MEGABYTES)

    def _heartbeat(self, now: float) -> HealthMeasure:
        last = self._pi.last_heartbeat_at() if self._pi is not None else None
        if last is None:
            return HealthMeasure(key='pi_heartbeat', level=CheckLevel.UNKNOWN, unit=SECONDS)
        age = now - last
        level = CheckLevel.WARNING if age >= self._settings.pi_heartbeat_late_sec else CheckLevel.OK
        return HealthMeasure(key='pi_heartbeat', level=level, value=round(age, 1), unit=SECONDS)
