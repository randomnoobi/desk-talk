"""
Object detection with Ultralytics YOLO.

    det = Detector.from_settings(settings.detector)
    objects = det.detect(frame)
    # → [{'label': 'cup', 'conf': 0.83, 'bbox': [x1, y1, x2, y2], 'center': [cx, cy]}, ...]

The model file is set in config/settings.yaml (detector.model). If it isn't in
models/ yet, it is downloaded the first time (or run scripts/download_models.py).
"""

from __future__ import annotations

import threading
from pathlib import Path

import cv2

from .. import paths


def pick_device(wanted: str = 'auto') -> str:
    """'auto' → Apple GPU ('mps') or NVIDIA ('cuda:0') when available, else 'cpu'."""
    wanted = (wanted or 'auto').lower()
    if wanted != 'auto':
        return wanted
    try:
        import torch
        if torch.cuda.is_available():
            return 'cuda:0'
        if getattr(torch.backends, 'mps', None) and torch.backends.mps.is_available():
            return 'mps'
    except Exception:
        pass
    return 'cpu'


def resolve_model_path(model: str) -> Path:
    """Where the model file is (or should be downloaded to): always inside models/ unless a full path is given."""
    p = Path(model).expanduser()
    if p.is_absolute():
        return p
    candidate = paths.PROJECT_ROOT / p
    if candidate.is_file():
        return candidate
    return paths.MODELS_DIR / p.name


def ensure_model_file(model: str) -> Path:
    """Download the model into models/ if it isn't there yet. Returns its path."""
    path = resolve_model_path(model)
    if path.is_file():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    from ultralytics.utils.downloads import attempt_download_asset
    got = Path(attempt_download_asset(str(path)))
    if not got.is_file():
        raise FileNotFoundError(f'Could not download {path.name}. Run: python scripts/download_models.py')
    return got


class Detector:
    def __init__(self, model: str, confidence: float = 0.35, image_size: int = 640, device: str = 'auto'):
        from ultralytics import YOLO
        self.model_path = ensure_model_file(model)
        self.model = YOLO(str(self.model_path))
        self.names = dict(self.model.names)
        self.confidence = float(confidence)
        self.image_size = int(image_size)
        self.device = pick_device(device)
        # Ultralytics builds its predictor on the first call; two threads at once corrupt it.
        self._lock = threading.Lock()

    @classmethod
    def from_settings(cls, s) -> 'Detector':
        return cls(s.get('model'), s.get('confidence', 0.35), s.get('image_size', 640), s.get('device', 'auto'))

    @property
    def label(self) -> str:
        return f'{self.model_path.name} ({len(self.names)} kinds of objects, {self.device})'

    def _predict(self, frame):
        return self.model.predict(source=frame, conf=self.confidence, imgsz=self.image_size,
                                  device=self.device, verbose=False)

    def detect(self, frame) -> list[dict]:
        with self._lock:
            try:
                results = self._predict(frame)
            except Exception as exc:
                if self.device == 'cpu':
                    raise
                print(f'  [detector] {self.device} failed ({exc}); using the CPU from now on')
                self.device = 'cpu'
                results = self._predict(frame)
        out = []
        for r in results:
            for box in r.boxes:
                x1, y1, x2, y2 = (float(v) for v in box.xyxy[0].tolist())
                out.append({
                    'label': self.names[int(box.cls[0])],
                    'conf': round(float(box.conf[0]), 3),
                    'bbox': [round(x1, 1), round(y1, 1), round(x2, 1), round(y2, 1)],
                    'center': [round((x1 + x2) / 2, 1), round((y1 + y2) / 2, 1)],
                })
        return out

    def warmup(self) -> None:
        import numpy as np
        try:
            self.detect(np.zeros((self.image_size, self.image_size, 3), dtype=np.uint8))
        except Exception as exc:
            print(f'  [detector] warm-up: {exc}')


def draw_boxes(frame, objects, highlight: set | None = None):
    """Boxes + labels for the live view ("Boxes" in the app)."""
    for o in objects:
        x1, y1, x2, y2 = (int(v) for v in o['bbox'])
        on = highlight is None or o['label'] in highlight
        color = (60, 200, 90) if on else (150, 150, 150)
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        text = f"{o['label']} {o['conf']:.0%}"
        (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
        ty = max(th + 6, y1)
        cv2.rectangle(frame, (x1, ty - th - 6), (x1 + tw + 6, ty), color, -1)
        cv2.putText(frame, text, (x1 + 3, ty - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
    return frame
