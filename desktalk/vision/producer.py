"""
The producer: camera pictures → detection events.

About 3 times a second (settings → camera.fps) it takes the newest picture,
runs YOLO on it, and publishes one "detection" event into the event stream:

    {"type": "detection", "ts": ..., "frame_id": "pi-000123", "source": "pi",
     "size": [1280, 720], "detected_obj": [{"label": "cup", "conf": 0.81,
     "bbox": [...], "center": [x, y]}, ...], "person": false,
     "light": {"brightness": 112.4, "lux": 240.0}}

It also keeps the newest plain and annotated pictures for the live view, and
hands each picture to Lens (camera health). Everything else (agents joining,
alerts, habits, the event log) happens in the consumers.
"""

from __future__ import annotations

import threading
import time

import cv2

from ..events.stream import DETECTION
from .detector import draw_boxes


def measure_light(frame) -> float:
    """Average brightness 0-255 of a small grey copy of the picture."""
    h, w = frame.shape[:2]
    small = cv2.resize(frame, (64, max(1, round(64 * h / max(1, w)))), interpolation=cv2.INTER_AREA)
    gray = small if small.ndim == 2 else cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    return round(float(gray.mean()), 1)


class DetectionProducer(threading.Thread):
    def __init__(self, source, detector, stream, desk, camera_health=None, analyzer=None,
                 profiles=None, fps: float = 3.0, cam_idx: int = 0, break_after_sec: float = 300):
        super().__init__(daemon=True, name='DetectionProducer')
        self.source = source
        self.detector = detector
        self.stream = stream
        self.desk = desk
        self.camera_health = camera_health
        self.analyzer = analyzer
        self.profiles = profiles
        self.min_interval = 1.0 / max(0.1, float(fps))
        self.cam_idx = cam_idx
        self.break_after_sec = break_after_sec
        self.frames_done = 0
        self.last_ts = 0.0
        self.last_ms = 0.0               # how long YOLO took on the last picture
        self._stop_event = threading.Event()

    def stop(self):
        self._stop_event.set()

    def run(self):
        while not self._stop_event.is_set():
            ok, frame = self.source.read(timeout=1.0)
            if not ok or frame is None or frame.size == 0:
                continue
            if time.time() - self.last_ts < self.min_interval:
                continue               # faster than the configured rate: skip this picture
            try:
                self.process_frame(frame, getattr(self.source, 'jpeg', None), getattr(self.source, 'meta', None))
            except Exception as exc:
                print(f'  [producer] {exc!r}')
                time.sleep(0.2)

    def process_frame(self, frame, jpeg: bytes | None = None, meta: dict | None = None,
                      now: float | None = None) -> dict:
        """Analyse one picture and publish its detection event. Returns the event."""
        meta = meta or {}
        now = now if now is not None else time.time()
        h, w = frame.shape[:2]
        if jpeg is None:
            _, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 78])
            jpeg = buf.tobytes()

        t0 = time.time()
        objects = self.detector.detect(frame)
        self.last_ms = (time.time() - t0) * 1000
        brightness = measure_light(frame)
        lux = meta.get('lux')
        person = any(o['label'] == 'person' for o in objects)

        moved = False
        if self.camera_health is not None:
            self.camera_health.sample(frame, [(o['label'], o['bbox']) for o in objects], now=now)
            mts = self.camera_health.motion_ts
            moved = bool(mts and 0 <= now - mts < 1.5)

        with self.desk.lock:
            gap = now - self.last_ts if self.last_ts else None
            if self.desk.session_start is None or (gap is not None and gap >= self.break_after_sec):
                self.desk.session_start = now          # first picture, or the camera was off a while
            self.desk.frame_w, self.desk.frame_h = w, h
            self.desk.raw_frames[self.cam_idx] = jpeg
            self.desk.cameras[self.cam_idx] = True
            self.desk.light = {'brightness': brightness, 'lux': lux}
            if person:
                self.desk.person_last_seen = now
            if moved:
                self.desk.last_motion_ts = now
            best = {}
            for o in objects:
                if o['label'] not in best or o['conf'] > best[o['label']]['conf']:
                    best[o['label']] = o
            self.desk.seen_labels = best
            agent_objects = dict(self.desk.objects)
        self.last_ts = now
        self.frames_done += 1

        ev = self.stream.publish(
            DETECTION, ts=now,
            frame_id=str(meta.get('frame_id') or f'{getattr(self.source, "kind", "cam")}-{self.frames_done:06d}'),
            source=getattr(self.source, 'kind', 'camera'),
            taken_at=meta.get('taken_at'),
            size=[w, h],
            detected_obj=objects,
            person=person,
            moved=moved,
            light={'brightness': brightness, 'lux': lux},
        )

        # The live view with boxes ("Boxes" in the app)
        annotated = frame.copy()
        agent_labels = {d['class_name'] for d in agent_objects.values()}
        draw_boxes(annotated, objects, highlight=agent_labels or None)
        if self.analyzer is not None and agent_objects:
            names = {}
            if self.profiles is not None:
                for pid in agent_objects:
                    p = self.profiles.get(pid)
                    if p:
                        names[pid] = p.name
            try:
                self.analyzer.update_frame_size(w, h)
                self.analyzer.draw_overlays(annotated, agent_objects, names)
            except Exception as exc:
                print(f'  [producer] overlay: {exc!r}')
        _, ba = cv2.imencode('.jpg', annotated, [cv2.IMWRITE_JPEG_QUALITY, 70])
        with self.desk.lock:
            self.desk.frames[self.cam_idx] = ba.tobytes()
        return ev
