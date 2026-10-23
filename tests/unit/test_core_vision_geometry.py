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


def test_intrusion_depth_is_the_thinner_side_of_the_overlap():
    assert g.intrusion_depth((0, 0, 10, 4), (0, 0, 100, 100)) == 4
    assert g.intrusion_depth((200, 200, 210, 210), (0, 0, 100, 100)) == 0


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
