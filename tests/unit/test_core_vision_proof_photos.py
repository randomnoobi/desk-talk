"""Proof photos: the live view picture (cropped around the objects) kept with an alert."""

import cv2
import numpy as np

from desktalk.core.vision.proof_photos import ProofPhotos
from desktalk.desk_state import DeskState
from tests.fakes import FakeClock
from tests.unit.test_core_vision_kit import app_config, desk_object

SNAPSHOTS = app_config().snapshots


def jpeg(w=640, h=480, value=120):
    picture = np.random.default_rng(1).integers(0, 255, (h, w, 3), dtype=np.uint8)
    picture[:] = np.clip(picture.astype(int) // 4 + value, 0, 255).astype(np.uint8)
    return cv2.imencode('.jpg', picture)[1].tobytes()


def shape(data):
    return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR).shape[:2]


def photos(desk=None, clock=None, settings=SNAPSHOTS):
    desk = desk or DeskState()
    return ProofPhotos(desk, clock or FakeClock(), settings), desk


def test_no_photo_before_the_first_picture():
    p, _ = photos()
    assert p.save('Proximity alert') is None


def test_photo_is_cropped_around_the_objects_with_some_room():
    p, desk = photos()
    desk.frames[0] = jpeg()
    snap = p.save('Proximity alert', [(200, 200, 400, 350)])
    # 25% more on each side: 50 px left and right, 37.5 px above and below
    assert shape(desk.snapshots[snap]) == (387 - 162, 450 - 150)


def test_several_boxes_are_cropped_together():
    p, desk = photos()
    desk.frames[0] = jpeg()
    snap = p.save('Proximity alert', [(200, 200, 300, 300), (300, 300, 400, 400)])
    assert shape(desk.snapshots[snap]) == (450 - 150, 450 - 150)


def test_a_tiny_crop_keeps_the_whole_picture():
    p, desk = photos()
    desk.frames[0] = jpeg()
    snap = p.save('Proximity alert', [(10, 10, 20, 20)])
    assert shape(desk.snapshots[snap]) == (480, 640)


def test_without_boxes_the_whole_picture_is_kept():
    p, desk = photos()
    desk.frames[0] = jpeg()
    assert desk.snapshots[p.save('Lens: camera view')] == desk.frames[0]


def test_crop_room_comes_from_the_settings():
    p, desk = photos(settings=SNAPSHOTS.model_copy(update={'crop_padding': 0.5}))
    desk.frames[0] = jpeg()
    snap = p.save('Proximity alert', [(200, 200, 400, 300)])
    assert shape(desk.snapshots[snap]) == (350 - 150, 500 - 100)


def test_jpeg_quality_comes_from_the_settings():
    sizes = []
    for quality in (20, 95):
        p, desk = photos(settings=SNAPSHOTS.model_copy(update={'jpeg_quality': quality}))
        desk.frames[0] = jpeg()
        sizes.append(len(desk.snapshots[p.save('x', [(100, 100, 400, 400)])]))
    assert sizes[0] < sizes[1]


def test_photo_is_listed_newest_first_with_what_was_on_the_desk():
    clock = FakeClock(start=5000)
    p, desk = photos(clock=clock)
    desk.frames[0] = jpeg()
    desk.objects = {'cup': desk_object('cup', (10, 10, 50, 50))}
    desk.seen_labels = {'Cup': {}, 'laptop': {}}
    first = p.save('First')
    clock.advance(3)
    second = p.save('Second')
    assert [s['id'] for s in desk.snapshot_order] == [second, first]
    assert desk.snapshot_order[0] == {'id': second, 'ts': 5003, 'label': 'Second', 'char_ids': ['cup'],
                                      'labels': ['cup', 'laptop'], 'vlm_labels': []}


def test_only_the_newest_photos_are_kept():
    p, desk = photos(desk=DeskState(max_snapshots=2))
    desk.frames[0] = jpeg()
    ids = [p.save(f'photo {i}') for i in range(3)]
    assert list(desk.snapshots) == ids[1:]
    assert [s['id'] for s in desk.snapshot_order] == ids[:0:-1]


def test_the_first_camera_with_a_picture_is_used():
    p, desk = photos()
    desk.frames[2] = jpeg(320, 240)
    desk.frames[1] = b''
    assert shape(desk.snapshots[p.save('x')]) == (240, 320)
