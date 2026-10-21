"""
Cameras. Each one has a `name` (sent to the computer with every picture), `capture()` → (JPEG bytes,
light level in lux or None) and `close()`.

    picamera2         the Raspberry Pi camera module (it also measures the light level)
    usb, usb:1        a USB webcam (OpenCV)
    folder:<dir>      the JPEGs in a folder, in a loop (for testing without a camera)
    video:<file>      a video file, in a loop (for testing without a camera)
    auto              the camera module if there is one, else the first USB webcam

picamera2 and cv2 come from apt (sudo apt install python3-picamera2 python3-opencv) and are only
imported when that camera is used.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from pathlib import Path

from raspberry_pi.camera.clock import Clock

log = logging.getLogger(__name__)

KINDS = 'auto | picamera2 | usb[:index] | folder:<dir> | video:<file>'


@dataclass(frozen=True)
class Picture:
    """One picture the Pi took."""

    jpeg: bytes
    frame_id: str            # e.g. pi-000123
    taken_at: float          # the Pi's clock, seconds since 1970
    lux: float | None        # light level (Pi camera module only)


class CameraError(RuntimeError):
    """The camera can't be opened or gave no picture. The text says why in plain words."""


class PiCamera:
    """The Raspberry Pi camera module, through Picamera2."""

    name = 'picamera2'

    def __init__(self, width: int, height: int, quality: int, warmup_sec: float, clock: Clock):
        try:
            from picamera2 import Picamera2
        except ImportError:
            raise CameraError('the Pi camera module needs picamera2: sudo apt install python3-picamera2') from None
        self.cam = Picamera2()
        config = self.cam.create_video_configuration(main={'size': (int(width), int(height)), 'format': 'RGB888'})
        self.cam.configure(config)
        self.cam.options['quality'] = int(quality)
        self.cam.start()
        clock.sleep(warmup_sec)              # let the exposure settle

    def capture(self) -> tuple[bytes, float | None]:
        request = self.cam.capture_request()
        try:
            buffer = io.BytesIO()
            request.save('main', buffer, format='jpeg')
            metadata = request.get_metadata() or {}
        finally:
            request.release()
        lux = metadata.get('Lux')
        return buffer.getvalue(), round(float(lux), 1) if lux is not None else None

    def close(self) -> None:
        try:
            self.cam.stop()
        except Exception as exc:          # closing a camera that's already gone is fine
            log.debug('Pi camera did not stop cleanly: %s', exc)


class OpenCVCamera:
    """A USB webcam (source = its number) or a video file (source = its path, played in a loop)."""

    def __init__(self, source: int | str, width: int, height: int, quality: int):
        import cv2
        self.cv2 = cv2
        self.cap = cv2.VideoCapture(source)
        if not self.cap.isOpened():
            raise CameraError(f'OpenCV could not open camera {source!r}')
        self.is_file = not isinstance(source, int)
        self.name = 'video' if self.is_file else 'usb'
        if not self.is_file:
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, int(width))
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, int(height))
        self.quality = int(quality)

    def capture(self) -> tuple[bytes, float | None]:
        ok, picture = self.cap.read()
        if not ok and self.is_file:
            self.cap.set(self.cv2.CAP_PROP_POS_FRAMES, 0)
            ok, picture = self.cap.read()
        if not ok or picture is None:
            raise CameraError('no picture from the camera')
        ok, buffer = self.cv2.imencode('.jpg', picture, [self.cv2.IMWRITE_JPEG_QUALITY, self.quality])
        if not ok:
            raise CameraError('could not turn the picture into a JPEG')
        return buffer.tobytes(), None

    def close(self) -> None:
        self.cap.release()


class FolderCamera:
    """Plays the JPEGs in a folder in a loop (for testing without a camera)."""

    name = 'folder'

    def __init__(self, folder: str | Path):
        self.files = sorted((p for p in Path(folder).expanduser().iterdir()
                             if p.suffix.lower() in ('.jpg', '.jpeg')), key=lambda p: p.name.lower())
        if not self.files:
            raise CameraError(f'no .jpg files in {folder}')
        self.next = 0

    def capture(self) -> tuple[bytes, float | None]:
        data = self.files[self.next % len(self.files)].read_bytes()
        self.next += 1
        return data, None

    def close(self) -> None:
        pass


def open_camera(kind: str, *, width: int, height: int, quality: int, warmup_sec: float, clock: Clock):
    """The camera `kind` names (see the top of this file)."""
    if kind.startswith('folder:'):
        return FolderCamera(kind.split(':', 1)[1])
    if kind.startswith('video:'):
        return OpenCVCamera(kind.split(':', 1)[1], width, height, quality)
    if kind == 'usb' or kind.startswith('usb:'):
        number = kind.split(':', 1)[1] if ':' in kind else '0'
        if not number.isdigit():
            raise CameraError(f'usb:<number> needs a webcam number, like usb:1 (got {kind!r})')
        return OpenCVCamera(int(number), width, height, quality)
    if kind == 'picamera2':
        return PiCamera(width, height, quality, warmup_sec, clock)
    if kind == 'auto':
        try:
            return PiCamera(width, height, quality, warmup_sec, clock)
        except Exception as exc:          # any trouble with the camera module: try a webcam instead
            log.info('Pi camera module not available (%s); trying a USB webcam', exc)
        return OpenCVCamera(0, width, height, quality)
    raise CameraError(f'unknown camera {kind!r}: use {KINDS}')
