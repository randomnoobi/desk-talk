"""
The models folder (a Docker volume, so it survives restarts and rebuilds):

    yolo11s.pt                      the model, copied in or downloaded by Ultralytics the first time
    yolo11s_480_fp16_ncnn_model/    its ncnn version for 480-pixel pictures, made the first time it's asked for
    last_model.json                 the model the camera program asked for last, loaded again after a restart
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from desktalk_shared.detector_api import ModelSettings

log = logging.getLogger(__name__)

DEVICE = 'cpu'                  # the Pi has no GPU that Ultralytics can use
MEMORY_FILE = 'last_model.json'


def ultralytics_yolo(path: str, task: str | None = None) -> Any:
    """ultralytics.YOLO, with Ultralytics' automatic pip installs switched off: a missing package
    should fail with a clear message, not get installed inside the container while it runs."""
    os.environ['YOLO_AUTOINSTALL'] = 'false'
    from ultralytics import YOLO
    return YOLO(path, task=task)


class ModelFolder:
    def __init__(self, folder: Path, yolo: Callable[..., Any] = ultralytics_yolo):
        self.folder = Path(folder)
        self._yolo = yolo           # called like ultralytics.YOLO(path, task=None)

    def check(self, settings: ModelSettings) -> None:
        """Raises ValueError (plain words) if the settings can't name a model here."""
        if not settings.model.endswith('.pt'):
            raise ValueError(f'model must be the name of a .pt file, e.g. yolo11s.pt (got {settings.model!r})')

    def open(self, settings: ModelSettings) -> Any:
        """The model, ready for predict(). Converts the .pt to ncnn first if that hasn't been done yet
        for this picture size and precision (takes a while: about a minute or more on the Pi)."""
        pt = self.folder / Path(settings.model).name
        if settings.format == 'pt':
            return self._yolo(str(pt))
        ncnn = self.folder / f'{pt.stem}_{settings.image_size}_{"fp16" if settings.half else "fp32"}_ncnn_model'
        if not ncnn.is_dir():
            log.info('converting %s to ncnn (%d pixels%s)', pt.name, settings.image_size,
                     ', fp16' if settings.half else '')
            made = self._yolo(str(pt)).export(format='ncnn', imgsz=settings.image_size, half=settings.half,
                                              device=DEVICE)
            # Moved in under a temporary name and renamed at the end, so a crash halfway never
            # leaves a folder that looks finished.
            tmp = ncnn.with_name(ncnn.name + '.tmp')
            shutil.rmtree(tmp, ignore_errors=True)
            shutil.move(str(made), str(tmp))
            tmp.rename(ncnn)
        return self._yolo(str(ncnn), task='detect')

    def remember(self, settings: ModelSettings) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        tmp = self.folder / (MEMORY_FILE + '.tmp')
        tmp.write_text(json.dumps(settings.to_dict()), encoding='utf-8')
        tmp.replace(self.folder / MEMORY_FILE)

    def remembered(self) -> ModelSettings | None:
        path = self.folder / MEMORY_FILE
        if not path.is_file():
            return None
        try:
            return ModelSettings.from_dict(json.loads(path.read_text(encoding='utf-8')))
        except ValueError as exc:
            log.warning('ignoring %s, it is broken: %s', path, exc)
            return None
