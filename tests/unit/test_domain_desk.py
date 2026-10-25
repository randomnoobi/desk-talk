"""The desk right now as plain data (desktalk/domain/desk.py)."""

from __future__ import annotations

from desktalk.domain import BBox, Danger, Detection, Photo, SeenObject


def test_a_seen_object_from_a_detection_is_rounded_like_the_app_always_showed():
    seen = SeenObject.from_detection(Detection('cup', 0.91234, BBox(10.04, 20, 50, 80.06)), cam=1)
    assert seen == SeenObject(label='cup', box=(10.0, 20.0, 50.0, 80.1), center=(30.0, 50.0), confidence=0.912,
                              cam=1)


def test_a_danger_round_trips_through_the_shape_the_app_reads():
    spill = Danger(pair=('cup', 'laptop'), label='Spill', kind='zone_intrusion', depth_px=12.0)
    edge = Danger(pair=('cup', 'desk_edge'), label='cup near left edge', kind='edge_danger')
    assert spill.to_dict() == {'pair': ['cup', 'laptop'], 'label': 'Spill', 'depth_px': 12.0, 'type': 'zone_intrusion'}
    assert edge.to_dict() == {'pair': ['cup', 'desk_edge'], 'label': 'cup near left edge', 'type': 'edge_danger'}
    assert Danger.from_dict(spill.to_dict()) == spill and Danger.from_dict(edge.to_dict()) == edge


def test_a_photo_is_sent_to_the_app_without_its_picture():
    photo = Photo(id='abc', ts=100.0, label='Spill', jpeg=b'jpeg', char_ids=('cup',), labels=('cup', 'laptop'))
    assert photo.to_dict() == {'id': 'abc', 'ts': 100.0, 'label': 'Spill', 'char_ids': ['cup'],
                               'labels': ['cup', 'laptop'], 'vlm_labels': []}
    assert 'jpeg' not in repr(photo)
