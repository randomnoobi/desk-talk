"""
Ultralytics predict() results → Detections. Used by the detector service on the Pi and by the
detector on this computer, so both give the same labels and boxes.

Doesn't import Ultralytics (or anything else outside the standard library): it only reads
result.boxes.xyxy / .conf / .cls (tensors or arrays with .tolist()), result.names and
result.orig_shape.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from desktalk_shared.detection import BBox, Detection, keep_inside


def detections_from_results(results: Iterable[Any], names: Mapping[int, str] | None = None) -> list[Detection]:
    """Every box in every result, cut to its picture. `names` (class number → word) defaults to the result's own."""
    out: list[Detection] = []
    for r in results:
        boxes = getattr(r, 'boxes', None)
        if boxes is None:
            continue
        words = names or r.names
        found = [Detection(words[int(k)], float(c), BBox(*(float(v) for v in xyxy)))
                 for xyxy, c, k in zip(boxes.xyxy.tolist(), boxes.conf.tolist(), boxes.cls.tolist(), strict=True)]
        height, width = r.orig_shape[:2]
        out.extend(keep_inside(found, width, height))
    return out
