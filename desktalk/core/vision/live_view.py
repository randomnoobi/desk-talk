"""
The live view with boxes ("Boxes" in the app): every detection box, green for the agents in the chat
and grey for the rest, plus the desk overlays from the desk analyzer's latest result.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from desktalk.core.vision.analyzer import DeskAnalyzer
from desktalk.core.vision.drawing import draw_boxes, draw_overlays
from desktalk.core.vision.findings import Thing
from desktalk.desk_state import DeskState
from desktalk.domain import Detection
from desktalk.ports import Profiles

log = logging.getLogger(__name__)


class DeskPainter:
    """Implements LiveViewPainter."""

    def __init__(self, desk: DeskState, profiles: Profiles, analyzer: DeskAnalyzer):
        self._desk = desk
        self._profiles = profiles
        self._analyzer = analyzer

    def paint(self, picture: Any, detections: Sequence[Detection]) -> Any:
        out = picture.copy()
        with self._desk.lock:
            objects = dict(self._desk.objects)
        agent_labels = {o['class_name'] for o in objects.values()}
        draw_boxes(out, detections, highlight=agent_labels or None)
        if not objects:
            return out
        names = {agent: p.name for agent in objects if (p := self._profiles.get(agent))}
        things = {agent: Thing.from_desk(agent, o) for agent, o in objects.items()}
        try:
            draw_overlays(out, things, names, self._analyzer.latest, self._analyzer.rules, self._analyzer.history())
        except Exception:
            log.exception('Could not draw the desk overlays on the live view (the boxes are still shown)')
        return out
