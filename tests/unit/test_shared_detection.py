"""Detection and BBox: the one way boxes travel between the Pi, the detector and the app."""

from __future__ import annotations

import pytest

from desktalk_shared.detection import BBox, Detection, keep_inside


def test_bbox_center_size_and_area():
    b = BBox(10, 20, 50, 100)
    assert b.center == (30, 60)
    assert (b.width, b.height, b.area) == (40, 80, 3200)


def test_bbox_rejects_upside_down_boxes():
    with pytest.raises(ValueError, match='x2'):
        BBox(50, 20, 10, 100)


def test_bbox_rejects_non_numbers():
    with pytest.raises(ValueError):
        BBox.from_list([1, 2, 'three', 4])
    with pytest.raises(ValueError):
        BBox.from_list([1, 2, 3])
    with pytest.raises(ValueError):
        BBox.from_list([1, 2, float('nan'), 4])


def test_clipped_cuts_box_to_picture_and_drops_box_fully_outside():
    assert BBox(-10, 5, 30, 500).clipped(640, 480) == BBox(0, 5, 30, 480)
    assert BBox(700, 10, 800, 20).clipped(640, 480) is None
    assert BBox(10, 10, 10, 20).clipped(640, 480) is None     # no area left


def test_detection_dict_has_todays_shape():
    d = Detection('cup', 0.83456, BBox(10.04, 20, 50, 100.06))
    assert d.to_dict() == {'label': 'cup', 'conf': 0.835, 'bbox': [10.0, 20.0, 50.0, 100.1], 'center': [30.0, 60.0]}


def test_detection_round_trips_through_dict():
    d = Detection('cup', 0.8, BBox(10, 20, 50, 100))
    assert Detection.from_dict(d.to_dict()) == d


def test_detection_from_dict_names_the_bad_field():
    with pytest.raises(ValueError, match='conf'):
        Detection.from_dict({'label': 'cup', 'conf': 1.5, 'bbox': [0, 0, 1, 1]})
    with pytest.raises(ValueError, match='label'):
        Detection.from_dict({'label': '', 'conf': 0.5, 'bbox': [0, 0, 1, 1]})
    with pytest.raises(ValueError, match='bbox'):
        Detection.from_dict({'label': 'cup', 'conf': 0.5})


def test_keep_inside_clips_and_drops():
    dets = [Detection('cup', 0.9, BBox(-5, 0, 20, 20)), Detection('pen', 0.9, BBox(900, 0, 950, 20))]
    assert keep_inside(dets, 640, 480) == [Detection('cup', 0.9, BBox(0, 0, 20, 20))]
