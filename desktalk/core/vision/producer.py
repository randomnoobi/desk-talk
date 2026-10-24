"""
The producer: camera pictures → detection events.

About 3 times a second (settings → camera.fps) it takes the newest picture and works out what's in it:

    the Pi's detector said ok    use the detections that came with the picture
    the Pi's detector said busy  live view only: the Pi is still detecting an earlier picture
    off, or not a Pi camera      detect it here (UltralyticsDetector)

Then it publishes one DetectionEvent and updates the live view. Everything else (agents joining,
alerts, habits, the event log) happens in the parts that subscribe to the event.

A Pi picture taken before the one in the live view (the Pi sends an ok picture once its detector is
done, after the busy ones taken meanwhile) is analysed but not shown, unless the live view has stood
still for camera.keep_newer_picture_sec (so a Pi clock that jumps back can't freeze it).
"""

from __future__ import annotations

import logging
from typing import Any

import cv2

from desktalk.config import CameraSettings
from desktalk.desk_state import DeskState
from desktalk.domain import Detection, DetectionEvent, DetectorState, DetectorUnavailable, Frame, Light, keep_inside
from desktalk.domain.errors import plain_error
from desktalk.ports import Clock, Detector, EventBus, FrameSource, LiveViewPainter, PictureCheck

log = logging.getLogger(__name__)


def measure_light(picture: Any, width: int) -> float:
    """Average brightness 0-255 of a small grey copy of the picture (`width` pixels wide)."""
    h, w = picture.shape[:2]
    small = cv2.resize(picture, (width, max(1, round(width * h / max(1, w)))), interpolation=cv2.INTER_AREA)
    grey = small if small.ndim == 2 else cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    return round(float(grey.mean()), 1)


class _Trouble:
    """One thing that can go wrong over and over (3 times a second): the log says it once when it
    starts or changes, and once when it's fixed."""

    def __init__(self, what: str):
        self.what = what
        self.reason: str | None = None

    def failed(self, reason: str, exc: BaseException | None = None) -> None:
        if reason != self.reason:
            log.warning('%s failed: %s', self.what, reason, exc_info=exc)
        self.reason = reason

    def worked(self) -> None:
        if self.reason is not None:
            log.info('%s works again', self.what)
        self.reason = None


class DetectionProducer:
    """A PeriodicWorker (desktalk/ports.py), run by the Supervisor."""

    name = 'detection'

    def __init__(self, source: FrameSource, detector: Detector, bus: EventBus, desk: DeskState, clock: Clock,
                 lens: PictureCheck, painter: LiveViewPainter, camera: CameraSettings, *,
                 new_session_after_sec: float, camera_index: int = 0):
        self._source = source
        self._detector = detector
        self._bus = bus
        self._desk = desk
        self._clock = clock
        self._lens = lens
        self._painter = painter
        self._camera = camera
        self._new_session_after = new_session_after_sec
        self._cam = camera_index
        self._interval = 1.0 / camera.fps
        self._last_analysed_at: float | None = None
        self._newest: list[Detection] = []          # from the newest analysed picture, for busy pictures
        self._detecting = _Trouble('Detection on this computer')
        self._drawing = _Trouble('Drawing the live view')
        self._analysed = 0
        self._detect_ms: float | None = None
        self._shown_taken_at: float | None = None    # the Pi's time on the picture in the live view
        self._shown_at = 0.0                         # clock.monotonic() when the live view last changed

    @property
    def pictures_analysed(self) -> int:
        """Pictures that became a detection event (not the live-view-only ones)."""
        return self._analysed

    @property
    def last_detect_ms(self) -> float | None:
        """How long detection took for the newest analysed picture: here, or on the Pi if it said."""
        return self._detect_ms

    @property
    def problem(self) -> str | None:
        """Why detection on this computer isn't working, in plain words (None = it works)."""
        return self._detecting.reason

    def tick(self, now: float) -> float:
        """Handle the newest picture. Returns 0 (no picture yet: look again) or the time until the next is due."""
        frame = self._source.read(timeout=self._camera.wait_for_picture_sec)
        if frame is None:
            return 0.0
        started = self._clock.monotonic()
        self._handle(frame)
        return max(0.0, self._interval - (self._clock.monotonic() - started))

    def _handle(self, frame: Frame) -> None:
        now = self._clock.now()
        picture = frame.picture
        jpeg = frame.jpeg if frame.jpeg is not None else self._encode(picture, self._camera.jpeg_quality)
        if frame.detector_state is DetectorState.BUSY:
            self._show(frame, jpeg, self._newest)       # live view only, with the newest boxes
            return
        found = self._find(frame)
        if found is None:
            self._show(frame, jpeg, [])                 # detection failed: we don't know what's there
            return
        detections, where = found
        self._lens.sample(picture, detections, now)
        event = self._event(frame, detections, where, now)
        self._update_desk(event)
        self._bus.publish(event)
        self._analysed += 1
        self._newest = detections
        self._show(frame, jpeg, detections)

    def _find(self, frame: Frame) -> tuple[list[Detection], str] | None:
        """What's in the picture and where it was worked out (pi or computer); None if detection failed."""
        if frame.detector_state is DetectorState.OK:
            h, w = frame.picture.shape[:2]
            if frame.pi.detect_ms is not None:
                self._detect_ms = frame.pi.detect_ms
            return keep_inside(frame.pi_detections, w, h), 'pi'
        started = self._clock.monotonic()
        try:
            detections = self._detector.detect(frame.picture)
        except DetectorUnavailable as exc:
            self._detecting.failed(str(exc))
            return None
        self._detecting.worked()
        self._detect_ms = (self._clock.monotonic() - started) * 1000
        return detections, 'computer'

    def _event(self, frame: Frame, detections: list[Detection], where: str, now: float) -> DetectionEvent:
        h, w = frame.picture.shape[:2]
        light = Light(brightness=measure_light(frame.picture, self._camera.light_measure_width),
                      lux=frame.pi.lux if frame.pi else None)
        return DetectionEvent(ts=now, frame_id=frame.frame_id, source=frame.source, size=(w, h),
                              detections=tuple(detections), person=any(d.label == 'person' for d in detections),
                              light=light, moved=self._lens.moved_recently(now),
                              taken_at=frame.pi.taken_at if frame.pi else None, detected_on=where)

    def _update_desk(self, event: DetectionEvent) -> None:
        now = event.ts
        best: dict[str, Detection] = {}
        for d in event.detections:
            if d.label not in best or d.confidence > best[d.label].confidence:
                best[d.label] = d
        with self._desk.lock:
            gap = now - self._last_analysed_at if self._last_analysed_at is not None else None
            if self._desk.session_start is None or (gap is not None and gap >= self._new_session_after):
                self._desk.session_start = now          # the first picture, or the camera was off a while
            self._desk.frame_w, self._desk.frame_h = event.size
            self._desk.light = event.light.to_dict()
            if event.person:
                self._desk.person_last_seen = now
            if event.moved:
                self._desk.last_motion_ts = now
            self._desk.seen_labels = {label: d.to_dict() for label, d in best.items()}
        self._last_analysed_at = now

    def _older_than_shown(self, taken_at: float | None) -> bool:
        """The picture was taken before the one in the live view, which changed only a moment ago."""
        if taken_at is None or self._shown_taken_at is None or taken_at >= self._shown_taken_at:
            return False
        return self._clock.monotonic() - self._shown_at < self._camera.keep_newer_picture_sec

    def _show(self, frame: Frame, jpeg: bytes, detections: list[Detection]) -> None:
        """The live view: the plain picture and the one with boxes."""
        taken_at = frame.pi.taken_at if frame.pi else None
        if self._older_than_shown(taken_at):
            return
        self._shown_taken_at, self._shown_at = taken_at, self._clock.monotonic()
        picture = frame.picture
        try:
            painted = self._painter.paint(picture, detections)
            self._drawing.worked()
        except Exception as exc:                      # a drawing problem must not stop detection
            self._drawing.failed(plain_error(exc), exc)
            painted = picture
        boxes = self._encode(painted, self._camera.boxes_jpeg_quality)
        with self._desk.lock:
            self._desk.raw_frames[self._cam] = jpeg
            self._desk.frames[self._cam] = boxes
            self._desk.cameras[self._cam] = True

    @staticmethod
    def _encode(picture: Any, quality: int) -> bytes:
        return cv2.imencode('.jpg', picture, [cv2.IMWRITE_JPEG_QUALITY, quality])[1].tobytes()
