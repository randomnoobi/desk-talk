"""
Proof photos: the live view picture kept with an alert (shown in the chat and on the Captures page),
cropped around the objects it's about. Kept in DeskState.snapshots, newest first in snapshot_order.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import cv2
import numpy as np

from desktalk.config import SnapshotsSettings
from desktalk.core.vision.geometry import Box
from desktalk.desk_state import DeskState
from desktalk.ports import Clock


class ProofPhotos:
    def __init__(self, desk: DeskState, clock: Clock, snapshots: SnapshotsSettings):
        self._desk = desk
        self._clock = clock
        self._settings = snapshots

    def save(self, label: str, crop_boxes: Sequence[Box] = ()) -> str | None:
        """Keep the newest live view picture, cropped around `crop_boxes` (if any). Returns its id, or None
        when there is no picture yet. The cropping happens outside the desk lock, so the camera never waits."""
        with self._desk.lock:
            jpeg = next((bytes(self._desk.frames[cam]) for cam in sorted(self._desk.frames)
                         if self._desk.frames[cam]), None)
        if not jpeg:
            return None
        if crop_boxes:
            jpeg = self._cropped(jpeg, crop_boxes)
        snap_id = uuid.uuid4().hex[:12]
        desk = self._desk
        with desk.lock:
            tags = {'char_ids': list(desk.objects.keys()),
                    'labels': sorted(str(k).lower() for k in desk.seen_labels),
                    'vlm_labels': sorted(str(x).lower() for x in desk.lost_last_vlm_visible)}
            desk.snapshots[snap_id] = jpeg
            desk.snapshot_order.insert(0, {'id': snap_id, 'ts': self._clock.now(), 'label': label or 'Capture',
                                           **tags})
            desk.snapshot_order = desk.snapshot_order[:desk.max_snapshots]
            while len(desk.snapshots) > desk.max_snapshots:
                old_id, _ = desk.snapshots.popitem(last=False)
                desk.snapshot_order = [x for x in desk.snapshot_order if x.get('id') != old_id]
        return snap_id

    def _cropped(self, jpeg: bytes, boxes: Sequence[Box]) -> bytes:
        """The part of the picture around all the boxes, with snapshots.crop_padding more on each side.
        A crop no bigger than snapshots.crop_min_px keeps the whole picture."""
        picture = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
        if picture is None:
            return jpeg
        ih, iw = picture.shape[:2]
        x1, y1 = min(b[0] for b in boxes), min(b[1] for b in boxes)
        x2, y2 = max(b[2] for b in boxes), max(b[3] for b in boxes)
        pad_w = (x2 - x1) * self._settings.crop_padding
        pad_h = (y2 - y1) * self._settings.crop_padding
        cx1, cy1 = max(0, int(x1 - pad_w)), max(0, int(y1 - pad_h))
        cx2, cy2 = min(iw, int(x2 + pad_w)), min(ih, int(y2 + pad_h))
        smallest = self._settings.crop_min_px
        if cx2 - cx1 <= smallest or cy2 - cy1 <= smallest:
            return jpeg
        crop = picture[cy1:cy2, cx1:cx2]
        return cv2.imencode('.jpg', crop, [cv2.IMWRITE_JPEG_QUALITY, self._settings.jpeg_quality])[1].tobytes()
