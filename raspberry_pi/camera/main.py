"""
The camera program's loop: take a picture every 1/fps seconds, save it on the Pi, ask the detector
container what's in it (one picture at a time), and send it to the computer.

Three background threads keep the camera's pace steady: the detector worker, the uploader, and the
settings watcher. Each one only waits on its own job, so a slow detector or Wi-Fi never holds up the camera.
"""

from __future__ import annotations

import argparse
import logging
import signal
import threading
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from desktalk_shared.wire import DetectorState, PiHealth, UploadInfo
from raspberry_pi.camera import self_test
from raspberry_pi.camera.cameras import CameraError, Picture, open_camera
from raspberry_pi.camera.clock import Clock, SystemClock
from raspberry_pi.camera.detector_client import DetectorClient, DetectorWorker, NoDetector, Verdict
from raspberry_pi.camera.health import HealthReader
from raspberry_pi.camera.photo_store import PhotoStore
from raspberry_pi.camera.settings import (
    CameraSettings,
    ComputerProblem,
    SettingsError,
    SettingsWatcher,
    computer_config,
    from_layers,
    merge,
    read_repo_settings,
    upload_timeout,
)
from raspberry_pi.camera.uploader import Uploader

log = logging.getLogger(__name__)

REPO_SETTINGS = Path(__file__).resolve().parents[2] / 'config' / 'settings.yaml'
RUN_COMMAND = 'python3 -m raspberry_pi.camera --server http://192.168.1.20:5000 --key AbC123xY'
NO_DETECTOR = 'detection on the Pi is turned off (--no-detector)'
OFFLINE = 'nothing is sent (--offline)'
# Settings a flag can override (flag --jpeg-quality → pi.jpeg_quality)
FLAG_SETTINGS = ('fps', 'width', 'height', 'jpeg_quality', 'photo_dir', 'delete_every_min', 'keep_last_min',
                 'min_free_mb', 'upload_timeout_sec')


class CameraProgram:
    def __init__(self, *, camera: Any, store: PhotoStore, health: HealthReader, uploader: Uploader | None,
                 settings: CameraSettings, clock: Clock):
        self.camera = camera
        self.store = store
        self.health = health
        self.uploader = uploader
        self.settings = settings
        self.clock = clock
        self.detector: DetectorWorker | NoDetector = NoDetector(OFFLINE)    # build_program sets the real one
        self.taken = 0
        self.errors = 0
        self.verdicts: Counter[DetectorState] = Counter()
        self.health_now = PiHealth(camera=camera.name)
        self.running = True
        self._started = clock.now()
        self._next_health = clock.monotonic()
        self._next_status = clock.monotonic() + settings.status_every_sec
        self._problem = ''
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
            self.maybe_cleanup(self.clock.now())
            self.maybe_log_status()
            if max_pictures and self.taken >= max_pictures:
                break

    def step(self) -> Picture:
        """Take, save and hand on one picture."""
        now = self.clock.now()
        jpeg, lux = self.camera.capture()
        self.taken += 1
        picture = Picture(jpeg=jpeg, frame_id=f'pi-{self.taken:06d}', taken_at=now, lux=lux)
        self.store.save(jpeg, now, picture.frame_id)
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

    def maybe_cleanup(self, now: float) -> int | None:
        """Delete the stored pictures every pi.delete_every_min. Returns how many, or None if it's not time yet."""
        last = self.store.last_cleanup if self.store.last_cleanup is not None else self._started
        if now - last < self.settings.delete_every_min * 60:
            return None
        removed = self.store.cleanup(now)
        log.info('Cleanup: deleted %d pictures (every %g min). %d MB free.', removed, self.settings.delete_every_min,
                 self.store.free_mb())
        return removed

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
        self.health_now = self.health.read(self.camera.name, self.store)
        if self.health_now.disk_free_mb < self.store.min_free_mb:
            removed = self.store.free_up_space()
            log.warning('Disk nearly full: deleted the %d oldest pictures', removed)
            self.health_now = self.health.read(self.camera.name, self.store)


class WorkerThread(threading.Thread):
    """Calls tick() over and over in a thread of its own, waiting the seconds it returns
    (None: until `wake` is set). A tick that fails is logged, and the next one comes after a short wait."""

    def __init__(self, name: str, tick: Callable[[], float | None], wake: threading.Event,
                 pause_after_error_sec: float):
        super().__init__(name=name, daemon=True)
        self.tick = tick
        self.wake = wake
        self.pause_after_error_sec = pause_after_error_sec
        self._stopping = False

    def run(self) -> None:
        while not self._stopping:
            self.wake.clear()
            try:
                wait = self.tick()
            except Exception:
                log.exception('%s had a problem; trying again', self.name)
                wait = self.pause_after_error_sec
            if not self._stopping:
                self.wake.wait(wait)

    def stop(self) -> None:
        self._stopping = True
        self.wake.set()


@dataclass
class Workers:
    """The parts that run in threads of their own (None when not used)."""

    detector: DetectorWorker | None
    uploader: Uploader | None
    settings: SettingsWatcher | None

    def threads(self, pause_after_error_sec: float) -> list[WorkerThread]:
        threads = []
        if self.detector:
            threads.append(WorkerThread('detector', self.detector.tick, self.detector.wake, pause_after_error_sec))
        if self.uploader:
            threads.append(WorkerThread('uploader', self.uploader.tick, self.uploader.wake, pause_after_error_sec))
        if self.settings:
            threads.append(WorkerThread('settings', self.settings.tick, threading.Event(), pause_after_error_sec))
        return threads


def build_program(settings: CameraSettings, camera: Any, *, server: str | None, key: str | None, offline: bool,
                  no_detector: bool, clock: Clock, sys_root: str | Path = '/',
                  reload: Callable[[], CameraSettings] | None = None) -> tuple[CameraProgram, Workers]:
    """All the parts, connected. `reload` asks the computer for its settings again (for the settings watcher)."""
    store = PhotoStore(settings.photo_dir, keep_last_sec=settings.keep_last_min * 60, min_free_mb=settings.min_free_mb)
    uploader = None
    if not offline:
        uploader = Uploader(server or '', key or '', timeout_sec=settings.upload_timeout_sec,
                            retry_start_sec=settings.upload_retry_start_sec,
                            retry_max_sec=settings.upload_retry_max_sec, clock=clock)
    program = CameraProgram(camera=camera, store=store, health=HealthReader(sys_root), uploader=uploader,
                            settings=settings, clock=clock)
    worker = client = None
    if offline:
        program.detector = NoDetector(OFFLINE)
    elif no_detector:
        program.detector = NoDetector(NO_DETECTOR)
    else:
        client = DetectorClient(settings.detector, clock)
        worker = program.detector = DetectorWorker(client, program.deliver)
    watcher = None
    if reload is not None and not offline:
        watcher = SettingsWatcher(reload, settings, client, every_sec=settings.settings_check_every_sec, clock=clock)
    return program, Workers(worker, uploader, watcher)


# ── settings and flags ────────────────────────────────────────────────
def flag_settings(args: argparse.Namespace) -> dict[str, Any]:
    return {name: getattr(args, name) for name in FLAG_SETTINGS if getattr(args, name) is not None}


def load_settings(args: argparse.Namespace, repo_settings: Path, *, need_computer: bool = False) -> CameraSettings:
    """config/settings.yaml in the repo → the computer's settings → flags. If the computer can't be reached,
    the repo's settings are used (unless need_computer: then ComputerProblem is raised)."""
    repo = read_repo_settings(repo_settings)
    flags = flag_settings(args)
    computer: dict[str, Any] = {}
    if not args.offline and args.server and args.key:
        try:
            reply = computer_config(args.server, args.key, timeout=upload_timeout(merge(repo, flags)))
            computer = reply.get('pi') or {}
        except ComputerProblem as exc:
            if need_computer:
                raise
            log.warning('%s Using the settings in config/settings.yaml on the Pi for now.', exc)
    return from_layers(repo, computer, flags)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog='python3 -m raspberry_pi.camera', formatter_class=argparse.RawDescriptionHelpFormatter,
        description=f'Desk Talk camera program for the Raspberry Pi. In the desk-talk folder on the Pi, run:\n\n'
                    f'    {RUN_COMMAND}\n\n'
                    f"server.py on your computer prints this command with your computer's address and pairing key.\n"
                    f'Settings come from config/settings.yaml (pi:) on your computer; these flags override them.')
    p.add_argument('--server', help='your computer, e.g. http://192.168.1.20:5000 (printed by server.py)')
    p.add_argument('--key', help='pairing key (printed by server.py)')
    p.add_argument('--camera', default='auto', help='auto | picamera2 | usb[:index] | folder:<dir> | video:<file>')
    p.add_argument('--fps', type=float, help='pictures per second')
    p.add_argument('--width', type=int, help='picture width')
    p.add_argument('--height', type=int, help='picture height')
    p.add_argument('--jpeg-quality', dest='jpeg_quality', type=int)
    p.add_argument('--photo-dir', dest='photo_dir', help='where the pictures are saved on the Pi')
    p.add_argument('--delete-every-min', dest='delete_every_min', type=float, help='delete saved pictures this often')
    p.add_argument('--keep-last-min', dest='keep_last_min', type=float,
                   help='minutes of pictures a cleanup keeps (0 = delete them all)')
    p.add_argument('--min-free-mb', dest='min_free_mb', type=float,
                   help='if the disk gets this full, delete the oldest pictures right away')
    p.add_argument('--upload-timeout-sec', dest='upload_timeout_sec', type=float)
    p.add_argument('--no-detector', action='store_true',
                   help="don't use the detector container: the computer detects every picture")
    p.add_argument('--offline', action='store_true', help='only take and save pictures; send nothing')
    p.add_argument('--self-test', action='store_true',
                   help='check the camera, the detector container, the temperature and the computer, then stop')
    p.add_argument('--max-pictures', type=int, help='stop after this many (for testing)')
    return p.parse_args(argv)


def main(argv: list[str] | None = None, *, repo_settings: Path = REPO_SETTINGS, clock: Clock | None = None,
         sys_root: str | Path = '/') -> int:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s', datefmt='%H:%M:%S')
    args = parse_args(argv)
    if not args.offline and not args.self_test and not (args.server and args.key):
        log.error('Missing --server and --key. Start server.py on your computer: it prints the full command.')
        return 2
    clock = clock or SystemClock()
    try:
        settings = load_settings(args, repo_settings)
    except SettingsError as exc:
        log.error('Settings problem: %s', exc)
        return 2

    def camera():
        return open_camera(args.camera, width=settings.width, height=settings.height, quality=settings.jpeg_quality,
                           warmup_sec=settings.camera_warmup_sec, clock=clock)

    if args.self_test:
        client = None if args.no_detector else DetectorClient(settings.detector, clock)
        return self_test.run(camera, client, HealthReader(sys_root), settings, server=args.server, key=args.key)
    try:
        cam = camera()
    except CameraError as exc:
        log.error('Camera problem: %s', exc)
        return 1

    def reload() -> CameraSettings:
        return load_settings(args, repo_settings, need_computer=True)

    program, workers = build_program(settings, cam, server=args.server, key=args.key, offline=args.offline,
                                     no_detector=args.no_detector, clock=clock, sys_root=sys_root, reload=reload)
    log.info('Camera: %s. %g pictures a second, saved in %s, deleted every %g min.', cam.name, settings.fps,
             program.store.folder, settings.delete_every_min)
    if not args.offline:
        log.info('Sending to %s%s', args.server, '' if workers.detector else f' ({NO_DETECTOR})')
    threads = workers.threads(settings.pause_after_error_sec)
    for thread in threads:
        thread.start()

    def stop(*_):
        program.running = False

    before = {sig: signal.signal(sig, stop) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        program.run(max_pictures=args.max_pictures)
    finally:
        for sig, handler in before.items():
            signal.signal(sig, handler)
        for thread in threads:
            thread.stop()
        for thread in threads:
            thread.join(max(settings.upload_timeout_sec, settings.detector.wait_per_picture_sec))
        program.close()
    log.info('Stopped. %d pictures taken.', program.taken)
    return 0
