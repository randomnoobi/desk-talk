"""
The camera program's command line: python3 -m raspberry_pi.camera (in the desk-talk folder). --help shows
the flags. Settings come from config/settings.yaml (pi:), then the computer's settings, then the flags.
"""

from __future__ import annotations

import argparse
import logging
import signal
from pathlib import Path
from typing import Any

from raspberry_pi.camera import self_test
from raspberry_pi.camera.cameras import CameraError, open_camera
from raspberry_pi.camera.clock import Clock, SystemClock
from raspberry_pi.camera.detector_client import DetectorClient
from raspberry_pi.camera.health import HealthReader
from raspberry_pi.camera.main import NO_DETECTOR
from raspberry_pi.camera.settings import (
    CameraSettings,
    ComputerProblem,
    SettingsError,
    computer_config,
    error_text_chars,
    from_layers,
    merge,
    read_repo_settings,
    upload_timeout,
)
from raspberry_pi.camera.workers import build_program

log = logging.getLogger(__name__)

REPO_SETTINGS = Path(__file__).resolve().parents[2] / 'config' / 'settings.yaml'
RUN_COMMAND = 'python3 -m raspberry_pi.camera --server http://192.168.1.20:5000 --key AbC123xY'
# Settings a flag can override (flag --jpeg-quality → pi.jpeg_quality)
FLAG_SETTINGS = ('fps', 'width', 'height', 'jpeg_quality', 'photo_dir', 'delete_every_min', 'keep_last_min',
                 'min_free_mb', 'upload_timeout_sec')


def flag_settings(args: argparse.Namespace) -> dict[str, Any]:
    return {name: getattr(args, name) for name in FLAG_SETTINGS if getattr(args, name) is not None}


def load_settings(args: argparse.Namespace, repo_settings: Path, *, need_computer: bool = False) -> CameraSettings:
    """config/settings.yaml in the repo → the computer's settings → flags. If the computer can't be reached,
    the repo's settings are used (unless need_computer: then ComputerProblem is raised)."""
    repo = read_repo_settings(repo_settings)
    flags = flag_settings(args)
    computer: dict[str, Any] = {}
    if not args.offline and args.server and args.key:
        local = merge(repo, flags)
        try:
            reply = computer_config(args.server, args.key, timeout=upload_timeout(local),
                                    error_text_chars=error_text_chars(local))
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
    p.add_argument('--camera', help='auto | picamera2 | usb[:index] | folder:<dir> | video:<file> '
                                    '(if not given: pi.camera in config/settings.yaml)')
    p.add_argument('--fps', type=float, help='pictures per second')
    p.add_argument('--width', type=int, help='picture width')
    p.add_argument('--height', type=int, help='picture height')
    p.add_argument('--jpeg-quality', dest='jpeg_quality', type=int)
    p.add_argument('--photo-dir', dest='photo_dir', help='where the pictures are saved on the Pi')
    p.add_argument('--delete-every-min', dest='delete_every_min', type=float,
                   help='delete saved pictures this often (0 = never)')
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
        return open_camera(args.camera or settings.camera, width=settings.width, height=settings.height,
                           quality=settings.jpeg_quality, warmup_sec=settings.camera_warmup_sec, clock=clock)

    if args.self_test:
        client = None if args.no_detector else DetectorClient(settings.detector, clock,
                                                              error_text_chars=settings.error_text_chars)
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
    deleted = f'deleted every {settings.delete_every_min:g} min' if settings.delete_every_min else 'never deleted'
    log.info('Camera: %s. %g pictures a second, saved in %s, %s.', cam.name, settings.fps, program.store.folder,
             deleted)
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
