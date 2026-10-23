"""
Box and point maths for the desk rules.

A box is (x1, y1, x2, y2) in pixels: top left, then bottom right. A point is (x, y).
A segment is two points: a line from one to the other.
"""

from __future__ import annotations

import math

Box = tuple[float, float, float, float]
Point = tuple[float, float]
Segment = tuple[Point, Point]


def distance(p: Point, q: Point) -> float:
    return math.hypot(p[0] - q[0], p[1] - q[1])


def grow(box: Box, px: float) -> Box:
    """The box made `px` pixels bigger on every side."""
    return (box[0] - px, box[1] - px, box[2] + px, box[3] + px)


def overlap(a: Box, b: Box) -> Box | None:
    """The part two boxes share, or None (boxes that only touch share nothing)."""
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    if x2 > x1 and y2 > y1:
        return (x1, y1, x2, y2)
    return None


def box_gap(a: Box, b: Box) -> float:
    """How far apart two boxes are, in pixels: the larger of the side-to-side gap and the up-and-down
    gap (0 when they touch or overlap)."""
    across = max(0.0, a[0] - b[2], b[0] - a[2])
    up_down = max(0.0, a[1] - b[3], b[1] - a[3])
    return max(across, up_down)


def point_segment_distance(p: Point, a: Point, b: Point) -> float:
    """Shortest distance from a point to the segment a-b."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    if dx == dy == 0:
        return math.hypot(p[0] - a[0], p[1] - a[1])
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy)))
    return math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy))


def _segments_cross(a: Point, b: Point, c: Point, d: Point) -> bool:
    def turn(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    return turn(a, b, c) * turn(a, b, d) < 0 and turn(c, d, a) * turn(c, d, b) < 0


def box_segment_distance(box: Box, seg: Segment) -> float:
    """Shortest distance between a box and a segment (0 if they touch)."""
    x1, y1, x2, y2 = box
    a, b = seg
    if (x1 <= a[0] <= x2 and y1 <= a[1] <= y2) or (x1 <= b[0] <= x2 and y1 <= b[1] <= y2):
        return 0.0
    corners = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
    sides = [(corners[i], corners[(i + 1) % 4]) for i in range(4)]
    if any(_segments_cross(a, b, c, d) for c, d in sides):
        return 0.0
    from_corners = min(point_segment_distance(c, a, b) for c in corners)
    from_ends = min(point_segment_distance(p, c, d) for p in (a, b) for c, d in sides)
    return min(from_corners, from_ends)


def footprint(box: Box, share: float, min_px: float) -> Box:
    """The bottom strip of a box (`share` of its height, at least `min_px`): about where the object
    stands on the desk."""
    x1, y1, x2, y2 = box
    return (x1, y2 - max(min_px, share * (y2 - y1)), x2, y2)
