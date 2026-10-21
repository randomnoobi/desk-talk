"""
--self-test: checks the camera, the detector container, the Pi's temperature and that the computer
answers, says what it found in plain words, and stops. It only looks: it sends no pictures and
doesn't tell the container which model to use.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from desktalk_shared.detection import DetectorUnavailable
from desktalk_shared.detector_api import ServiceState
from raspberry_pi.camera.detector_client import DetectorClient
from raspberry_pi.camera.health import HealthReader, throttle_words
from raspberry_pi.camera.settings import CameraSettings, ComputerProblem, computer_config

log = logging.getLogger(__name__)

KB = 1024
START_DETECTOR = ('Start it on the Pi: cd raspberry_pi/detector_service && docker compose up -d '
                  '(raspberry_pi/detector_service/README.md has the first-time steps)')


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    message: str


def check_camera(open_camera: Callable[[], Any]) -> Check:
    try:
        camera = open_camera()
    except Exception as exc:
        return Check('camera', False, f'{exc}. For the camera module, rpicam-hello should show a picture; '
                                      'for a USB webcam add --camera usb')
    try:
        jpeg, lux = camera.capture()
    except Exception as exc:
        return Check('camera', False, f'{camera.name} opened but gave no picture: {exc}')
    finally:
        camera.close()
    light = f', light {lux:g} lux' if lux is not None else ''
    return Check('camera', True, f'{camera.name}: took a picture ({len(jpeg) // KB} KB{light})')


def check_detector(client: DetectorClient | None) -> Check:
    if client is None:
        return Check('detector', True, 'not used (--no-detector): the computer detects every picture')
    try:
        health = client.health()
    except DetectorUnavailable as exc:
        return Check('detector', False, f'{exc}. {START_DETECTOR}')
    if health.state is ServiceState.ERROR:
        return Check('detector', False, health.message or 'the detector has no working model')
    if health.state is ServiceState.READY and health.model:
        model = health.model
        return Check('detector', True, f'ready: {model.model} ({model.format}, {model.image_size} pixels)')
    note = {ServiceState.STARTING: 'the camera program tells it which model when it starts',
            ServiceState.LOADING: 'until it is ready, the computer detects the pictures'}.get(health.state, '')
    words = f'{health.message} ({note})' if health.message and note else health.message or note
    return Check('detector', True, f'{health.state.value}: {words}')


def check_temperature(reader: HealthReader, hot_warning_c: float) -> Check:
    temp = reader.cpu_temp_c()
    if temp is None:
        return Check('temperature', False, "couldn't read the processor's temperature (is this a Raspberry Pi?)")
    now, earlier = throttle_words(reader.throttled())
    problems = []
    if temp >= hot_warning_c:
        problems.append(f'too hot: {temp:g} °C (pi.hot_warning_c is {hot_warning_c:g}). A cooler or a fan helps')
    if now:
        problems.append('right now ' + ', '.join(now))
    if problems:
        return Check('temperature', False, '; '.join(problems))
    since = f' (since it started: {", ".join(earlier)})' if earlier else ''
    return Check('temperature', True, f'{temp:g} °C{since}')


def check_computer(server: str | None, key: str | None, timeout: float) -> Check:
    if not (server and key):
        return Check('computer', False, 'no --server and --key given (server.py on your computer prints them)')
    try:
        reply = computer_config(server, key, timeout)
    except ComputerProblem as exc:
        return Check('computer', False, str(exc))
    source = reply.get('camera_source')
    if source not in (None, 'pi'):
        return Check('computer', False, f'{server} answered, but it is set to camera.source: {source}. '
                                        'Set it to pi in config/settings.yaml on the computer and restart it.')
    return Check('computer', True, f'{server} answered, and takes its pictures from the Pi')


def run(open_camera: Callable[[], Any], client: DetectorClient | None, reader: HealthReader,
        settings: CameraSettings, *, server: str | None, key: str | None) -> int:
    """All the checks, logged one per line. 0 when everything is fine, 1 when something isn't."""
    checks = [check_camera(open_camera), check_detector(client), check_temperature(reader, settings.hot_warning_c),
              check_computer(server, key, settings.upload_timeout_sec)]
    for check in checks:
        log.log(logging.INFO if check.ok else logging.WARNING, '%-8s %-12s %s', 'OK' if check.ok else 'PROBLEM',
                check.name, check.message)
    problems = sum(not check.ok for check in checks)
    log.info('All good.' if not problems else f'{problems} of {len(checks)} checks found a problem.')
    return 1 if problems else 0
