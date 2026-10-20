#!/usr/bin/env python3
"""
Desk Talk camera node: runs on the Raspberry Pi.

    python3 raspberry_pi/camera_node.py --server http://192.168.1.20:5000 --key abc123

(server.py prints this exact command, with your computer's address and key.)

What it does:
  1. Takes a picture 3 times a second (settings → pi.fps).
  2. Saves each one on the Pi, in ~/desk-talk-photos/<day>/ (pictures stay on the Pi).
  3. Sends the newest picture to your computer, which detects objects and keeps
     the event log. If the Wi-Fi is slow it skips pictures instead of falling behind.
  4. Every 10 minutes deletes the saved pictures, so the Pi's storage never fills
     up (pi.delete_every_min, pi.keep_last_min). If the disk gets nearly full
     (pi.min_free_mb) it deletes the oldest pictures right away.

Settings come from config/settings.yaml (pi:) on your computer: the node asks
the server for them when it starts. Command-line flags win over both.

Cameras: the Pi camera module (Picamera2, which also reports the light level in
lux), or a USB webcam (OpenCV). For testing without a camera:
    --camera folder:/path/to/jpgs   or   --camera video:/path/to/clip.mp4

Only the Python standard library is required, plus picamera2 or opencv for a
real camera (both come with Raspberry Pi OS, or: sudo apt install python3-picamera2 python3-opencv).
"""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import signal
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULTS = {
    'fps': 3.0,
    'width': 1280,
    'height': 720,
    'jpeg_quality': 80,
    'photo_dir': '~/desk-talk-photos',
    'delete_every_min': 10.0,
    'keep_last_min': 0.0,
    'min_free_mb': 300,
    'upload_timeout_sec': 5.0,
}


def log(msg: str) -> None:
    print(time.strftime('%H:%M:%S'), msg, flush=True)


# ════════════════════════════════════════════════════════════════════
#  Settings: built-in → config/settings.yaml → the server → flags
# ════════════════════════════════════════════════════════════════════
def settings_from_repo() -> dict:
    """pi: section of config/settings.yaml, if this file is inside the project and PyYAML is installed."""
    path = Path(__file__).resolve().parent.parent / 'config' / 'settings.yaml'
    try:
        import yaml
        data = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
        return dict(data.get('pi') or {})
    except Exception:
        return {}


def settings_from_server(server: str, key: str, timeout: float = 5.0) -> dict:
    try:
        req = urllib.request.Request(server.rstrip('/') + '/api/pi/config', headers={'X-Desk-Key': key})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return dict(json.loads(r.read().decode()).get('pi') or {})
    except Exception:
        return {}


# ════════════════════════════════════════════════════════════════════
#  Cameras: capture() → (jpeg_bytes, {'lux': ...})
# ════════════════════════════════════════════════════════════════════
class PiCamera:
    """Raspberry Pi camera module through Picamera2."""
    name = 'picamera2'

    def __init__(self, width, height, quality):
        from picamera2 import Picamera2
        self.cam = Picamera2()
        cfg = self.cam.create_video_configuration(main={'size': (int(width), int(height)), 'format': 'RGB888'})
        self.cam.configure(cfg)
        self.cam.options['quality'] = int(quality)
        self.cam.start()
        time.sleep(1.0)          # let exposure settle

    def capture(self):
        request = self.cam.capture_request()
        try:
            buf = io.BytesIO()
            request.save('main', buf, format='jpeg')
            md = request.get_metadata() or {}
        finally:
            request.release()
        lux = md.get('Lux')
        return buf.getvalue(), {'lux': round(float(lux), 1) if lux is not None else None}

    def close(self):
        try:
            self.cam.stop()
        except Exception:
            pass


class OpenCVCamera:
    """A USB webcam (or any OpenCV source, e.g. a video file)."""
    name = 'opencv'

    def __init__(self, source, width, height, quality):
        import cv2
        self.cv2 = cv2
        self.cap = cv2.VideoCapture(source)
        if not self.cap.isOpened():
            raise RuntimeError(f'OpenCV could not open camera {source!r}')
        if isinstance(source, int):
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, int(width))
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, int(height))
        self.quality = int(quality)
        self.loop_video = not isinstance(source, int)

    def capture(self):
        ok, frame = self.cap.read()
        if not ok and self.loop_video:
            self.cap.set(self.cv2.CAP_PROP_POS_FRAMES, 0)
            ok, frame = self.cap.read()
        if not ok or frame is None:
            raise RuntimeError('no picture from the camera')
        ok, buf = self.cv2.imencode('.jpg', frame, [self.cv2.IMWRITE_JPEG_QUALITY, self.quality])
        return buf.tobytes(), {'lux': None}

    def close(self):
        self.cap.release()


class FolderCamera:
    """Plays JPEGs from a folder in a loop (for testing without a camera)."""
    name = 'folder'

    def __init__(self, folder):
        self.files = sorted(p for p in Path(folder).expanduser().iterdir()
                            if p.suffix.lower() in ('.jpg', '.jpeg'))
        if not self.files:
            raise RuntimeError(f'no .jpg files in {folder}')
        self.i = 0

    def capture(self):
        data = self.files[self.i % len(self.files)].read_bytes()
        self.i += 1
        return data, {'lux': None}

    def close(self):
        pass


def open_camera(kind: str, width, height, quality):
    if kind.startswith('folder:'):
        return FolderCamera(kind.split(':', 1)[1])
    if kind.startswith('video:'):
        return OpenCVCamera(kind.split(':', 1)[1], width, height, quality)
    if kind.startswith('usb'):
        idx = int(kind.split(':', 1)[1]) if ':' in kind else 0
        return OpenCVCamera(idx, width, height, quality)
    if kind in ('auto', 'picamera2'):
        try:
            return PiCamera(width, height, quality)
        except Exception as exc:
            if kind == 'picamera2':
                raise
            log(f'Pi camera module not available ({exc}); trying a USB webcam')
    return OpenCVCamera(0, width, height, quality)


# ════════════════════════════════════════════════════════════════════
#  Photo storage on the Pi
# ════════════════════════════════════════════════════════════════════
class PhotoStore:
    def __init__(self, folder, keep_last_sec: float, min_free_mb: float):
        self.folder = Path(folder).expanduser()
        self.folder.mkdir(parents=True, exist_ok=True)
        self.keep_last_sec = float(keep_last_sec)
        self.min_free_mb = float(min_free_mb)
        self.lock = threading.Lock()
        self.last_cleanup = 0.0
        self.deleted_total = 0

    def save(self, jpeg: bytes, ts: float, frame_id: str) -> Path:
        day = time.strftime('%Y-%m-%d', time.localtime(ts))
        name = time.strftime('%H-%M-%S', time.localtime(ts)) + f'.{int(ts * 1000) % 1000:03d}_{frame_id}.jpg'
        d = self.folder / day
        with self.lock:
            d.mkdir(parents=True, exist_ok=True)
            path = d / name
            tmp = d / (name + '.part')
            tmp.write_bytes(jpeg)
            os.replace(tmp, path)
        return path

    def photos(self) -> list[Path]:
        return sorted(self.folder.glob('*/*.jpg'))

    def count(self) -> int:
        return len(self.photos())

    def free_mb(self) -> int:
        return int(shutil.disk_usage(self.folder).free / (1024 * 1024))

    def cleanup(self, now: float | None = None) -> int:
        """Delete pictures older than keep_last_sec (all of them when it's 0)."""
        now = now if now is not None else time.time()
        cutoff = now - self.keep_last_sec
        removed = 0
        with self.lock:
            for p in self.photos():
                try:
                    if p.stat().st_mtime <= cutoff:
                        p.unlink()
                        removed += 1
                except FileNotFoundError:
                    pass
            for d in self.folder.iterdir():
                if d.is_dir() and not any(d.iterdir()):
                    d.rmdir()
            self.last_cleanup = now
        self.deleted_total += removed
        return removed

    def free_up_space(self) -> int:
        """Disk nearly full: delete the oldest pictures until there is room again."""
        removed = 0
        if self.free_mb() >= self.min_free_mb:
            return 0
        with self.lock:
            for p in self.photos():
                if self.free_mb() >= self.min_free_mb:
                    break
                try:
                    p.unlink()
                    removed += 1
                except FileNotFoundError:
                    pass
        self.deleted_total += removed
        return removed


# ════════════════════════════════════════════════════════════════════
#  Sending pictures to the computer (newest one wins)
# ════════════════════════════════════════════════════════════════════
class Uploader(threading.Thread):
    def __init__(self, server: str, key: str, timeout: float, store: PhotoStore, camera_name: str):
        super().__init__(daemon=True, name='Uploader')
        self.url = server.rstrip('/') + '/api/pi/frame'
        self.key = key
        self.timeout = timeout
        self.store = store
        self.camera_name = camera_name
        self.cond = threading.Condition()
        self.latest = None            # (jpeg, meta) waiting to be sent
        self.sent = 0
        self.failed = 0
        self.skipped = 0
        self.last_error = ''
        self.running = True
        self._said = set()

    def offer(self, jpeg: bytes, meta: dict) -> None:
        with self.cond:
            if self.latest is not None:
                self.skipped += 1     # the previous one never got sent: the network is slower than the camera
            self.latest = (jpeg, meta)
            self.cond.notify()

    def _post(self, jpeg: bytes, meta: dict) -> None:
        headers = {'Content-Type': 'image/jpeg', 'X-Desk-Key': self.key, 'X-Frame-Id': meta['frame_id'],
                   'X-Taken-At': f'{meta["taken_at"]:.3f}', 'X-Pi-Camera': self.camera_name,
                   'X-Pi-Photos': str(meta.get('photos_stored', '')), 'X-Pi-Free-MB': str(meta.get('free_mb', '')),
                   'X-Pi-Last-Cleanup': f'{self.store.last_cleanup:.0f}'}
        if meta.get('lux') is not None:
            headers['X-Lux'] = str(meta['lux'])
        req = urllib.request.Request(self.url, data=jpeg, headers=headers, method='POST')
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            r.read()

    def run(self):
        backoff = 0.0
        while self.running:
            with self.cond:
                while self.latest is None and self.running:
                    self.cond.wait(1.0)
                if not self.running:
                    return
                jpeg, meta = self.latest
                self.latest = None
            try:
                self._post(jpeg, meta)
                self.sent += 1
                backoff = 0.0
                if 'ok' not in self._said:
                    log(f'Connected: pictures are reaching {self.url.rsplit("/api", 1)[0]}')
                    self._said = {'ok'}
            except urllib.error.HTTPError as exc:
                self.failed += 1
                try:
                    msg = json.loads(exc.read().decode()).get('error', '')
                except Exception:
                    msg = ''
                self.last_error = f'{exc.code} {msg}'.strip()
                if exc.code not in self._said:
                    log(f'The computer said no ({exc.code}): {msg or exc.reason}')
                    self._said = {exc.code}
                backoff = min(10.0, backoff + 2.0)
            except Exception as exc:
                self.failed += 1
                self.last_error = str(exc)
                if 'net' not in self._said:
                    log(f'Cannot reach the computer ({exc}). Is server.py running, and on the same Wi-Fi?')
                    self._said = {'net'}
                backoff = min(10.0, backoff + 1.0)
            if backoff:
                time.sleep(backoff)


# ════════════════════════════════════════════════════════════════════
#  The node
# ════════════════════════════════════════════════════════════════════
class CameraNode:
    def __init__(self, cfg: dict, camera, server: str | None, key: str | None):
        self.cfg = cfg
        self.camera = camera
        self.store = PhotoStore(cfg['photo_dir'], float(cfg['keep_last_min']) * 60, float(cfg['min_free_mb']))
        self.uploader = Uploader(server, key, float(cfg['upload_timeout_sec']), self.store, camera.name) \
            if server else None
        self.interval = 1.0 / max(0.1, float(cfg['fps']))
        self.delete_every = float(cfg['delete_every_min']) * 60
        self.taken = 0
        self.errors = 0
        self.running = True
        self.started = time.time()

    def step(self, now: float | None = None) -> dict:
        """Take, save and hand over one picture. Returns its metadata."""
        now = now if now is not None else time.time()
        jpeg, extra = self.camera.capture()
        self.taken += 1
        frame_id = f'pi-{self.taken:06d}'
        path = self.store.save(jpeg, now, frame_id)
        meta = {'frame_id': frame_id, 'taken_at': now, 'path': str(path), **extra}
        if self.taken % 10 == 1:                      # checking the disk is cheap but not free
            meta['free_mb'] = self.store.free_mb()
            meta['photos_stored'] = self.store.count()
            if meta['free_mb'] < self.store.min_free_mb:
                n = self.store.free_up_space()
                log(f'Disk nearly full: deleted the {n} oldest pictures')
        if self.uploader:
            self.uploader.offer(jpeg, meta)
        return meta

    def maybe_cleanup(self, now: float) -> int | None:
        if self.delete_every > 0 and now - (self.store.last_cleanup or self.started) >= self.delete_every:
            n = self.store.cleanup(now)
            log(f'Cleanup: deleted {n} pictures (every {self.delete_every / 60:g} min). '
                f'{self.store.free_mb()} MB free.')
            return n
        return None

    def run(self, max_pictures: int | None = None):
        self.started = time.time()
        if self.uploader:
            self.uploader.start()
        last_status = self.started
        next_shot = self.started
        while self.running:
            now = time.time()
            if now < next_shot:
                time.sleep(min(0.05, next_shot - now))
                continue
            next_shot = max(next_shot + self.interval, now)   # don't try to "catch up" after a stall
            try:
                self.step(now)
            except Exception as exc:
                self.errors += 1
                if self.errors in (1, 10) or self.errors % 100 == 0:
                    log(f'Camera problem ({self.errors}): {exc}')
                time.sleep(0.5)
            self.maybe_cleanup(time.time())
            if time.time() - last_status >= 30:
                last_status = time.time()
                up = (f', sent {self.uploader.sent}, failed {self.uploader.failed}, skipped {self.uploader.skipped}'
                      if self.uploader else '')
                log(f'Pictures taken {self.taken}{up}; stored {self.store.count()}; '
                    f'{self.store.free_mb()} MB free')
            if max_pictures and self.taken >= max_pictures:
                break
        self.stop()

    def stop(self):
        self.running = False
        if self.uploader:
            self.uploader.running = False
        try:
            self.camera.close()
        except Exception:
            pass


def build_config(args) -> dict:
    cfg = dict(DEFAULTS)
    cfg.update({k: v for k, v in settings_from_repo().items() if k in DEFAULTS})
    if args.server and args.key and not args.offline:
        from_server = settings_from_server(args.server, args.key)
        cfg.update({k: v for k, v in from_server.items() if k in DEFAULTS})
    for k in DEFAULTS:
        v = getattr(args, k, None)
        if v is not None:
            cfg[k] = v
    return cfg


def parse_args(argv=None):
    p = argparse.ArgumentParser(description='Desk Talk camera node for the Raspberry Pi')
    p.add_argument('--server', help='your computer, e.g. http://192.168.1.20:5000 (printed by server.py)')
    p.add_argument('--key', help='pairing key (printed by server.py)')
    p.add_argument('--camera', default='auto', help='auto | picamera2 | usb[:index] | folder:<dir> | video:<file>')
    p.add_argument('--fps', type=float)
    p.add_argument('--width', type=int)
    p.add_argument('--height', type=int)
    p.add_argument('--jpeg-quality', dest='jpeg_quality', type=int)
    p.add_argument('--photo-dir', dest='photo_dir')
    p.add_argument('--delete-every-min', dest='delete_every_min', type=float)
    p.add_argument('--keep-last-min', dest='keep_last_min', type=float)
    p.add_argument('--min-free-mb', dest='min_free_mb', type=float)
    p.add_argument('--upload-timeout-sec', dest='upload_timeout_sec', type=float)
    p.add_argument('--offline', action='store_true', help='only take and store pictures; send nothing')
    p.add_argument('--max-pictures', type=int, help='stop after this many (for testing)')
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if not args.offline and not (args.server and args.key):
        print('Missing --server and --key. Start `python server.py` on your computer: it prints the full command.')
        return 2
    cfg = build_config(args)
    camera = open_camera(args.camera, cfg['width'], cfg['height'], cfg['jpeg_quality'])
    node = CameraNode(cfg, camera, None if args.offline else args.server, args.key)
    log(f'Camera: {camera.name}. {cfg["fps"]:g} pictures a second, saved in {node.store.folder}, '
        f'deleted every {cfg["delete_every_min"]:g} min.')
    if not args.offline:
        log(f'Sending to {args.server}')

    def _stop(*_):
        node.running = False
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    node.run(max_pictures=args.max_pictures)
    log(f'Stopped. {node.taken} pictures taken.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
