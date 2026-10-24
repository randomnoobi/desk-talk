"""
Proof photos: the live view picture kept with an alert (shown in the chat and on the Captures page),
cropped around the objects it's about. They go into the PhotoAlbum, tagged with what was on the desk.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from desktalk.config import SnapshotsSettings
from desktalk.core.vision.geometry import Box
from desktalk.ports import Clock, DeskScene, LivePictures, PhotoAlbum, PictureTools

NO_LABEL = 'Capture'        # the label of a photo saved without one


class PhotoDesk(LivePictures, DeskScene, PhotoAlbum, Protocol):
    """The parts of the desk proof photos use: the live view picture, what's on the desk, and the album."""


class ProofPhotos:
    """Implements PhotoTaker. `pictures` crops the photos."""

    def __init__(self, desk: PhotoDesk, clock: Clock, snapshots: SnapshotsSettings, *, pictures: PictureTools):
        self._desk = desk
        self._clock = clock
        self._settings = snapshots
        self._pictures = pictures

    def save(self, label: str, crop_boxes: Sequence[Box] = ()) -> str | None:
        """Keep the newest live view picture, cropped around `crop_boxes` (if any). Returns its id, or None
        when there is no picture yet."""
        jpeg = self._desk.boxed()
        if not jpeg:
            return None
        if crop_boxes:
            jpeg = self._cropped(jpeg, crop_boxes)
        desk = self._desk
        return desk.add(jpeg, label or NO_LABEL, self._clock.now(), char_ids=list(desk.members()),
                        labels=sorted(str(k).lower() for k in desk.seen()),
                        vlm_labels=sorted(str(x).lower() for x in desk.scan_labels()))

    def _cropped(self, jpeg: bytes, boxes: Sequence[Box]) -> bytes:
        """The part of the picture around all the boxes, with snapshots.crop_padding more on each side.
        A crop no bigger than snapshots.crop_min_px keeps the whole picture."""
        tools = self._pictures
        picture = tools.decode_jpeg(jpeg)
        if picture is None:
            return jpeg
        size = tools.size(picture)
        x1, y1 = min(b[0] for b in boxes), min(b[1] for b in boxes)
        x2, y2 = max(b[2] for b in boxes), max(b[3] for b in boxes)
        pad_w = (x2 - x1) * self._settings.crop_padding
        pad_h = (y2 - y1) * self._settings.crop_padding
        cx1, cy1 = max(0, int(x1 - pad_w)), max(0, int(y1 - pad_h))
        cx2, cy2 = min(size.width, int(x2 + pad_w)), min(size.height, int(y2 + pad_h))
        smallest = self._settings.crop_min_px
        if cx2 - cx1 <= smallest or cy2 - cy1 <= smallest:
            return jpeg
        crop = tools.crop(picture, (cx1, cy1, cx2, cy2))
        return tools.encode_jpeg(crop, self._settings.jpeg_quality) or jpeg
