"""
The upload format: how the Pi sends a picture (and what its detector found) to this computer.

One HTTP POST to /api/pi/frame per picture, with the pair key in the X-Desk-Key header, as
multipart/form-data with two parts:

    info     JSON, an UploadInfo: format version, picture id, when it was taken, light,
             the Pi's health, the detector state and the detections
    picture  the JPEG

Detector states (what this computer should do with the picture):
    ok    detections are attached: use them
    busy  the Pi's detector is still working on an earlier picture: this one is only for the
          live view, don't detect it
    off   the Pi's detector is down (detector_reason says why): detect it on this computer

Version 1 (the old raspberry_pi/camera_node.py) sent the JPEG as the whole body and a few
X-... headers; info_from_v1_headers() reads those, so old Pis keep working.

Standard library only: the camera program on the Pi imports this with the Pi's own python3.
"""

from __future__ import annotations

import json
import math
import secrets
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

from desktalk_shared import _read
from desktalk_shared._read import WireError
from desktalk_shared.detection import Detection

VERSION = 2

__all__ = ['VERSION', 'DetectorState', 'PiHealth', 'UploadInfo', 'WireError', 'decode_upload', 'encode_upload',
           'info_from_v1_headers']


class DetectorState(StrEnum):
    OK = 'ok'
    BUSY = 'busy'
    OFF = 'off'


@dataclass(frozen=True, kw_only=True)
class PiHealth:
    """What the Pi reports about itself with each picture (None = it didn't say)."""

    camera: str = ''                      # which camera: picamera2, usb, folder, video
    photos_stored: int | None = None      # pictures kept on the Pi's disk
    disk_free_mb: float | None = None
    last_cleanup_ts: float | None = None  # when the Pi last deleted old pictures
    cpu_temp_c: float | None = None
    throttled: str = ''                   # the Pi's slow-down flags, e.g. 0x0 = fine, 0x50005 = too hot

    @classmethod
    def from_dict(cls, data: Any) -> PiHealth:
        d = _read.mapping(data, 'health')
        w = 'health.'
        return cls(camera=_read.text(d, 'camera', w), photos_stored=_read.whole(d, 'photos_stored', w),
                   disk_free_mb=_read.number(d, 'disk_free_mb', w),
                   last_cleanup_ts=_read.number(d, 'last_cleanup_ts', w),
                   cpu_temp_c=_read.number(d, 'cpu_temp_c', w), throttled=_read.text(d, 'throttled', w))


@dataclass(frozen=True, kw_only=True)
class UploadInfo:
    frame_id: str                          # e.g. pi-000123
    taken_at: float | None                 # the Pi's clock, seconds since 1970
    lux: float | None = None               # light level (Pi camera module only)
    detector: DetectorState = DetectorState.OFF
    detector_reason: str = ''              # why the detector is off, in plain words
    detections: tuple[Detection, ...] = ()
    detect_ms: float | None = None         # how long the Pi's detector took
    model: str = ''                        # which model made the detections
    health: PiHealth = field(default_factory=PiHealth)
    version: int = VERSION

    def __post_init__(self):
        object.__setattr__(self, 'detections', tuple(self.detections))
        if self.detections and self.detector is not DetectorState.OK:
            raise WireError(f'detections are only sent when the detector state is ok (it is {self.detector.value})')

    def to_dict(self) -> dict[str, Any]:
        return {'version': self.version, 'frame_id': self.frame_id, 'taken_at': self.taken_at, 'lux': self.lux,
                'detector': self.detector.value, 'detector_reason': self.detector_reason,
                'detections': [d.to_dict() for d in self.detections], 'detect_ms': self.detect_ms,
                'model': self.model, 'health': asdict(self.health)}

    def to_json(self) -> bytes:
        return json.dumps(self.to_dict()).encode('utf-8')

    @classmethod
    def from_dict(cls, data: Any) -> UploadInfo:
        d = _read.mapping(data, 'info')
        version = _read.whole(d, 'version', 'info.', required=True)
        if version > VERSION:
            raise WireError(f'The Pi sends upload format {version}, but this computer only understands up to '
                            f'{VERSION}. Update Desk Talk on this computer (git pull) and restart it.')
        if version < VERSION:
            raise WireError(f'The Pi sends upload format {version}; this computer needs {VERSION}. Update the Pi: '
                            f'run git pull in desk-talk on the Pi and restart the camera program.')
        try:
            state = DetectorState(_read.text(d, 'detector', 'info.', required=True))
        except ValueError:
            raise WireError(f'info.detector must be ok, busy or off (got {d.get("detector")!r})') from None
        raw = d.get('detections') or []
        if not isinstance(raw, list):
            raise WireError('info.detections must be a list')
        detections = []
        for i, item in enumerate(raw):
            try:
                detections.append(Detection.from_dict(item))
            except ValueError as exc:
                raise WireError(f'info.detections[{i}]: {exc}') from None
        return cls(version=version, frame_id=_read.text(d, 'frame_id', 'info.', required=True),
                   taken_at=_read.number(d, 'taken_at', 'info.'), lux=_read.number(d, 'lux', 'info.'),
                   detector=state, detector_reason=_read.text(d, 'detector_reason', 'info.'),
                   detections=tuple(detections), detect_ms=_read.number(d, 'detect_ms', 'info.'),
                   model=_read.text(d, 'model', 'info.'), health=PiHealth.from_dict(d.get('health') or {}))

    @classmethod
    def from_json(cls, data: bytes | str) -> UploadInfo:
        try:
            parsed = json.loads(data)
        except (ValueError, UnicodeDecodeError) as exc:
            raise WireError(f'info is not valid JSON ({exc})') from None
        return cls.from_dict(parsed)


# ── multipart/form-data, by hand (the Pi has only the standard library) ──
def _part(boundary: str, name: str, content_type: str, content: bytes, filename: str = '') -> bytes:
    disposition = f'form-data; name="{name}"' + (f'; filename="{filename}"' if filename else '')
    head = f'--{boundary}\r\nContent-Disposition: {disposition}\r\nContent-Type: {content_type}\r\n\r\n'
    return head.encode('ascii') + content + b'\r\n'


def encode_upload(jpeg: bytes, info: UploadInfo) -> tuple[bytes, str]:
    """The request body and its Content-Type header for one upload."""
    boundary = 'desktalk-' + secrets.token_hex(16)
    body = (_part(boundary, 'info', 'application/json', info.to_json())
            + _part(boundary, 'picture', 'image/jpeg', jpeg, filename='picture.jpg')
            + f'--{boundary}--\r\n'.encode('ascii'))
    return body, f'multipart/form-data; boundary={boundary}'


def _boundary(content_type: str) -> str:
    kind, _, params = (content_type or '').partition(';')
    if kind.strip().lower() != 'multipart/form-data':
        raise WireError(f'upload must be multipart/form-data (got {content_type!r})')
    for param in params.split(';'):
        key, _, value = param.strip().partition('=')
        if key.lower() == 'boundary' and value.strip('"'):
            return value.strip('"')
    raise WireError('upload Content-Type has no boundary')


def _parts(body: bytes, boundary: str) -> dict[str, bytes]:
    delimiter = b'\r\n--' + boundary.encode('ascii')
    if not body.startswith(delimiter[2:]):
        raise WireError('upload body is not in the multipart format it says it is')
    parts: dict[str, bytes] = {}
    for chunk in (b'\r\n' + body).split(delimiter)[1:]:
        if chunk.startswith(b'--'):
            break                                      # the closing delimiter
        head, sep, content = chunk.removeprefix(b'\r\n').partition(b'\r\n\r\n')
        if not sep:
            raise WireError('upload has a part without headers')
        for line in head.decode('latin-1').split('\r\n'):
            name, _, value = line.partition(':')
            if name.strip().lower() == 'content-disposition':
                for param in value.split(';'):
                    key, _, v = param.strip().partition('=')
                    if key == 'name':
                        parts[v.strip('"')] = content
    return parts


def decode_upload(body: bytes, content_type: str) -> tuple[bytes, UploadInfo]:
    """The JPEG and the info from one upload. Raises WireError saying what's wrong."""
    parts = _parts(body, _boundary(content_type))
    if 'info' not in parts:
        raise WireError('upload has no info part')
    if 'picture' not in parts:
        raise WireError('upload has no picture part')
    return parts['picture'], UploadInfo.from_json(parts['info'])


def info_from_v1_headers(headers: Mapping[str, str]) -> UploadInfo:
    """The info an old Pi (upload format 1) sends in X-... headers. Its detector is always off."""
    h = {str(k).lower(): v for k, v in headers.items()}

    def value(header: str, cast):
        """The header as a number, or None when it's missing or not a plain number (nan and inf can't go
        into a JSON reply)."""
        raw = h.get(header.lower())
        if raw in (None, ''):
            return None
        try:
            number = cast(raw)
        except ValueError:
            return None
        return number if math.isfinite(number) else None

    return UploadInfo(version=1, frame_id=h.get('x-frame-id', ''), taken_at=value('X-Taken-At', float),
                      lux=value('X-Lux', float), detector=DetectorState.OFF,
                      detector_reason='this Pi runs the old camera program (no detector)',
                      health=PiHealth(camera=h.get('x-pi-camera', ''), photos_stored=value('X-Pi-Photos', int),
                                      disk_free_mb=value('X-Pi-Free-MB', int),
                                      last_cleanup_ts=value('X-Pi-Last-Cleanup', float)))
