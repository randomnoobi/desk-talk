"""Proof photos: the live view picture (cropped around the objects) kept with an alert."""

import numpy as np
import pytest

from desktalk.adapters.picture import OpenCvPictureTools
from desktalk.core.vision.proof_photos import ProofPhotos
from desktalk.domain import Size
from tests.fakes import FakeClock, FakePictureTools
from tests.unit.test_core_vision_kit import FakeDesk, app_config, seen_object

SNAPSHOTS = app_config().snapshots
TOOLS = OpenCvPictureTools()


def jpeg(w=640, h=480, value=120, tools=TOOLS):
    picture = np.random.default_rng(1).integers(0, 255, (h, w, 3), dtype=np.uint8)
    picture[:] = np.clip(picture.astype(int) // 4 + value, 0, 255).astype(np.uint8)
    data = tools.encode_jpeg(picture, 95)
    assert data is not None
    return data


def shape(data, tools=TOOLS):
    picture = tools.decode_jpeg(data)
    size = tools.size(picture)
    return size.height, size.width


NO_LABEL = app_config().prompts.photos.no_label


def photos(desk=None, clock=None, settings=SNAPSHOTS, tools=TOOLS, no_label=NO_LABEL):
    desk = desk or FakeDesk()
    return ProofPhotos(desk, clock or FakeClock(), settings, pictures=tools, no_label=no_label), desk


def show(desk, picture, cam=0):
    desk.put(cam, picture, picture, Size(640, 480))


def kept(desk, photo_id):
    photo = desk.get(photo_id)
    assert photo is not None
    return photo.jpeg


def test_no_photo_before_the_first_picture():
    p, _ = photos()
    assert p.save('Proximity alert') is None


def test_photo_is_cropped_around_the_objects_with_some_room():
    p, desk = photos()
    show(desk, jpeg())
    snap = p.save('Proximity alert', [(200, 200, 400, 350)])
    # 25% more on each side: 50 px left and right, 37.5 px above and below
    assert shape(kept(desk, snap)) == (387 - 162, 450 - 150)


def test_cropping_goes_through_the_picture_tools():
    fake = FakePictureTools()
    p, desk = photos(tools=fake)
    show(desk, jpeg(tools=fake))
    snap = p.save('Proximity alert', [(200, 200, 400, 350)])
    assert shape(kept(desk, snap), fake) == (387 - 162, 450 - 150)


def test_several_boxes_are_cropped_together():
    p, desk = photos()
    show(desk, jpeg())
    snap = p.save('Proximity alert', [(200, 200, 300, 300), (300, 300, 400, 400)])
    assert shape(kept(desk, snap)) == (450 - 150, 450 - 150)


def test_a_tiny_crop_keeps_the_whole_picture():
    p, desk = photos()
    show(desk, jpeg())
    snap = p.save('Proximity alert', [(10, 10, 20, 20)])
    assert shape(kept(desk, snap)) == (480, 640)


def test_a_picture_that_does_not_read_is_kept_as_it_is():
    p, desk = photos()
    show(desk, b'not a picture')
    assert kept(desk, p.save('Proximity alert', [(200, 200, 400, 350)])) == b'not a picture'


def test_the_picture_tools_must_be_passed_in():
    with pytest.raises(TypeError):
        ProofPhotos(FakeDesk(), FakeClock(), SNAPSHOTS)        # type: ignore[call-arg]


def test_without_boxes_the_whole_picture_is_kept():
    p, desk = photos()
    show(desk, picture := jpeg())
    assert kept(desk, p.save('Lens: camera view')) == picture


def test_crop_room_comes_from_the_settings():
    p, desk = photos(settings=SNAPSHOTS.model_copy(update={'crop_padding': 0.5}))
    show(desk, jpeg())
    snap = p.save('Proximity alert', [(200, 200, 400, 300)])
    assert shape(kept(desk, snap)) == (350 - 150, 500 - 100)


def test_jpeg_quality_comes_from_the_settings():
    sizes = []
    for quality in (20, 95):
        p, desk = photos(settings=SNAPSHOTS.model_copy(update={'jpeg_quality': quality}))
        show(desk, jpeg())
        sizes.append(len(kept(desk, p.save('x', [(100, 100, 400, 400)]))))
    assert sizes[0] < sizes[1]


def test_photo_is_listed_newest_first_with_what_was_on_the_desk():
    clock = FakeClock(start=5000)
    p, desk = photos(clock=clock)
    show(desk, jpeg())
    desk.set_members({'cup': seen_object('cup', (10, 10, 50, 50))}, 5000)
    desk.note_picture({'Cup': seen_object('cup', (10, 10, 50, 50)), 'laptop': seen_object('laptop', (0, 0, 9, 9))},
                      5000, person=False, moved=False, new_session=False)
    desk.set_scan_labels({'Keys'})
    first = p.save('First')
    clock.advance(3)
    second = p.save('Second')
    assert [x.id for x in desk.newest(5)] == [second, first]
    assert desk.newest(1)[0].to_dict() == {'id': second, 'ts': 5003, 'label': 'Second', 'char_ids': ['cup'],
                                           'labels': ['cup', 'laptop'], 'vlm_labels': ['keys']}


def test_a_photo_without_a_label_is_a_capture():
    p, desk = photos()
    show(desk, jpeg())
    photo = desk.get(p.save('') or '')
    assert photo is not None and photo.label == 'Capture'


def test_the_label_of_a_photo_without_one_comes_from_prompts_yaml():
    p, desk = photos(no_label='Snapshot')
    show(desk, jpeg())
    photo = desk.get(p.save('') or '')
    assert photo is not None and photo.label == 'Snapshot'


def test_the_first_camera_with_a_picture_is_used():
    p, desk = photos()
    desk.put(2, jpeg(320, 240), jpeg(320, 240), Size(320, 240))
    desk.put(1, b'', b'', Size(320, 240))
    assert shape(kept(desk, p.save('x'))) == (240, 320)
