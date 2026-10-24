"""
The live view with boxes ("Boxes" in the app), drawn with OpenCV: every detection box, green for the agents in
the chat and grey for the rest, plus the desk overlays from the desk analyzer's latest result (desk edges,
danger zones, chains, crowds, things moving, the risk badge).

Every colour, line width, text size, see-through amount, trail, dash, arrow tip and circle size comes from
settings → drawing. Colours are [blue, green, red] (OpenCV's order). Only where each piece of writing sits next
to the thing it labels (a few pixels left, right, up or down) is written here.

If the overlays can't be drawn, the boxes are still shown. The problem is logged once (with its traceback),
not with every picture, until a different one comes up or the overlays work again.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol

import cv2

from desktalk.config import DrawingSettings
from desktalk.core.vision.desk_edges import desk_edges
from desktalk.core.vision.findings import Thing
from desktalk.core.vision.geometry import footprint, grow, overlap
from desktalk.domain import DeskRules, Detection
from desktalk.ports import DeskScene, Profiles

log = logging.getLogger(__name__)

FONT = cv2.FONT_HERSHEY_SIMPLEX
AA = cv2.LINE_AA
Color = tuple[int, int, int]


class DeskChecksView(Protocol):
    """What the painter reads from the desk analyzer (DeskAnalyzer)."""

    @property
    def latest(self) -> Mapping[str, Any] | None:
        ...

    @property
    def rules(self) -> DeskRules:
        ...

    def history(self) -> Sequence[Mapping[str, Thing]]:
        ...


class DeskPainter:
    """Implements LiveViewPainter."""

    def __init__(self, desk: DeskScene, profiles: Profiles, analyzer: DeskChecksView, drawing: DrawingSettings):
        self._desk = desk
        self._profiles = profiles
        self._analyzer = analyzer
        self._pen = Pen(drawing)
        self._last_problem = ''          # the overlay problem already logged

    def paint(self, picture: Any, detections: Sequence[Detection]) -> Any:
        out = picture.copy()
        members = self._desk.members()
        agent_labels = {o.label for o in members.values()}
        self._pen.boxes(out, detections, highlight=agent_labels or None)
        if not members:
            return out
        names = {agent: p.name for agent in members if (p := self._profiles.get(agent))}
        things = {agent: Thing.from_seen(agent, o) for agent, o in members.items()}
        try:
            self._pen.overlays(out, things, names, self._analyzer.latest, self._analyzer.rules,
                               self._analyzer.history())
        except Exception as exc:
            problem = f'{type(exc).__name__}: {exc}'
            if problem != self._last_problem:
                self._last_problem = problem
                log.exception('Could not draw the desk overlays on the live view (the boxes are still shown)')
        else:
            self._last_problem = ''
        return out


def _point(p) -> tuple[int, int]:
    return int(p[0]), int(p[1])


class Pen:
    """Draws onto a picture (changing it), the way settings → drawing says."""

    def __init__(self, drawing: DrawingSettings):
        self._s = drawing
        self._c = drawing.colors

    # ── the boxes ──
    def boxes(self, picture: Any, detections: Sequence[Detection], highlight: set[str] | None = None) -> Any:
        """A box and "label 83%" for each detection: box_on if its label is in `highlight` (or there's no
        highlight), box_off otherwise."""
        s, c = self._s, self._c
        for d in detections:
            x1, y1, x2, y2 = (int(v) for v in (d.bbox.x1, d.bbox.y1, d.bbox.x2, d.bbox.y2))
            color = c.box_on if highlight is None or d.label in highlight else c.box_off
            cv2.rectangle(picture, (x1, y1), (x2, y2), color, s.line_px)
            text = f'{d.label} {d.confidence:.0%}'
            (tw, th), _ = cv2.getTextSize(text, FONT, s.font_scale.box, s.thin_line_px)
            ty = max(th + 6, y1)
            cv2.rectangle(picture, (x1, ty - th - 6), (x1 + tw + 6, ty), color, -1)
            cv2.putText(picture, text, (x1 + 3, ty - 4), FONT, s.font_scale.box, c.text, s.thin_line_px, AA)
        return picture

    # ── the overlays ──
    def overlays(self, picture: Any, things: Mapping[str, Thing], names: Mapping[str, str],
                 result: Mapping[str, Any] | None, rules: DeskRules,
                 history: Sequence[Mapping[str, Thing]]) -> None:
        """Draw what the desk analyzer found (`result`) for the agents' objects (`things`) onto `picture`.
        `names` are the agents' names, for the labels; `history` is the analyzer's recent pictures."""
        if not result:
            return

        def name(agent: str) -> str:
            if agent in names:
                return names[agent]
            return things[agent].label if agent in things else agent

        self._edges(picture, things, result, rules)
        self._zones(picture, things, result, rules)
        self._chains(picture, things, result, rules, name)
        self._moving(picture, things, result, history)
        self._risk_badge(picture, result)

    # ── small helpers ──
    def _dashed_line(self, img, x1, y1, x2, y2, color: Color, thickness: int, dash: tuple[int, int]) -> None:
        length = math.hypot(x2 - x1, y2 - y1)
        if length < 1:
            return
        on, off = dash
        ux, uy = (x2 - x1) / length, (y2 - y1) / length
        pos, draw = 0.0, True
        while pos < length:
            end = min(pos + (on if draw else off), length)
            if draw:
                cv2.line(img, (int(x1 + ux * pos), int(y1 + uy * pos)), (int(x1 + ux * end), int(y1 + uy * end)),
                         color, thickness, AA)
            pos, draw = end, not draw

    def _dashed_rect(self, img, x1, y1, x2, y2, color: Color, thickness: int, dash: tuple[int, int]) -> None:
        for ax, ay, bx, by in ((x1, y1, x2, y1), (x2, y1, x2, y2), (x2, y2, x1, y2), (x1, y2, x1, y1)):
            self._dashed_line(img, ax, ay, bx, by, color, thickness, dash)

    def _label(self, img, text: str, x: int, y: int, bg: Color, fg: Color | None = None,
               scale: float | None = None) -> None:
        """Text with a filled background."""
        fg = fg if fg is not None else self._c.text
        scale = scale if scale is not None else self._s.font_scale.label
        thick = self._s.thin_line_px
        (tw, th), _ = cv2.getTextSize(text, FONT, scale, thick)
        cv2.rectangle(img, (x - 3, y - th - 5), (x + tw + 3, y + 4), bg, -1)
        cv2.putText(img, text, (x, y), FONT, scale, fg, thick, AA)

    def _arrow(self, img, pt1, pt2, color: Color) -> None:
        cv2.arrowedLine(img, pt1, pt2, color, self._s.line_px, AA, tipLength=self._s.arrow_tip)

    @staticmethod
    def _see_through_rect(img, x1, y1, x2, y2, color: Color, alpha: float) -> None:
        layer = img.copy()
        cv2.rectangle(layer, (int(x1), int(y1)), (int(x2), int(y2)), color, -1)
        cv2.addWeighted(layer, alpha, img, 1 - alpha, 0, img)

    # ── each overlay ──
    def _edges(self, img, things, result, rules: DeskRules) -> None:
        s, c = self._s, self._c
        h, w = img.shape[:2]
        for a, b in desk_edges(rules.outline, (w, h), rules.desk_border_share):    # the edges you marked
            cv2.line(img, _point(a), _point(b), c.warn, s.line_px, AA)
        for rel in result['relationships']:
            if rel['type'] != 'edge_proximity' or rel['object'] not in things:
                continue
            box = things[rel['object']].box
            color = c.danger if rel['severity'] == 'danger' else c.warn
            cv2.rectangle(img, _point(box[:2]), _point(box[2:]), color, s.line_px, AA)
            seg = rel.get('segment')
            if seg:
                f = footprint(box, rules.footprint_share, rules.footprint_min_px)
                mid = ((f[0] + f[2]) / 2, (f[1] + f[3]) / 2)
                (ax, ay), (bx, by) = seg
                dx, dy = bx - ax, by - ay       # the closest point on the edge, for the arrow
                along = 0.0 if dx == dy == 0 else ((mid[0] - ax) * dx + (mid[1] - ay) * dy) / (dx * dx + dy * dy)
                t = max(0.0, min(1.0, along))
                q = (int(ax + t * dx), int(ay + t * dy))
                self._arrow(img, _point(mid), q, color)
                self._label(img, f"{int(rel['distance_px'])}px to {rel['edge']} edge",
                            (int(mid[0]) + q[0]) // 2, (int(mid[1]) + q[1]) // 2, color)

    def _zones(self, img, things, result, rules: DeskRules) -> None:
        s, c = self._s, self._c
        for thing in things.values():               # every zone, even with nothing in it
            zone = rules.zones.get(thing.label)
            if zone is None:
                continue
            area = grow(thing.box, zone.expand)
            self._dashed_rect(img, int(area[0]), int(area[1]), int(area[2]), int(area[3]), c.zone, s.thin_line_px,
                              s.dash.zone)
            self._label(img, zone.label, int(area[0]) + 4, int(area[1]) + 14, c.soft_back, c.soft_text,
                        s.font_scale.zone)
        for rel in result['relationships']:          # zones with something risky in them
            if rel['type'] != 'danger_zone' or rel['intruder'] not in things or rel['target'] not in things:
                continue
            intruder, target = things[rel['intruder']], things[rel['target']]
            zone = rules.zones.get(target.label)
            if zone is None:
                continue
            area = grow(target.box, zone.expand)
            shared = overlap(intruder.box, area)
            if shared:
                self._see_through_rect(img, *shared, c.zone_hit, s.fill_alpha.zone)
            cv2.rectangle(img, _point(area[:2]), _point(area[2:]), c.danger, s.line_px, AA)
            pi, pt = _point(intruder.center), _point(target.center)
            cv2.line(img, pi, pt, c.danger, s.line_px, AA)
            self._label(img, f"INTRUSION {int(rel['depth_px'])}px", (pi[0] + pt[0]) // 2 - 40,
                        (pi[1] + pt[1]) // 2, c.danger)

    def _chains(self, img, things, result, rules: DeskRules, name: Callable[[str], str]) -> None:
        s, c = self._s, self._c
        for rel in result['relationships']:
            if rel['type'] != 'chain_reaction' or not all(x in things for x in rel['chain']):
                continue
            chain = rel['chain']                     # [A, the one in between, B]
            pts = [_point(things[x].center) for x in chain]
            self._dashed_line(img, *pts[0], *pts[1], c.chain, s.line_px, s.dash.chain)
            self._dashed_line(img, *pts[1], *pts[2], c.chain, s.line_px, s.dash.chain)
            cv2.circle(img, pts[1], s.radius.chain, c.chain, s.line_px, AA)
            cv2.circle(img, pts[1], s.radius.chain_ring, c.text, s.thin_line_px, AA)
            self._label(img, f'chain: {name(chain[0])}->{name(chain[1])}->{name(chain[2])}',
                        pts[1][0] - 60, pts[1][1] - 26, c.chain, scale=s.font_scale.chain)
        radius = rules.crowd_radius_px
        for rel in result['relationships']:
            if rel['type'] != 'crowd':
                continue
            visible = [x for x in rel['cluster'] if x in things]
            if len(visible) < 2:
                continue
            cx = int(sum(things[x].center[0] for x in visible) / len(visible))
            cy = int(sum(things[x].center[1] for x in visible) / len(visible))
            self._see_through_rect(img, cx - radius, cy - radius, cx + radius, cy + radius, c.warn,
                                   s.fill_alpha.crowd)
            self._label(img, f'{len(visible)} crowded', cx - 30, int(cy - radius + 14), c.warn,
                        scale=s.font_scale.crowd)

    def _moving(self, img, things, result, history) -> None:
        s, c = self._s, self._c
        trail = s.trail
        if len(history) >= trail.start_after:       # trails: where each thing was in the last few pictures
            recent = list(history)[-trail.keep:]
            for agent in things:
                pts = [_point(seen[agent].center) for seen in recent if agent in seen]
                if len(pts) >= 2:
                    for k, p in enumerate(pts[:-1]):
                        shade = int(trail.shade_from + trail.shade_range * k / len(pts))
                        dot = max(s.radius.trail_dot_min, s.radius.trail_dot - k)
                        cv2.circle(img, p, dot, (shade, shade, shade), -1, AA)
        for rel in result['relationships']:
            if rel['type'] != 'approaching' or rel['from'] not in things or rel['to'] not in things:
                continue
            pa, pb = _point(things[rel['from']].center), _point(things[rel['to']].center)
            self._arrow(img, pa, pb, c.moving)
            self._label(img, f"approaching {int(rel['rate_pct'])}%", (pa[0] + pb[0]) // 2 - 40,
                        (pa[1] + pb[1]) // 2 - 14, c.moving, scale=s.font_scale.moving)
        for rel in result['relationships']:
            if rel['type'] != 'repeated_intrusion' or rel['intruder'] not in things:
                continue
            box = things[rel['intruder']].box
            bx, by = int(box[2]) + 4, int(box[1])
            cv2.circle(img, (bx + 10, by + 10), s.radius.count, c.danger, -1)
            cv2.putText(img, str(rel['count']), (bx + 4, by + 16), FONT, s.font_scale.count, c.text,
                        s.bold_text_px, AA)

    def _risk_badge(self, img, result) -> None:
        s, c = self._s, self._c
        level = result['risk_level']
        color = c.risk[min(level, len(c.risk) - 1)]
        left, top, right, bottom = s.badge
        cv2.rectangle(img, (left, top), (right, bottom), c.badge_back, -1)
        cv2.rectangle(img, (left, top), (right, bottom), color, s.line_px)
        cv2.putText(img, f'RISK {level}/{len(c.risk) - 1}', (left + 8, top + 30), FONT, s.font_scale.risk, color,
                    s.bold_text_px, AA)
        cv2.putText(img, f"{len(result['risk_events'])} events", (left + 92, top + 30), FONT,
                    s.font_scale.risk_events, c.soft_text, s.thin_line_px, AA)
