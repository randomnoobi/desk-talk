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


def test_same_box_share_is_how_much_two_boxes_are_one_box():
    assert g.same_box_share((0, 0, 10, 10), (0, 0, 10, 10)) == pytest.approx(1.0)
    assert g.same_box_share((0, 0, 10, 10), (5, 0, 15, 10)) == pytest.approx(50 / 150)
    assert g.same_box_share((0, 0, 10, 10), (20, 20, 30, 30)) == 0.0
    assert g.same_box_share((0, 0, 0, 0), (0, 0, 0, 0)) == 0.0          # no area: nothing shared


LAPTOP = (574, 183, 1094, 713)
BOOK_ON_LAPTOP = (574, 180, 1126, 716)           # these two share about 0.93 of the area they cover


def test_a_second_name_on_the_same_box_loses_to_the_surer_one():
    found = [('book', 0.52, BOOK_ON_LAPTOP), ('laptop', 0.95, LAPTOP)]
    assert g.same_thing_drops(found, 0.7) == {0: 1}          # the book (0) is dropped for the laptop (1)
    assert g.same_thing_drops([('book', 0.8, BOOK_ON_LAPTOP), ('laptop', 0.5, LAPTOP)], 0.7) == {1: 0}


def test_a_known_word_beats_a_new_things_word_on_the_same_box():
    """A word with its own agent keeps the box over a word for a new thing, even a little less sure."""
    mug = (0, 0, 40, 60)
    found = [('vase', 0.52, mug), ('cup', 0.50, mug)]
    assert g.same_thing_drops(found, 0.7, known={'cup'}) == {0: 1}      # the vase (0) is dropped for the cup (1)
    assert g.same_thing_drops(found, 0.7) == {1: 0}                     # without known words, the surer wins
    assert g.same_thing_drops(found, 0.7, known={'cup', 'vase'}) == {1: 0}     # both known: the surer wins


def test_two_names_count_as_two_things_when_the_boxes_share_less():
    found = [('laptop', 0.95, LAPTOP), ('book', 0.52, BOOK_ON_LAPTOP)]
    assert g.same_thing_drops(found, 0.99) == {}
    small_on_big = [('laptop', 0.95, LAPTOP), ('cell phone', 0.8, (700, 400, 780, 560))]
    assert g.same_thing_drops(small_on_big, 0.7) == {}


def test_two_boxes_with_the_same_name_are_never_merged():
    assert g.same_thing_drops([('cup', 0.9, (0, 0, 10, 10)), ('cup', 0.5, (0, 0, 10, 10))], 0.7) == {}


def test_a_dropped_name_cannot_drop_another():
    # tv 0.9 drops monitor 0.8; monitor would have dropped screen 0.7, but it's gone, and screen shares little with tv
    found = [('tv', 0.9, (0, 0, 10, 10)), ('monitor', 0.8, (0, 0, 10, 12)), ('screen', 0.7, (0, 2, 10, 13))]
    assert g.same_thing_drops(found, 0.7) == {1: 0}
