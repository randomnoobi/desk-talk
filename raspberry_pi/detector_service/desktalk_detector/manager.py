"""
The model manager: keeps the model that answers /detect, and loads new ones.

- PUT /model calls want(); a background thread loads the model (converting it to ncnn the first time)
  while the last good model keeps answering.
- A model that fails to load is tried again after 5 s, 10 s, 20 s ... (settings), and /health says why.
- One detection at a time: Ultralytics breaks when two threads use a model at once. A picture sent while
  another is being detected is turned away at once (DetectorBusy, 429): the camera program has already
  given up on it, and keeping it waiting would only keep the Pi busy and hot.
- The model asked for last is remembered in the models folder, so a restarted container loads it by itself.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np

from desktalk_detector.model_folder import DEVICE, ModelFolder
from desktalk_shared.detection import DetectorUnavailable
from desktalk_shared.detector_api import DetectorHealth, DetectReply, ModelSettings, ServiceState
from desktalk_shared.ultralytics_results import detections_from_results

log = logging.getLogger(__name__)

BUSY = 'The detector is still working on another picture.'


class DetectorBusy(Exception):
    """A picture came while another one was being detected. It is not kept: the camera sends a newer one."""


def describe(s: ModelSettings) -> str:
    """e.g. "yolo11s.pt (ncnn fp16, 480)"."""
    return f'{s.model} ({s.format}{" fp16" if s.half else ""}, {s.image_size})'


def _note(s: ModelSettings) -> str:
    """What's worth knowing about how this model runs (decision 9 in docs/plans/2026-10-21-refactor-plan.md)."""
    if s.format == 'ncnn' and 'yolo26' in s.model.lower():
        return (" Note: ncnn can't run YOLO26's end-to-end head, so Ultralytics runs this model with its other head "
                'plus the usual overlap filter (NMS). It finds slightly different boxes than the end-to-end head.')
    return ''


@dataclass(frozen=True)
class _Loaded:
    settings: ModelSettings
    model: Any


class ModelManager:
    def __init__(self, folder: ModelFolder, *, retry_first_sec: float, retry_max_sec: float,
                 clock: Callable[[], float] = time.perf_counter):
        self._folder = folder
        self._clock = clock
        self._retry_first, self._retry_max = retry_first_sec, retry_max_sec
        self._one_at_a_time = threading.Lock()  # Ultralytics breaks when two threads predict at once
        self._lock = threading.Lock()           # guards the fields below
        self._current: _Loaded | None = None    # the model answering /detect
        self._wanted: ModelSettings | None = None   # the model to load next
        self._problem: str | None = None        # why the wanted model didn't load
        self._wait = retry_first_sec            # before the next try, if this one fails
        self._detections_done = 0
        self._last_detect_ms: float | None = None
        self._wake = threading.Event()          # a new model was asked for, or it's time to stop
        self._stopping = False

    def health(self) -> DetectorHealth:
        with self._lock:
            current, wanted, problem = self._current, self._wanted, self._problem
            counts = {'detections_done': self._detections_done, 'last_detect_ms': self._last_detect_ms}
        if wanted is not None:
            doing = problem or f'Loading {describe(wanted)}.' + (
                ' The first time, it is converted to ncnn, which can take a few minutes on the Pi.'
                if wanted.format == 'ncnn' else '')
            if current is None:
                state = ServiceState.ERROR if problem else ServiceState.LOADING
                return DetectorHealth(state=state, model=None, message=doing, **counts)
            return DetectorHealth(state=ServiceState.LOADING, model=current.settings,
                                  message=f'{doing} {describe(current.settings)} answers meanwhile.', **counts)
        if current is not None:
            return DetectorHealth(state=ServiceState.READY, model=current.settings,
                                  message=f'Running {describe(current.settings)}.{_note(current.settings)}', **counts)
        return DetectorHealth(state=ServiceState.STARTING, model=None,
                              message='Waiting to be told which model to run (the camera program sends PUT /model).')

    def want(self, settings: ModelSettings) -> DetectorHealth:
        """Load this model next. The model running now keeps answering until it's ready.
        Raises ValueError (plain words) for settings that can't name a model."""
        self._folder.check(settings)
        try:
            self._folder.remember(settings)     # so a restarted container loads it again by itself
        except OSError as exc:
            log.warning('could not remember the model in the models folder: %s', exc)
        with self._lock:
            running = self._current is not None and self._current.settings == settings
            if settings != self._wanted:
                self._wanted, self._problem, self._wait = None if running else settings, None, self._retry_first
        self._wake.set()
        return self.health()

    def start(self) -> None:
        """Load the model asked for last time (if any), and from now on load models in a background thread."""
        remembered = self._folder.remembered()
        if remembered is not None:
            try:
                self.want(remembered)
            except ValueError as exc:
                log.warning('not loading the remembered model: %s', exc)
        self._stopping = False
        threading.Thread(target=self._load_in_background, name='model loader', daemon=True).start()

    def stop(self) -> None:
        """Stop the background thread (it finishes a load that's under way first; it doesn't hold up the exit)."""
        self._stopping = True
        self._wake.set()

    def _load_in_background(self) -> None:
        while not self._stopping:
            self._wake.clear()
            wait = self.load_step()
            if wait != 0:
                self._wake.wait(wait)           # None: until another model is asked for

    def load_step(self) -> float | None:
        """One try at loading the wanted model. Returns None when there's nothing more to do,
        or how many seconds to wait before trying again."""
        with self._lock:
            wanted = self._wanted
        if wanted is None:
            return None
        try:
            model = self._folder.open(wanted)
            # Ultralytics sets itself up on the first picture (slow): do that now, not on the camera's first one.
            blank = np.zeros((wanted.image_size, wanted.image_size, 3), np.uint8)
            model.predict(source=blank, conf=wanted.confidence, imgsz=wanted.image_size, device=DEVICE,
                          verbose=False)
        except Exception as exc:    # Ultralytics can raise almost anything for a missing or broken model
            with self._lock:
                if self._wanted != wanted:
                    return 0                    # asked for another model meanwhile: try that one now
                wait = self._wait
                self._wait = min(wait * 2, self._retry_max)
                self._problem = problem = f'Could not load {describe(wanted)}: {exc}. Trying again in {wait:g} s.'
            log.warning('%s', problem, exc_info=True)
            return wait
        with self._lock:
            if self._wanted != wanted:
                return 0                        # asked for another model meanwhile: load that one now
            self._current, self._wanted, self._problem = _Loaded(wanted, model), None, None
        log.info('running %s', describe(wanted))
        return None

    def detect(self, picture: Any) -> DetectReply:
        with self._lock:
            current = self._current
        if current is None:
            raise DetectorUnavailable(f'The detector has no model yet. {self.health().message}')
        s = current.settings
        if not self._one_at_a_time.acquire(blocking=False):
            raise DetectorBusy(BUSY)
        try:
            start = self._clock()
            results = current.model.predict(source=picture, conf=s.confidence, imgsz=s.image_size, device=DEVICE,
                                            verbose=False)
            found = detections_from_results(results)
            ms = round((self._clock() - start) * 1000, 1)
        finally:
            self._one_at_a_time.release()
        with self._lock:
            self._detections_done += 1
            self._last_detect_ms = ms
        height, width = picture.shape[:2]
        return DetectReply(detections=found, detect_ms=ms, width=width, height=height, model=describe(s))
