"""The desk right now, as plain data: what the camera sees, the dangers, and the proof photos."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from desktalk.domain.pictures import Box, Point
from desktalk_shared.detection import Detection


@dataclass(frozen=True, kw_only=True)
class SeenObject:
    """One thing in the newest picture: an agent on the desk, or the best box for one detector word."""

    label: str                  # the detector's word, e.g. cup
    box: Box
    center: Point
    confidence: float
    cam: int = 0                # which camera saw it

    @classmethod
    def from_detection(cls, detection: Detection, cam: int = 0) -> SeenObject:
        """Rounded the way detections have always been shown (box and center to 0.1 px, confidence to 0.001)."""
        d = detection.to_dict()
        x1, y1, x2, y2 = d['bbox']
        cx, cy = d['center']
        return cls(label=d['label'], box=(x1, y1, x2, y2), center=(cx, cy), confidence=d['conf'], cam=cam)


@dataclass(frozen=True, kw_only=True)
class Danger:
    """A danger the desk monitor found: something in another thing's danger zone, or near the desk edge."""

    pair: tuple[str, str]       # the two detector words, e.g. ('cup', 'laptop'), or ('cup', 'desk_edge')
    label: str                  # e.g. 'Spill / pressure zone' or 'cup near left edge'
    kind: str                   # zone_intrusion or edge_danger
    depth_px: float | None = None   # how far into the zone (zone_intrusion only)

    def to_dict(self) -> dict[str, Any]:
        """The shape the app and the AI have always read."""
        out: dict[str, Any] = {'pair': list(self.pair), 'label': self.label}
        if self.depth_px is not None:
            out['depth_px'] = self.depth_px
        out['type'] = self.kind
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Danger:
        a, b = data['pair']
        depth = data.get('depth_px')
        return cls(pair=(str(a), str(b)), label=str(data.get('label', '')), kind=str(data.get('type', '')),
                   depth_px=float(depth) if depth is not None else None)


@dataclass(frozen=True, kw_only=True)
class SafetyNow:
    """The desk monitor's latest report."""

    state: str = 'SAFE'                         # SAFE or DANGEROUS
    dangers: tuple[Danger, ...] = ()
    result: Mapping[str, Any] | None = None     # the desk analyzer's full result (risk_level, relationships ...)


@dataclass(frozen=True, kw_only=True)
class Photo:
    """A proof photo: the live view picture kept with an alert or a lost-item sighting."""

    id: str
    ts: float
    label: str
    jpeg: bytes = field(repr=False)
    char_ids: tuple[str, ...] = ()      # the agents on the desk when it was taken
    labels: tuple[str, ...] = ()        # every detector word in the picture
    vlm_labels: tuple[str, ...] = ()    # what the last vision scan saw

    def to_dict(self) -> dict[str, Any]:
        """The shape /api/snapshots has always sent (without the picture)."""
        return {'id': self.id, 'ts': self.ts, 'label': self.label, 'char_ids': list(self.char_ids),
                'labels': list(self.labels), 'vlm_labels': list(self.vlm_labels)}
