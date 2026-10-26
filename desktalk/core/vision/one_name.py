"""
One thing, one name (settings → agents.same_thing_overlap).

The detector sometimes names one thing twice, on nearly the same box: a laptop that is also "book". The producer
runs every picture's detections through OneNameEach before it builds the DetectionEvent, so only the surer name
goes out and everyone who listens (the chat, the warnings, the habits, lost items, the object states) sees the
same list.

Words on the ignore list (agents.yaml) take no part. They never become anything, so they can't knock out the real
name (a monitor seen as "tv" and, surer, as "picture/frame"), and they are kept as they are (the producer still
needs "person" for someone at the desk).

Each drop is logged once, at debug level, with both names, when it starts; again only after a picture without it.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence

from desktalk.core.vision.geometry import Box, same_thing_drops
from desktalk.domain import Detection

log = logging.getLogger(__name__)


def box_of(det: Detection) -> Box:
    """A detection's box as (x1, y1, x2, y2)."""
    return det.bbox.x1, det.bbox.y1, det.bbox.x2, det.bbox.y2


class OneNameEach:
    """detections → the same detections without a second name for a box a surer name already has."""

    def __init__(self, overlap: float, ignore_labels: Iterable[str] = ()):
        self._overlap = overlap
        self._ignore = {str(x).lower() for x in ignore_labels}
        self._dropping: set[tuple[str, str]] = set()     # (left out, kept) in the last picture, logged already

    def __call__(self, detections: Sequence[Detection]) -> list[Detection]:
        named = [i for i, d in enumerate(detections) if d.label.lower() not in self._ignore]
        drops = same_thing_drops([(detections[i].label, detections[i].confidence, box_of(detections[i]))
                                  for i in named], self._overlap)
        pairs = {(detections[named[lost]], detections[named[won]]) for lost, won in drops.items()}
        dropping = {(lost.label, won.label) for lost, won in pairs}
        for lost, won in pairs:
            if (lost.label, won.label) not in self._dropping:
                log.debug('Left out "%s" (%.2f): the same box is "%s" (%.2f), which is surer', lost.label,
                          lost.confidence, won.label, won.confidence)
        self._dropping = dropping
        gone = {named[i] for i in drops}
        return [d for i, d in enumerate(detections) if i not in gone]
