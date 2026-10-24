"""
The parts of the camera program that run in threads of their own (the detector worker, the uploader, the
settings watcher, the watchdog and the cleaner), and build_program, which connects them to the loop in
main.py. Each thread is a desktalk_shared WorkerThread: one that crashes is logged and goes on after
pi.pause_after_error_sec.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from desktalk_shared.backoff import Backoff
from desktalk_shared.workers import WorkerThread
from raspberry_pi.camera.cleaner import Cleaner
from raspberry_pi.camera.clock import Clock
from raspberry_pi.camera.detector_client import DetectorClient, DetectorWorker, NoDetector
from raspberry_pi.camera.health import HealthReader
from raspberry_pi.camera.main import NO_DETECTOR, OFFLINE, CameraProgram
from raspberry_pi.camera.photo_store import PhotoStore
from raspberry_pi.camera.settings import CameraSettings, SettingsWatcher
from raspberry_pi.camera.uploader import Uploader
from raspberry_pi.camera.watchdog import Watchdog


@dataclass
class Workers:
    """The parts that run in threads of their own (None when not used)."""

    detector: DetectorWorker | None
    uploader: Uploader | None
    settings: SettingsWatcher | None
    watchdog: Watchdog
    cleaner: Cleaner

    def threads(self, pause_after_error_sec: float) -> list[WorkerThread]:
        """One thread per part. After a crash each waits pause_after_error_sec (the same every time)."""

        def thread(name: str, step: Callable[[], float | None],
                   wake: threading.Event | None = None) -> WorkerThread:
            return WorkerThread(name, step, Backoff(pause_after_error_sec, pause_after_error_sec), wake=wake)

        threads = [thread('watchdog', self.watchdog.tick), thread('cleanup', self.cleaner.tick, self.cleaner.wake)]
        if self.detector:
            threads.append(thread('detector', self.detector.tick, self.detector.wake))
        if self.uploader:
            threads.append(thread('uploader', self.uploader.tick, self.uploader.wake))
        if self.settings:
            threads.append(thread('settings', self.settings.tick))
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
                            retry_max_sec=settings.upload_retry_max_sec, error_text_chars=settings.error_text_chars,
                            clock=clock)
    program = CameraProgram(camera=camera, store=store, health=HealthReader(sys_root), uploader=uploader,
                            settings=settings, clock=clock)
    worker = client = None
    if offline:
        program.detector = NoDetector(OFFLINE)
    elif no_detector:
        program.detector = NoDetector(NO_DETECTOR)
    else:
        client = DetectorClient(settings.detector, clock, error_text_chars=settings.error_text_chars)
        worker = program.detector = DetectorWorker(client, program.deliver)
    watcher = None
    if reload is not None and not offline:
        watcher = SettingsWatcher(reload, settings, client, every_sec=settings.settings_check_every_sec, clock=clock)
    program.watchdog = Watchdog(settings.restart_after_missed_pictures / settings.fps, clock)
    program.cleaner = Cleaner(store, settings.delete_every_min, clock)
    return program, Workers(worker, uploader, watcher, program.watchdog, program.cleaner)
