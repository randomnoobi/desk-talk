"""Ultralytics predict() results → Detections (desktalk_shared.ultralytics_results), without Ultralytics."""

from __future__ import annotations

from types import SimpleNamespace as NS

from desktalk_shared.detection import BBox, Detection
from desktalk_shared.ultralytics_results import detections_from_results


class Col(list):
    """Stands in for a tensor column: has .tolist() like torch / numpy."""

    def tolist(self):
        return list(self)


def result(boxes, names, shape=(480, 640)):
    xyxy = Col([b[0] for b in boxes])
    return NS(names=names, orig_shape=shape,
              boxes=NS(xyxy=xyxy, conf=Col([b[1] for b in boxes]), cls=Col([b[2] for b in boxes])))


def test_turns_each_box_into_a_detection_with_the_models_word():
    r = result([([10.0, 20.0, 50.0, 100.0], 0.83, 41.0), ([0.0, 0.0, 5.0, 5.0], 0.4, 0.0)], {0: 'person', 41: 'cup'})
    assert detections_from_results([r]) == [Detection('cup', 0.83, BBox(10, 20, 50, 100)),
                                             Detection('person', 0.4, BBox(0, 0, 5, 5))]


def test_no_boxes_gives_an_empty_list():
    assert detections_from_results([result([], {0: 'person'})]) == []
    assert detections_from_results([NS(names={}, orig_shape=(1, 1), boxes=None)]) == []


def test_boxes_are_cut_to_the_picture():
    r = result([([-3.0, 10.0, 700.0, 50.0], 0.9, 0.0)], {0: 'person'}, shape=(480, 640))
    assert detections_from_results([r])[0].bbox == BBox(0, 10, 640, 50)


def test_names_can_be_given_instead_of_read_from_the_result():
    r = result([([1.0, 1.0, 2.0, 2.0], 0.5, 3.0)], {})
    assert detections_from_results([r], names={3: 'keys'})[0].label == 'keys'
