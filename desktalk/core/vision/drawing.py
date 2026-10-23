"""
Drawing on the live view: detection boxes, and the desk overlays (desk edges, danger zones, chains,
crowds, things moving, the risk badge). Colours, line widths and text sizes are drawing details and stay
here in the code on purpose. Colours are BGR (OpenCV's order).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import cv2

from desktalk.core.vision.danger_zones import standing_strip, zone_area
from desktalk.core.vision.desk_edges import desk_edges
from desktalk.core.vision.findings import Thing
from desktalk.core.vision.geometry import footprint, overlap
from desktalk.core.vision.rules import DeskRules
from desktalk.domain import Detection

BOX_ON = (60, 200, 90)           # green: an agent in the chat (or everything, when nobody is in the chat)
BOX_OFF = (150, 150, 150)        # grey: seen, but not chatting
DANGER = (0, 0, 255)             # red
WARN = (0, 190, 255)             # orange-yellow
ZONE_OK = (200, 160, 0)          # teal
ZONE_HIT = (0, 50, 230)          # dark red fill
CHAIN = (220, 80, 255)           # magenta
MOVING = (255, 200, 0)           # cyan
RISK_COLORS = [(0, 200, 0), (0, 220, 220), (0, 140, 255), (0, 0, 255), (0, 0, 160)]   # risk level 0-4
FONT = cv2.FONT_HERSHEY_SIMPLEX
AA = cv2.LINE_AA


def draw_boxes(picture: Any, detections: Sequence[Detection], highlight: set[str] | None = None) -> Any:
    """A box and "label 83%" for each detection: green if its label is in `highlight` (or there's no
    highlight), grey otherwise."""
    for d in detections:
        x1, y1, x2, y2 = (int(v) for v in (d.bbox.x1, d.bbox.y1, d.bbox.x2, d.bbox.y2))
        color = BOX_ON if highlight is None or d.label in highlight else BOX_OFF
        cv2.rectangle(picture, (x1, y1), (x2, y2), color, 2)
        text = f'{d.label} {d.confidence:.0%}'
        (tw, th), _ = cv2.getTextSize(text, FONT, 0.5, 1)
        ty = max(th + 6, y1)
        cv2.rectangle(picture, (x1, ty - th - 6), (x1 + tw + 6, ty), color, -1)
        cv2.putText(picture, text, (x1 + 3, ty - 4), FONT, 0.5, (255, 255, 255), 1, AA)
    return picture


# ── small helpers ──
def _dashed_line(img, x1, y1, x2, y2, color, thickness=1, dash=10, gap=6):
    length = math.hypot(x2 - x1, y2 - y1)
    if length < 1:
        return
    ux, uy = (x2 - x1) / length, (y2 - y1) / length
    pos, draw = 0.0, True
    while pos < length:
        end = min(pos + (dash if draw else gap), length)
        if draw:
            cv2.line(img, (int(x1 + ux * pos), int(y1 + uy * pos)), (int(x1 + ux * end), int(y1 + uy * end)),
                     color, thickness, AA)
        pos, draw = end, not draw


def _dashed_rect(img, x1, y1, x2, y2, color, thickness=1, dash=10, gap=6):
    for ax, ay, bx, by in ((x1, y1, x2, y1), (x2, y1, x2, y2), (x2, y2, x1, y2), (x1, y2, x1, y1)):
        _dashed_line(img, ax, ay, bx, by, color, thickness, dash, gap)


def _label(img, text, x, y, bg, fg=(255, 255, 255), scale=0.42, thick=1):
    """Text with a filled background."""
    (tw, th), _ = cv2.getTextSize(text, FONT, scale, thick)
    cv2.rectangle(img, (x - 3, y - th - 5), (x + tw + 3, y + 4), bg, -1)
    cv2.putText(img, text, (x, y), FONT, scale, fg, thick, AA)


def _arrow(img, pt1, pt2, color, thickness=2):
    cv2.arrowedLine(img, pt1, pt2, color, thickness, AA, tipLength=0.15)


def _see_through_rect(img, x1, y1, x2, y2, color, alpha=0.20):
    layer = img.copy()
    cv2.rectangle(layer, (int(x1), int(y1)), (int(x2), int(y2)), color, -1)
    cv2.addWeighted(layer, alpha, img, 1 - alpha, 0, img)


def _point(p) -> tuple[int, int]:
    return int(p[0]), int(p[1])


# ── the overlays ──
def draw_overlays(picture: Any, things: Mapping[str, Thing], names: Mapping[str, str],
                  result: Mapping[str, Any] | None, rules: DeskRules, history: Sequence[Mapping[str, Thing]]) -> None:
    """Draw what the desk analyzer found (`result`) for the agents' objects (`things`) onto `picture`.
    `names` are the agents' names, for the labels; `history` is the analyzer's recent pictures."""
    if not result:
        return

    def name(agent: str) -> str:
        if agent in names:
            return names[agent]
        return things[agent].label if agent in things else agent

    _draw_edges(picture, things, result, rules)
    _draw_zones(picture, things, result, rules)
    _draw_chains(picture, things, result, rules, name)
    _draw_moving(picture, things, result, history)
    _draw_risk_badge(picture, result)


def _draw_edges(img, things, result, rules: DeskRules):
    h, w = img.shape[:2]
    for a, b in desk_edges(rules.outline, (w, h), rules.desk_border_share):    # the edges you marked
        cv2.line(img, _point(a), _point(b), WARN, 2, AA)
    for rel in result['relationships']:
        if rel['type'] != 'edge_proximity' or rel['object'] not in things:
            continue
        box = things[rel['object']].box
        color = DANGER if rel['severity'] == 'danger' else WARN
        cv2.rectangle(img, _point(box[:2]), _point(box[2:]), color, 2, AA)
        seg = rel.get('segment')
        if seg:
            f = footprint(box, rules.footprint_share, rules.footprint_min_px)
            c = ((f[0] + f[2]) / 2, (f[1] + f[3]) / 2)
            (ax, ay), (bx, by) = seg
            dx, dy = bx - ax, by - ay       # the closest point on the edge, for the arrow
            along = 0.0 if dx == dy == 0 else ((c[0] - ax) * dx + (c[1] - ay) * dy) / (dx * dx + dy * dy)
            t = max(0.0, min(1.0, along))
            q = (int(ax + t * dx), int(ay + t * dy))
            _arrow(img, _point(c), q, color, 2)
            _label(img, f"{int(rel['distance_px'])}px to {rel['edge']} edge",
                   (int(c[0]) + q[0]) // 2, (int(c[1]) + q[1]) // 2, color)


def _draw_zones(img, things, result, rules: DeskRules):
    for thing in things.values():               # every zone, even with nothing in it
        zone = rules.zones.get(thing.label)
        if zone is None:
            continue
        area = zone_area(thing.box, zone, rules)    # around the part that sits on the desk
        _dashed_rect(img, int(area[0]), int(area[1]), int(area[2]), int(area[3]), ZONE_OK, 1, 8, 6)
        _label(img, zone.label, int(area[0]) + 4, int(area[1]) + 14, (60, 60, 60), (180, 180, 180), 0.35)
    for rel in result['relationships']:          # zones with something risky in them
        if rel['type'] != 'danger_zone' or rel['intruder'] not in things or rel['target'] not in things:
            continue
        intruder, target = things[rel['intruder']], things[rel['target']]
        zone = rules.zones.get(target.label)
        if zone is None:
            continue
        area = zone_area(target.box, zone, rules)
        shared = overlap(standing_strip(intruder.box, rules), area)    # where the risky thing stands
        if shared:
            _see_through_rect(img, *shared, ZONE_HIT, 0.30)
        cv2.rectangle(img, _point(area[:2]), _point(area[2:]), DANGER, 2, AA)
        pi, pt = _point(intruder.center), _point(target.center)
        cv2.line(img, pi, pt, DANGER, 2, AA)
        _label(img, f"INTRUSION {int(rel['depth_px'])}px", (pi[0] + pt[0]) // 2 - 40, (pi[1] + pt[1]) // 2, DANGER)


def _draw_chains(img, things, result, rules: DeskRules, name: Callable[[str], str]):
    for rel in result['relationships']:
        if rel['type'] != 'chain_reaction' or not all(c in things for c in rel['chain']):
            continue
        chain = rel['chain']                     # [A, the one in between, B]
        pts = [_point(things[c].center) for c in chain]
        _dashed_line(img, *pts[0], *pts[1], CHAIN, 2, 8, 5)
        _dashed_line(img, *pts[1], *pts[2], CHAIN, 2, 8, 5)
        cv2.circle(img, pts[1], 16, CHAIN, 2, AA)
        cv2.circle(img, pts[1], 18, (255, 255, 255), 1, AA)
        _label(img, f'chain: {name(chain[0])}->{name(chain[1])}->{name(chain[2])}',
               pts[1][0] - 60, pts[1][1] - 26, CHAIN, scale=0.35)
    radius = rules.crowd_radius_px
    for rel in result['relationships']:
        if rel['type'] != 'crowd':
            continue
        visible = [c for c in rel['cluster'] if c in things]
        if len(visible) < 2:
            continue
        cx = int(sum(things[c].center[0] for c in visible) / len(visible))
        cy = int(sum(things[c].center[1] for c in visible) / len(visible))
        _see_through_rect(img, cx - radius, cy - radius, cx + radius, cy + radius, WARN, 0.10)
        _label(img, f'{len(visible)} crowded', cx - 30, int(cy - radius + 14), WARN, scale=0.4)


def _draw_moving(img, things, result, history):
    if len(history) >= 3:                       # trails: where each thing was in the last few pictures
        recent = list(history)[-6:]
        for agent in things:
            pts = [_point(seen[agent].center) for seen in recent if agent in seen]
            if len(pts) >= 2:
                for k, p in enumerate(pts[:-1]):
                    shade = int(80 + 120 * k / len(pts))
                    cv2.circle(img, p, max(2, 4 - k), (shade, shade, shade), -1, AA)
    for rel in result['relationships']:
        if rel['type'] != 'approaching' or rel['from'] not in things or rel['to'] not in things:
            continue
        pa, pb = _point(things[rel['from']].center), _point(things[rel['to']].center)
        _arrow(img, pa, pb, MOVING, 2)
        _label(img, f"approaching {int(rel['rate_pct'])}%", (pa[0] + pb[0]) // 2 - 40, (pa[1] + pb[1]) // 2 - 14,
               MOVING, scale=0.38)
    for rel in result['relationships']:
        if rel['type'] != 'repeated_intrusion' or rel['intruder'] not in things:
            continue
        box = things[rel['intruder']].box
        bx, by = int(box[2]) + 4, int(box[1])
        cv2.circle(img, (bx + 10, by + 10), 14, DANGER, -1)
        cv2.putText(img, str(rel['count']), (bx + 4, by + 16), FONT, 0.5, (255, 255, 255), 2, AA)


def _draw_risk_badge(img, result):
    level = result['risk_level']
    color = RISK_COLORS[min(level, 4)]
    cv2.rectangle(img, (8, 8), (160, 52), (0, 0, 0), -1)
    cv2.rectangle(img, (8, 8), (160, 52), color, 2)
    cv2.putText(img, f'RISK {level}/4', (16, 38), FONT, 0.7, color, 2, AA)
    cv2.putText(img, f"{len(result['risk_events'])} events", (100, 38), FONT, 0.35, (180, 180, 180), 1, AA)
