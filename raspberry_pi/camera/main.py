"""
The camera program's loop: take a picture every 1/fps seconds, save it on the Pi, ask the detector
container what's in it (one picture at a time), and send it to the computer.

Four background threads keep the camera's pace steady: the detector worker, the uploader, the cleaner
(deletes stored pictures) and the settings watcher (workers.py starts them). Each one only waits on its own
job, so a slow detector, Wi-Fi or SD card never holds up the camera. Another, the watchdog, ends the program
when the camera stops giving pictures, so systemd starts it again. The command line is in cli.py.

A picture that can't be saved on the Pi is still detected and sent, and a cleanup that fails never stops
the camera: each problem is logged once, until it goes away.
"""

from __future__ import annotations

import logging
import threading
from collections import Counter
from typing import Any

from desktalk_shared.wire import DETECTOR_TURNED_OFF, DetectorState, PiHealth, UploadInfo
from raspberry_pi.camera.cameras import Picture
from raspberry_pi.camera.cleaner import Cleaner
from raspberry_pi.camera.clock import Clock
from raspberry_pi.camera.detector_client import DetectorWorker, NoDetector, Verdict
from raspberry_pi.camera.health import HealthReader
from raspberry_pi.camera.photo_store import PhotoStore
from raspberry_pi.camera.settings import CameraSettings
from raspberry_pi.camera.uploader import Uploader
from raspberry_pi.camera.watchdog import Watchdog

log = logging.getLogger(__name__)

NO_DETECTOR = DETECTOR_TURNED_OFF
OFFLINE = 'nothing is sent (--offline)'


class CameraProgram:
    def __init__(self, *, camera: Any, store: PhotoStore, health: HealthReader, uploader: Uploader | None,
                 settings: CameraSettings, clock: Clock):
        self.camera = camera
        self.store = store
        self.health = health
        self.uploader = uploader
        self.settings = settings
        self.clock = clock
        self.watchdog: Watchdog | None = None           # build_program sets it
        self.cleaner: Cleaner | None = None             # build_program sets it
        self.detector: DetectorWorker | NoDetector = NoDetector(OFFLINE)    # build_program sets the real one
        self.taken = 0
        self.errors = 0
        self.verdicts: Counter[DetectorState] = Counter()
        self.health_now = PiHealth(camera=camera.name)
        self.running = True
        self._next_health = clock.monotonic()
        self._next_status = clock.monotonic() + settings.status_every_sec
        self._problem = ''
        self._save_problem = ''
        self._lock = threading.Lock()

    def run(self, max_pictures: int | None = None) -> None:
        """Take pictures until stopped (or until max_pictures, for testing)."""
        interval = 1.0 / self.settings.fps
        next_shot = self.clock.monotonic()
        while self.running:
            now = self.clock.monotonic()
            if now < next_shot:
                self.clock.sleep(next_shot - now)
                continue
            next_shot += interval
            if next_shot <= now:
                next_shot = now + interval       # after a hold-up, carry on from now: no burst to catch up
            self.step_safely()
            self.maybe_log_status()
            if max_pictures and self.taken >= max_pictures:
                break

    def step(self) -> Picture:
        """Take, save and hand on one picture."""
        now = self.clock.now()
        jpeg, lux = self.camera.capture()
        if self.watchdog is not None:
            self.watchdog.picture_taken()
        self.taken += 1
        picture = Picture(jpeg=jpeg, frame_id=f'pi-{self.taken:06d}', taken_at=now, lux=lux)
        self._save(picture)
        self._check_health()
        verdict = self.detector.offer(picture)
        if verdict is not None:
            self.deliver(picture, verdict)
        return picture

    def step_safely(self) -> None:
        """step(), but a problem is logged (once, until it goes away) and followed by a short wait."""
        try:
            self.step()
        except Exception as exc:
            self.errors += 1
            problem = str(exc) or type(exc).__name__
            if problem != self._problem:
                log.warning('Problem taking or saving a picture: %s', problem)
                self._problem = problem
            self.clock.sleep(self.settings.pause_after_error_sec)
            return
        if self._problem:
            log.info('Taking pictures again')
            self._problem = ''

    def _save(self, picture: Picture) -> None:
        """Keep the picture on the Pi. If that fails (disk full, a folder root owns ...), the picture still goes
        to the detector and the computer; the problem is logged once, until saving works again."""
        try:
            self.store.save(picture.jpeg, picture.taken_at, picture.frame_id)
        except OSError as exc:
            problem = str(exc) or type(exc).__name__
            if problem != self._save_problem:
                log.warning('Could not save a picture on the Pi (it is still sent): %s', problem)
                self._save_problem = problem
            return
        if self._save_problem:
            log.info('Saving pictures on the Pi works again')
            self._save_problem = ''

    def deliver(self, picture: Picture, verdict: Verdict) -> None:
        """Send a picture to the computer with what the detector said (called from the detector's thread too)."""
        with self._lock:
            self.verdicts[verdict.state] += 1
        if self.uploader is None:
            return
        reply = verdict.reply
        info = UploadInfo(frame_id=picture.frame_id, taken_at=picture.taken_at, lux=picture.lux,
                          detector=verdict.state, detector_reason=verdict.reason,
                          detections=reply.detections if reply else (), detect_ms=reply.detect_ms if reply else None,
                          model=reply.model if reply else '', health=self.health_now)
        self.uploader.offer(picture.jpeg, info)

    def maybe_log_status(self) -> None:
        now = self.clock.monotonic()
        if now < self._next_status:
            return
        self._next_status = now + self.settings.status_every_sec
        up = self.uploader
        sent = f', sent {up.sent}, failed {up.failed}, skipped {up.skipped}' if up else ''
        found = self.verdicts
        temp = self.health_now.cpu_temp_c
        log.info('Pictures taken %d%s; detector ok %d, busy %d, off %d; stored %s; %s MB free%s', self.taken, sent,
                 found[DetectorState.OK], found[DetectorState.BUSY], found[DetectorState.OFF],
                 self.health_now.photos_stored, self.health_now.disk_free_mb,
                 f'; {temp:g} °C' if temp is not None else '')

    def close(self) -> None:
        self.camera.close()

    def _check_health(self) -> None:
        now = self.clock.monotonic()
        if now < self._next_health:
            return
        self._next_health = now + self.settings.health_every_sec
        self.health_now = self.health.read(self.camera.name, self.store)    # cheap: the store keeps its count
        free = self.health_now.disk_free_mb
        if free is not None and free < self.store.min_free_mb and self.cleaner is not None:
            self.cleaner.need_space()                   # the cleaner's thread deletes; the camera carries on
