"""
The desk outline (the corners you tap in Settings) and its real edges: things close to an edge can
fall off.

The outline is kept in 0-1 of the picture size, so it fits any picture size. Sides that run along the
border of the picture aren't real edges: the desk just goes on out of view there. Without an outline
there's no way to know where the desk ends, so there are no edge warnings.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from desktalk.config.prompts import DeskCheckWords
from desktalk.core.vision.findings import Findings, Thing
from desktalk.core.vision.geometry import Point, Segment, box_segment_distance
from desktalk.core.vision.rules import standing_strip
from desktalk.domain import DeskRules

Size = tuple[float, float]           # picture width, height


def _in_pixels(outline: Sequence[Point], size: Size) -> list[Point]:
    w, h = size
    return [(x * w, y * h) for x, y in outline]


def desk_edges(outline: Sequence[Point], size: Size, border_share: float) -> list[Segment]:
    """The real desk edges in pixels: the outline's sides, minus those along the picture border
    (corners within `border_share` of the picture size from it)."""
    w, h = size
    corners = _in_pixels(outline, size)
    if len(corners) < 3:
        return []

    def borders(p: Point) -> set[str]:
        sides = set()
        if p[0] <= w * border_share:
            sides.add('left')
        if p[0] >= w * (1 - border_share):
            sides.add('right')
        if p[1] <= h * border_share:
            sides.add('top')
        if p[1] >= h * (1 - border_share):
            sides.add('bottom')
        return sides

    edges = []
    for i, a in enumerate(corners):
        b = corners[(i + 1) % len(corners)]
        if not borders(a) & borders(b):        # both corners on the same picture border = not a real edge
            edges.append((a, b))
    return edges


def edge_name(seg: Segment, size: Size) -> str:
    """front, back, left or right: from where the middle of the edge is in the picture."""
    w, h = size
    dx = (seg[0][0] + seg[1][0]) / 2 / w - 0.5
    dy = (seg[0][1] + seg[1][1]) / 2 / h - 0.5
    if abs(dy) >= abs(dx):
        return 'front' if dy > 0 else 'back'
    return 'right' if dx > 0 else 'left'


def inside_outline(p: Point, outline: Sequence[Point], size: Size) -> bool:
    """The point (in pixels) is on the desk."""
    corners = _in_pixels(outline, size)
    inside = False
    for i, a in enumerate(corners):
        b = corners[(i + 1) % len(corners)]
        if (a[1] > p[1]) != (b[1] > p[1]):
            x_cross = a[0] + (p[1] - a[1]) * (b[0] - a[0]) / (b[1] - a[1])
            if p[0] < x_cross:
                inside = not inside
    return inside


def on_desk(p: Point, outline: Sequence[Point], size: Size) -> bool:
    """The point (in pixels) is on the desk, or no outline is marked (so there's no telling: it counts)."""
    return not outline or inside_outline(p, outline, size)


def edge_risks(things: Mapping[str, Thing], rules: DeskRules, size: Size, *, words: DeskCheckWords) -> Findings:
    """Edge objects close to a real desk edge: closer than edge_danger_px is a danger, closer than
    edge_warn_px a warning."""
    found = Findings()
    edges = desk_edges(rules.outline, size, rules.desk_border_share)
    if not edges:
        return found
    for thing in things.values():
        if thing.label not in rules.edge_objects:
            continue
        # The bottom of the box, where the object stands. The camera looks at the desk at an angle, so the top
        # of a tall bottle can look like it's past the back edge even when it stands safely in the middle.
        foot = standing_strip(thing.box, rules)
        base = ((foot[0] + foot[2]) / 2, foot[3])
        gap, seg = min((box_segment_distance(foot, e), e) for e in edges)
        if not inside_outline(base, rules.outline, size) and gap >= rules.edge_danger_px:
            continue                            # not on the desk (in your hand, or on the floor)
        side = edge_name(seg, size)
        if gap < rules.edge_danger_px:
            severity, kind = 'danger', 'edge_danger'
            explanation = words.edge.format(object=thing.label, side=side)
        elif gap < rules.edge_warn_px:
            severity, kind, explanation = 'warning', 'edge_warning', None
        else:
            continue
        found.add({'type': kind, 'object': thing.id, 'class': thing.label, 'edge': side,
                   'distance_px': round(gap, 1)},
                  {'type': 'edge_proximity', 'severity': severity, 'object': thing.id, 'edge': side,
                   'distance_px': round(gap, 1), 'segment': [list(map(round, seg[0])), list(map(round, seg[1]))]},
                  explanation)
    return found
