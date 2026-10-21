"""
Asks the detector container (raspberry_pi/detector_service/, in Docker on this Pi) what's in each picture.

    POST /detect   one JPEG → what it found; waits at most pi.detector.wait_per_picture_sec
    GET  /health   is it ready? Asked now and then while it's off
    PUT  /model    which model to run (pi.detector). Sent on start, when the computer's settings change,
                   and when /health says it's starting from scratch (e.g. a brand-new models folder)

When it can't answer, the picture goes to the computer as `off`, with the reason, and the computer
detects it itself:
  - No model yet (503; the first conversion to ncnn takes minutes): off until /health says ready.
    That's not a crash, so it doesn't count as a failure.
  - Anything else (crashed, too slow, not running): after failures_before_pause failures in a row it
    stops asking for a while (pause_start_sec, doubling each time up to pause_max_sec), asks /health
    every health_check_every_sec meanwhile, and comes back early when it's ready.

Only one picture is at the detector at a time (DetectorWorker): pictures taken meanwhile go out as
`busy`, just for the live view, so detection can be slower than the camera without a queue building up.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass

from desktalk_shared.detection import DetectorUnavailable
from desktalk_shared.detector_api import DetectorHealth, DetectReply, ModelSettings, ServiceState
from desktalk_shared.wire import DetectorState
from raspberry_pi.camera import net
from raspberry_pi.camera.cameras import Picture
from raspberry_pi.camera.clock import Clock

log = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class DetectorConfig:
    """pi.detector in config/settings.yaml (what each one means is in the comments there)."""

    url: str
    model: ModelSettings
    wait_per_picture_sec: float
    failures_before_pause: int
    pause_start_sec: float
    pause_max_sec: float
    health_check_every_sec: float


@dataclass(frozen=True)
class Verdict:
    """What to tell the computer about one picture: ok (with what was found), busy, or off (with why)."""

    state: DetectorState
    reason: str = ''
    reply: DetectReply | None = None


class DetectorClient:
    """Talks to the container. Only the DetectorWorker's thread calls detect() and upkeep()."""

    def __init__(self, config: DetectorConfig, clock: Clock):
        self.config = config
        self.clock = clock
        self._model = config.model
        self._model_due = True               # PUT /model at the next chance
        self._off_reason = ''                # why it's off ('' = send it pictures)
        self._resume_at: float | None = None  # when a pause ends (None: only /health brings it back)
        self._next_health = 0.0
        self._failures = 0                   # failures in a row
        self._pause = 0.0                    # how long the last pause was (0 = no pause since the last success)

    # ── asked from the camera's thread ──
    def off_now(self) -> str:
        """Why pictures shouldn't go to the detector right now, or '' when they should."""
        reason, resume_at = self._off_reason, self._resume_at
        if reason and (resume_at is None or self.clock.monotonic() < resume_at):
            return reason
        return ''

    def want_model(self, model: ModelSettings) -> None:
        """The computer's settings changed: send the new model settings at the next chance."""
        self._model = model
        self._model_due = True

    # ── the worker's thread ──
    def detect(self, jpeg: bytes) -> Verdict:
        """Ask about one picture. Never raises: a picture it can't detect comes back as off, with why."""
        try:
            status, body = net.call('POST', self._url('/detect'), body=jpeg, headers={'Content-Type': 'image/jpeg'},
                                    timeout=self.config.wait_per_picture_sec)
        except net.NoAnswer as exc:
            return self._failed(f"the detector isn't answering: {exc}")
        if status == 503:
            return self._no_model_yet(net.error_text(body) or 'the detector has no model yet')
        if status != 200:
            return self._failed(net.error_text(body) or f'the detector replied {status}')
        try:
            reply = DetectReply.from_dict(json.loads(body))
        except ValueError as exc:
            return self._failed(f"the detector sent a reply this program can't read ({exc})")
        self._working()
        return Verdict(DetectorState.OK, reply=reply)

    def upkeep(self) -> float | None:
        """Between pictures: ask /health while it's off, and send the model settings when they're due.
        Returns the seconds until the next health check, or None when nothing is due before the next picture."""
        reachable = not self.off_now()
        if not reachable and self.clock.monotonic() >= self._next_health:
            reachable = self._check_health()
        if reachable and self._model_due:
            self._send_model()
        if self.off_now():
            return max(0.0, self._next_health - self.clock.monotonic())
        return None

    def health(self) -> DetectorHealth:
        """What the container says about itself. Raises DetectorUnavailable (with why) when it can't say."""
        try:
            status, body = net.call('GET', self._url('/health'), timeout=self.config.wait_per_picture_sec)
        except net.NoAnswer as exc:
            raise DetectorUnavailable(f"the detector isn't answering at {self.config.url}: {exc}") from None
        if status != 200:
            raise DetectorUnavailable(net.error_text(body) or f'the detector replied {status}')
        try:
            return DetectorHealth.from_dict(json.loads(body))
        except ValueError as exc:
            raise DetectorUnavailable(f"the detector sent a reply this program can't read ({exc})") from None

    # ── inside ──
    def _url(self, path: str) -> str:
        return self.config.url.rstrip('/') + path

    def _off(self, reason: str, resume_at: float | None) -> None:
        self._off_reason = reason
        self._resume_at = resume_at
        self._next_health = self.clock.monotonic() + self.config.health_check_every_sec

    def _failed(self, reason: str) -> Verdict:
        self._failures += 1
        if self._failures >= self.config.failures_before_pause:
            start, most = self.config.pause_start_sec, self.config.pause_max_sec
            self._pause = min(self._pause * 2, most) if self._pause else start
            self._off(reason, resume_at=self.clock.monotonic() + self._pause)
            log.warning('Stopped asking the detector for %g s (%s). Pictures go to the computer to detect.',
                        self._pause, reason)
        return Verdict(DetectorState.OFF, reason)

    def _no_model_yet(self, reason: str) -> Verdict:
        if not self.off_now():
            log.info('The detector has no model yet (%s). Pictures go to the computer to detect until it is ready.',
                     reason)
        self._off(reason, resume_at=None)
        return Verdict(DetectorState.OFF, reason)

    def _working(self) -> None:
        if self._off_reason:
            log.info('The detector is answering again')
        self._failures = 0
        self._pause = 0.0
        self._off_reason = ''
        self._resume_at = None

    def _check_health(self) -> bool:
        """Ask /health. True if it answered."""
        self._next_health = self.clock.monotonic() + self.config.health_check_every_sec
        try:
            health = self.health()
        except DetectorUnavailable as exc:
            log.debug('Detector health check: %s', exc)
            return False
        if health.state is ServiceState.STARTING:
            self._model_due = True
        if health.state is ServiceState.READY:
            self._off_reason = ''
            self._resume_at = None
            log.info('The detector says it is ready')
        else:
            self._off_reason = health.message or f'the detector is {health.state.value}'
        return True

    def _send_model(self) -> None:
        model = self._model
        try:
            status, body = net.call('PUT', self._url('/model'), body=json.dumps(model.to_dict()).encode(),
                                    headers={'Content-Type': 'application/json'},
                                    timeout=self.config.wait_per_picture_sec)
        except net.NoAnswer as exc:
            self._failed(f"the detector isn't answering: {exc}")
            return
        if self._model is model:
            self._model_due = False          # sent (or refused: sending the same again won't help)
        if status == 200:
            log.info('Asked the detector to use %s (%s, %d pixels)', model.model, model.format, model.image_size)
        else:
            log.warning("The detector won't use these model settings (%s). Check pi.detector in config/settings.yaml "
                        'on your computer.', net.error_text(body) or status)


class DetectorWorker:
    """Keeps the detector busy with at most one picture: the newest one whenever it's free.
    The camera's thread calls offer(); a thread of its own calls tick() over and over."""

    def __init__(self, client: DetectorClient, deliver: Callable[[Picture, Verdict], None]):
        self.client = client
        self.deliver = deliver               # gets each picture the detector took, once it's done
        self.wake = threading.Event()        # set when there's a picture: the thread stops waiting
        self._lock = threading.Lock()
        self._waiting: Picture | None = None
        self._busy = False

    def offer(self, picture: Picture) -> Verdict | None:
        """None if the detector took the picture (it's delivered when it's done); otherwise how to send it now."""
        with self._lock:
            if self._waiting is not None or self._busy:
                return Verdict(DetectorState.BUSY)
            reason = self.client.off_now()
            if reason:
                return Verdict(DetectorState.OFF, reason)
            self._waiting = picture
        self.wake.set()
        return None

    def tick(self) -> float | None:
        """Detect the waiting picture, or look after the container. Returns the seconds until the next
        round (None: when a picture comes)."""
        with self._lock:
            picture, self._waiting = self._waiting, None
            self._busy = picture is not None
        if picture is None:
            return self.client.upkeep()
        try:
            verdict = self.client.detect(picture.jpeg)
        finally:
            with self._lock:
                self._busy = False
        self.deliver(picture, verdict)
        return 0.0


class NoDetector:
    """Stands in for the worker when detection on the Pi is turned off: every picture goes out as off."""

    def __init__(self, reason: str):
        self.reason = reason

    def offer(self, picture: Picture) -> Verdict:
        return Verdict(DetectorState.OFF, self.reason)
