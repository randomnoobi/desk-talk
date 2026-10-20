"""
Where pictures come from.

All sources have the same read() as cv2.VideoCapture, so the detection
producer works the same whatever the camera is:

    ok, frame = source.read(timeout=1.0)

    PiFrameSource     the Raspberry Pi POSTs pictures to /api/pi/frame (raspberry_pi/camera_node.py)
    PhoneFrameSource  the phone page POSTs pictures to /api/phone/frame (HTTPS)
    WebcamSource      a webcam plugged into this computer (OpenCV)

The Pi and the phone push frames to us; we keep only the newest one, so a
slow computer skips pictures instead of falling behind.
"""

from __future__ import annotations

import sys
import threading
import time

import cv2
import numpy as np


class PushFrameSource:
    """Holds the newest pushed frame. Mimics cv2.VideoCapture's read()."""

    kind = 'push'

    def __init__(self, timeout_sec: float = 3.0):
        self.timeout_sec = timeout_sec
        self._cond = threading.Condition()
        self._frame = None
        self._jpeg = None        # the sender's original JPEG bytes for _frame
        self._meta: dict = {}
        self.jpeg = None         # JPEG bytes of the frame last returned by read()
        self.meta: dict = {}     # extra info sent with the frame last returned by read()
        self._seq = 0            # increases by 1 for every new frame
        self._read_seq = 0       # last frame number handed to read()
        self.last_frame_ts = 0.0
        self._fps = 0.0
        self.width = 0
        self.height = 0
        self.frames_received = 0
        self.stopped_by_user = False

    def mark_stopped(self):
        """The user pressed Stop (as opposed to the video dropping out)."""
        with self._cond:
            self.stopped_by_user = True
            self.last_frame_ts = 0.0

    # ---- called by the Flask route when a picture arrives ----
    def push_jpeg(self, data: bytes, meta: dict | None = None) -> bool:
        arr = np.frombuffer(data, dtype=np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if frame is None or frame.size == 0:
            return False
        now = time.time()
        with self._cond:
            gap = now - self.last_frame_ts
            if 0 < gap < 3:
                # Smoothed frames-per-second estimate for the status display.
                self._fps = 0.8 * self._fps + 0.2 * (1.0 / gap) if self._fps else 1.0 / gap
            self.stopped_by_user = False
            self._frame = frame
            self._jpeg = data
            self._meta = dict(meta or {})
            self._seq += 1
            self.frames_received += 1
            self.last_frame_ts = now
            self.height, self.width = frame.shape[:2]
            self._cond.notify_all()
        return True

    # ---- called by the detection producer (same shape as cv2.VideoCapture) ----
    def read(self, timeout: float = 1.0):
        """Wait for a frame we haven't processed yet. Returns (ok, frame)."""
        with self._cond:
            if self._seq == self._read_seq:
                self._cond.wait(timeout)
            if self._seq == self._read_seq or self._frame is None:
                return False, None
            self._read_seq = self._seq
            self.jpeg = self._jpeg
            self.meta = self._meta
            # No copy needed: push_jpeg() always stores a brand-new array, never edits this one.
            return True, self._frame

    def isOpened(self):
        return True

    def release(self):
        pass

    def set(self, *_args):
        return False

    @property
    def connected(self) -> bool:
        return (time.time() - self.last_frame_ts) < self.timeout_sec

    @property
    def fps(self) -> float:
        return round(self._fps, 1) if self.connected else 0.0


class PiFrameSource(PushFrameSource):
    """Pictures from the Raspberry Pi. Each one comes with its frame id, when it was
    taken, and (Pi camera module only) the light level in lux."""

    kind = 'pi'

    def __init__(self, timeout_sec: float = 3.0):
        super().__init__(timeout_sec)
        self.last_frame_id = ''
        self.pi_info: dict = {}      # what the Pi reported about itself (stored photos, free disk...)

    def push_jpeg(self, data: bytes, meta: dict | None = None) -> bool:
        ok = super().push_jpeg(data, meta)
        if ok and meta:
            self.last_frame_id = str(meta.get('frame_id', ''))
            info = {k: meta[k] for k in ('photos_stored', 'disk_free_mb', 'last_cleanup_ts', 'camera')
                    if k in meta}
            if info:
                self.pi_info.update(info)
        return ok


class PhoneFrameSource(PushFrameSource):
    """Pictures from the phone page. Also keeps the phone's tilt, so Lens can tell
    when the phone got bumped."""

    kind = 'phone'

    def __init__(self, timeout_sec: float = 3.0):
        super().__init__(timeout_sec)
        self.gravity = None      # (x, y, z) from the phone's motion sensor, if it has one
        self.gravity_ts = 0.0
        self.rotation = 0        # how much the phone turned the picture (0/90/180/270)

    def set_motion(self, gravity=None, rotation=None):
        if gravity is not None:
            self.gravity = gravity
            self.gravity_ts = time.time()
        if rotation is not None:
            self.rotation = rotation

    def recent_gravity(self, max_age: float = 5.0):
        return self.gravity if (time.time() - self.gravity_ts) < max_age else None


# ════════════════════════════════════════════════════════════════════
#  Webcam on this computer
# ════════════════════════════════════════════════════════════════════
def _open_capture_at_index(idx):
    """Open camera `idx`; on macOS retry with AVFoundation if the default backend fails."""
    cap = cv2.VideoCapture(idx)
    if cap.isOpened():
        return cap
    cap.release()
    if sys.platform == 'darwin':
        cap = cv2.VideoCapture(idx, cv2.CAP_AVFOUNDATION)
        if cap.isOpened():
            return cap
        cap.release()
    return None


def _capture_yields_frames(cap, warmup_reads=10):
    """USB / virtual cameras often need several reads before frames are valid."""
    for _ in range(warmup_reads):
        ok, frame = cap.read()
        if ok and frame is not None and frame.size > 0:
            return True
    return False


def discover_working_camera(preferred_idx, max_index=4):
    """
    Try `preferred_idx` first, then other indices. Many external webcams (e.g. USB) are index 1 on macOS
    while index 0 is FaceTime HD — isOpened() can succeed on the wrong empty device.
    Returns (cap, idx) or (None, None).
    """
    order = [preferred_idx] + [i for i in range(max_index) if i != preferred_idx]
    for idx in order:
        cap = _open_capture_at_index(idx)
        if cap is None:
            continue
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        try:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass
        if _capture_yields_frames(cap):
            return cap, idx
        cap.release()
    return None, None


class WebcamSource:
    """A webcam on this computer, opened the first time read() is called."""

    kind = 'webcam'

    def __init__(self, index: int = 0):
        self.preferred = index
        self.index = index
        self.cap = None
        self.jpeg = None
        self.meta: dict = {}
        self.last_frame_ts = 0.0
        self.failed = False
        self._fps = 0.0

    def open(self) -> bool:
        self.cap, idx = discover_working_camera(self.preferred)
        if self.cap is None:
            self.failed = True
            return False
        self.index = idx
        return True

    def read(self, timeout: float = 1.0):
        if self.cap is None and (self.failed or not self.open()):
            time.sleep(timeout)
            return False, None
        ok, frame = self.cap.read()
        if ok and frame is not None and frame.size:
            now = time.time()
            gap = now - self.last_frame_ts
            if 0 < gap < 3:
                self._fps = 0.8 * self._fps + 0.2 * (1.0 / gap) if self._fps else 1.0 / gap
            self.last_frame_ts = now
            return True, frame
        return False, None

    @property
    def connected(self) -> bool:
        return self.cap is not None and (time.time() - self.last_frame_ts) < 3

    @property
    def fps(self) -> float:
        return round(self._fps, 1) if self.connected else 0.0

    def release(self):
        if self.cap is not None:
            self.cap.release()
