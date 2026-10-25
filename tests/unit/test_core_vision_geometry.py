"""Box and point maths for the desk rules."""

import pytest

from desktalk.core.vision import geometry as g


def test_distance_between_two_points():
    assert g.distance((0, 0), (3, 4)) == 5


def test_grow_a_box_on_every_side():
    assert g.grow((10, 20, 30, 40), 5) == (5, 15, 35, 45)


def test_overlap_of_two_boxes():
    assert g.overlap((0, 0, 10, 10), (5, 5, 20, 20)) == (5, 5, 10, 10)


def test_boxes_that_only_touch_do_not_overlap():
    assert g.overlap((0, 0, 10, 10), (10, 0, 20, 10)) is None


@pytest.mark.parametrize('a, b, want', [
    ((0, 0, 10, 10), (5, 5, 20, 20), 0),          # overlapping
    ((0, 0, 10, 10), (10, 0, 20, 10), 0),         # touching
    ((0, 0, 10, 10), (25, 0, 30, 10), 15),        # side by side
    ((0, 40, 10, 50), (0, 0, 10, 10), 30),        # one above the other
    ((0, 0, 10, 10), (14, 30, 20, 40), 20),       # off to a corner: the larger of the two gaps
])
def test_gap_between_boxes(a, b, want):
    assert g.box_gap(a, b) == want == g.box_gap(b, a)


@pytest.mark.parametrize('point, a, b, want', [
    ((5, 5), (0, 0), (10, 0), 5),        # above the middle
    ((13, 4), (0, 0), (10, 0), 5),       # past the end: distance to the end point
    ((3, 4), (0, 0), (0, 0), 5),         # a segment that is only a point
])
def test_point_to_segment_distance(point, a, b, want):
    assert g.point_segment_distance(point, a, b) == pytest.approx(want)


@pytest.mark.parametrize('seg, want', [
    (((5, 5), (50, 50)), 0),             # starts inside the box
    (((-5, 5), (15, 5)), 0),             # runs right through the box
    (((20, 0), (20, 10)), 10),           # beside the box
    (((15, 15), (25, 25)), 7.0711),      # nearest the corner (10, 10)
])
def test_box_to_segment_distance(seg, want):
    assert g.box_segment_distance((0, 0, 10, 10), seg) == pytest.approx(want, abs=1e-4)


def test_footprint_is_the_bottom_strip_of_a_box():
    assert g.footprint((0, 0, 10, 100), share=0.2, min_px=4) == (0, 80, 10, 100)


def test_footprint_of_a_short_box_is_at_least_min_px_tall():
    assert g.footprint((0, 0, 10, 10), share=0.2, min_px=4) == (0, 6, 10, 10)
