"""
Object detection on this computer with Ultralytics YOLO: for the phone and the webcam, and as the
backup when the Pi's own detector is off.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from desktalk.config import DetectorSettings
from desktalk.domain import Detection, DetectorUnavailable
from desktalk.runtime.errors import plain_error
from desktalk_shared.ultralytics_results import detections_from_results

log = logging.getLogger(__name__)


def load_yolo(path: Path) -> Any:
    """The real model loader: downloads the model into its folder the first time (or run
    scripts/download_models.py), then opens it with Ultralytics."""
    from ultralytics import YOLO
    path = Path(path)
    if not path.is_file():
        from ultralytics.utils.downloads import attempt_download_asset
        path.parent.mkdir(parents=True, exist_ok=True)
        log.info('Downloading %s into %s', path.name, path.parent)
        got = Path(attempt_download_asset(str(path)))
        if not got.is_file():
            raise FileNotFoundError(f'Could not download {path.name}. Run: uv run python scripts/download_models.py')
        path = got
    return YOLO(str(path))


def pick_device(wanted: str, torch: Any = None) -> str:
    """'auto' = the NVIDIA GPU ('cuda:0') or the Apple one ('mps') when there is one, else 'cpu'.
    Anything else is used as written. (`torch` is for tests; normally PyTorch itself is asked.)"""
    wanted = wanted.lower()
    if wanted != 'auto':
        return wanted
    if torch is None:
        import torch
    if torch.cuda.is_available():
        return 'cuda:0'
    if torch.backends.mps.is_available():
        return 'mps'
    return 'cpu'


class UltralyticsDetector:
    """A Detector (desktalk/ports.py). `load_model(path)` gives a YOLO-like object (`names`, `predict()`)."""

    def __init__(self, model_path: Path, settings: DetectorSettings, *, end2end: bool, rect: bool,
                 load_model: Callable[[Path], Any] = load_yolo):
        self._path = Path(model_path)
        self._settings = settings
        self._end2end = end2end
        self._rect = rect
        self._device = pick_device(settings.device)
        self._model: Any = None
        self._names: dict[int, str] = {}
        self._problem = ''
        # Ultralytics builds its predictor on the first call; two threads at once break it.
        self._one_at_a_time = threading.Lock()
        try:
            self._model = load_model(self._path)
            self._names = dict(self._model.names)
        except Exception as exc:
            self._problem = f'The detection model {self._path.name} could not be loaded ({plain_error(exc)})'
            log.error('%s', self._problem)

    @property
    def label(self) -> str:
        if self._model is None:
            return f'{self._path.name} (not loaded)'
        return f'{self._path.name} ({len(self._names)} kinds of objects, {self._device})'

    def warmup(self) -> None:
        """One detection on a blank picture at start, so the first real picture isn't slow."""
        size = self._settings.image_size
        try:
            self.detect(np.zeros((size, size, 3), np.uint8))
        except DetectorUnavailable as exc:
            log.warning('Could not warm up the detector: %s', exc)

    def _predict(self, picture: Any) -> Any:
        s = self._settings
        return self._model.predict(source=picture, conf=s.confidence, imgsz=s.image_size, device=self._device,
                                   verbose=False, end2end=self._end2end, rect=self._rect)

    def detect(self, picture: Any) -> list[Detection]:
        """Detections in a BGR picture, boxes in its pixels. Raises DetectorUnavailable when it can't answer."""
        if self._model is None:
            raise DetectorUnavailable(self._problem)
        with self._one_at_a_time:
            return self._detect(picture)

    def _detect(self, picture: Any) -> list[Detection]:
        try:
            try:
                results = self._predict(picture)
            except Exception as exc:
                if self._device == 'cpu':
                    raise
                log.warning('Detection on %s failed (%s); using the CPU from now on', self._device, plain_error(exc))
                self._device = 'cpu'
                results = self._predict(picture)
            return detections_from_results(results, self._names)
        except Exception as exc:
            raise DetectorUnavailable(f'Detection with {self._path.name} failed ({plain_error(exc)})') from exc
