"""
The detector service's HTTP API. The service runs YOLO in Docker on the Pi, on 127.0.0.1 only;
the camera program on the Pi is its only client.

    POST /detect   body: one JPEG (Content-Type: image/jpeg)      → DetectReply
    GET  /health                                                   → DetectorHealth
    PUT  /model    body: ModelSettings as JSON (load, converting   → DetectorHealth
                   to ncnn first if needed; the last good model
                   keeps working until the new one is ready)

Replies are written with to_dict() and read with from_dict(), on both sides, so the JSON
looks the same everywhere. Standard library only (the camera program imports this).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from desktalk_shared import _read
from desktalk_shared._read import WireError
from desktalk_shared.detection import Detection

API_VERSION = 1
FORMATS = ('ncnn', 'pt')     # ncnn: fastest on the Pi's CPU; pt: plain PyTorch, for comparison
STRIDE = 32                  # YOLO picture sizes must be a multiple of this


class ServiceState(StrEnum):
    STARTING = 'starting'    # just started, no model yet
    LOADING = 'loading'      # loading or converting a model (a previous one may still be answering)
    READY = 'ready'
    ERROR = 'error'          # no working model; message says why


@dataclass(frozen=True, kw_only=True)
class ModelSettings:
    """Which model the service runs. Comes from config/settings.yaml → pi.detector on this computer."""

    model: str               # e.g. yolo11s.pt (inside the service's models folder)
    format: str              # ncnn or pt
    image_size: int          # YOLO picture size, e.g. 320, 480, 640
    confidence: float        # lowest confidence a box needs to be kept
    half: bool               # fp16 (ncnn only): smaller and often faster

    def __post_init__(self):
        if not self.model:
            raise WireError('model must name a model file')
        if self.format not in FORMATS:
            raise WireError(f'format must be one of {", ".join(FORMATS)} (got {self.format!r})')
        if self.image_size <= 0 or self.image_size % STRIDE:
            raise WireError(f'image_size must be a positive multiple of {STRIDE} (got {self.image_size})')
        if not 0 <= self.confidence <= 1:
            raise WireError(f'confidence must be from 0 to 1 (got {self.confidence})')
        if self.half and self.format != 'ncnn':
            raise WireError('half (fp16) only works with the ncnn format')

    def to_dict(self) -> dict[str, Any]:
        return {'model': self.model, 'format': self.format, 'image_size': self.image_size,
                'confidence': self.confidence, 'half': self.half}

    @classmethod
    def from_dict(cls, data: Any) -> ModelSettings:
        d = _read.mapping(data, 'model settings')
        return cls(model=_read.text(d, 'model', '', required=True), format=_read.text(d, 'format', '', required=True),
                   image_size=_read.whole(d, 'image_size', '', required=True),
                   confidence=_read.number(d, 'confidence', '', required=True), half=_read.flag(d, 'half', ''))


@dataclass(frozen=True, kw_only=True)
class DetectorHealth:
    state: ServiceState
    model: ModelSettings | None          # the model answering /detect right now
    message: str = ''                    # what it's doing or what's wrong, in plain words
    detections_done: int = 0
    last_detect_ms: float | None = None
    api_version: int = API_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {'api_version': self.api_version, 'state': self.state.value,
                'model': self.model.to_dict() if self.model else None, 'message': self.message,
                'detections_done': self.detections_done, 'last_detect_ms': self.last_detect_ms}

    @classmethod
    def from_dict(cls, data: Any) -> DetectorHealth:
        d = _read.mapping(data, 'health')
        try:
            state = ServiceState(_read.text(d, 'state', '', required=True))
        except ValueError:
            raise WireError(f'state must be one of {", ".join(s.value for s in ServiceState)} '
                            f'(got {d.get("state")!r})') from None
        model = d.get('model')
        return cls(api_version=_read.whole(d, 'api_version', '') or API_VERSION, state=state,
                   model=ModelSettings.from_dict(model) if model is not None else None,
                   message=_read.text(d, 'message', ''), detections_done=_read.whole(d, 'detections_done', '') or 0,
                   last_detect_ms=_read.number(d, 'last_detect_ms', ''))


@dataclass(frozen=True, kw_only=True)
class DetectReply:
    detections: tuple[Detection, ...]    # boxes in the picture's own pixels
    detect_ms: float
    width: int                           # size of the picture that was sent
    height: int
    model: str                           # e.g. "yolo11s.pt (ncnn, 480)"

    def __post_init__(self):
        object.__setattr__(self, 'detections', tuple(self.detections))

    def to_dict(self) -> dict[str, Any]:
        return {'detections': [d.to_dict() for d in self.detections], 'detect_ms': self.detect_ms,
                'width': self.width, 'height': self.height, 'model': self.model}

    @classmethod
    def from_dict(cls, data: Any) -> DetectReply:
        d = _read.mapping(data, 'detect reply')
        raw = d.get('detections')
        if not isinstance(raw, list):
            raise WireError('detections must be a list')
        detections = []
        for i, item in enumerate(raw):
            try:
                detections.append(Detection.from_dict(item))
            except ValueError as exc:
                raise WireError(f'detections[{i}]: {exc}') from None
        return cls(detections=tuple(detections), detect_ms=_read.number(d, 'detect_ms', '', required=True),
                   width=_read.whole(d, 'width', '', required=True), height=_read.whole(d, 'height', '', required=True),
                   model=_read.text(d, 'model', ''))
