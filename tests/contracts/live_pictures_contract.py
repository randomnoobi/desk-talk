"""What every LivePictures must do (desktalk.ports.LivePictures): the newest pictures for the live view."""

from __future__ import annotations

from desktalk.domain import Light, Size


class LivePicturesContract:
    """Subclass and give a `pictures` fixture (no picture yet)."""

    def test_nothing_before_the_first_picture(self, pictures):
        assert pictures.plain() is None and pictures.plain(0) is None
        assert pictures.boxed() is None and pictures.boxed(0) is None
        assert pictures.camera_numbers() == []
        assert isinstance(pictures.size(), Size)
        assert pictures.newest_light() is None

    def test_the_newest_picture_of_a_camera(self, pictures):
        pictures.put(0, b'plain 1', b'boxes 1', Size(320, 240))
        pictures.put(0, b'plain 2', b'boxes 2', Size(320, 240))
        assert (pictures.plain(0), pictures.boxed(0)) == (b'plain 2', b'boxes 2')
        assert (pictures.plain(), pictures.boxed()) == (b'plain 2', b'boxes 2')
        assert pictures.camera_numbers() == [0]
        assert pictures.size() == Size(320, 240)

    def test_without_a_camera_number_the_lowest_camera_with_a_picture_answers(self, pictures):
        pictures.put(2, b'plain from 2', b'boxes from 2', Size(640, 480))
        pictures.put(1, b'plain from 1', b'boxes from 1', Size(640, 480))
        assert pictures.camera_numbers() == [1, 2]
        assert pictures.plain() == b'plain from 1' and pictures.boxed() == b'boxes from 1'
        assert pictures.plain(2) == b'plain from 2' and pictures.plain(5) is None

    def test_a_picture_without_boxes(self, pictures):
        pictures.put(0, b'plain', None, Size(640, 480))
        assert pictures.plain(0) == b'plain' and pictures.boxed(0) is None

    def test_the_light_in_the_newest_picture(self, pictures):
        pictures.set_light(120.5, 30.0)
        assert pictures.newest_light() == Light(brightness=120.5, lux=30.0)
        pictures.set_light(90.0, None)
        assert pictures.newest_light() == Light(brightness=90.0, lux=None)
