"""
Helpers for the detector service's tests.

FakeYolo stands in for Ultralytics' YOLO: no PyTorch, no model files read.

    yolo = FakeYolo([('cup', 0.9, (10, 20, 50, 60))])
    model = yolo('models/yolo11n.pt')              # like ultralytics.YOLO(path, task=None)
    model.predict(source=picture, conf=0.35, imgsz=640, device='cpu', verbose=False)
    model.export(format='ncnn', imgsz=640, half=True, device='cpu')

Like the real one it fails to open a model file that isn't there, drops boxes below `conf`, and
exports to a `<name>_ncnn_model` folder next to the .pt. It records what it was asked to do.

InProcessServiceDetector makes the service (through FastAPI's TestClient) look like a desktalk Detector,
so it can run the Detector contract.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import cv2
from fastapi.testclient import TestClient

from desktalk_shared.detection import Detection, DetectorUnavailable
from desktalk_shared.detector_api import DetectReply


class Column(list):
    """Stands in for a tensor column: has .tolist() like torch / numpy."""

    def tolist(self):
        return list(self)


@dataclass
class FakeYolo:
    boxes: Sequence[tuple[str, float, tuple[float, float, float, float]]] = ()
    fail_open: str | None = None           # opening any model raises RuntimeError(this)
    fail_export: str | None = None         # exporting raises RuntimeError(this), after leaving a half-made folder
    during_predict: Callable[[], None] | None = None    # called inside every predict()
    opened: list[tuple[Path, str | None]] = field(default_factory=list)
    exports: list[dict[str, Any]] = field(default_factory=list)
    predictions: list[dict[str, Any]] = field(default_factory=list)

    def __call__(self, path: str, task: str | None = None) -> FakeModel:
        self.opened.append((Path(path), task))
        if self.fail_open:
            raise RuntimeError(self.fail_open)
        if not Path(path).exists():
            raise FileNotFoundError(f'{path} does not exist')
        return FakeModel(self, Path(path))


class FakeModel:
    def __init__(self, maker: FakeYolo, path: Path):
        self.maker, self.path = maker, path
        self.names = dict(enumerate(sorted({label for label, _, _ in maker.boxes})))

    def predict(self, *, source, conf, imgsz, device, verbose):
        self.maker.predictions.append({'model': self.path, 'shape': source.shape, 'conf': conf, 'imgsz': imgsz,
                                       'device': device})
        if self.maker.during_predict:
            self.maker.during_predict()
        number = {name: k for k, name in self.names.items()}
        kept = [b for b in self.maker.boxes if b[1] >= conf]
        boxes = SimpleNamespace(xyxy=Column([list(box) for _, _, box in kept]), conf=Column([c for _, c, _ in kept]),
                                cls=Column([float(number[label]) for label, _, _ in kept]))
        return [SimpleNamespace(boxes=boxes, names=self.names, orig_shape=source.shape[:2])]

    def export(self, *, format, imgsz, half, device):
        self.maker.exports.append({'model': self.path, 'format': format, 'imgsz': imgsz, 'half': half,
                                   'device': device})
        folder = self.path.with_name(f'{self.path.stem}_{format}_model')
        folder.mkdir(exist_ok=True)
        if self.maker.fail_export:
            raise RuntimeError(self.maker.fail_export)
        for name in ('model.ncnn.param', 'model.ncnn.bin', 'metadata.yaml'):
            (folder / name).write_text(f'{name} for {self.path.name} at {imgsz}')
        return str(folder)


class InProcessServiceDetector:
    """picture → JPEG → POST /detect → DetectReply → Detections, like the camera program's client does."""

    def __init__(self, client: TestClient):
        self._client = client

    @property
    def label(self) -> str:
        return 'detector service (in-process)'

    def detect(self, picture) -> list[Detection]:
        jpeg = cv2.imencode('.jpg', picture)[1].tobytes()
        r = self._client.post('/detect', content=jpeg, headers={'Content-Type': 'image/jpeg'})
        if r.status_code != 200:
            raise DetectorUnavailable(f'the detector service said {r.status_code}: {r.json()["detail"]}')
        return list(DetectReply.from_dict(r.json()).detections)
