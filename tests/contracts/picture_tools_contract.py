"""What every PictureTools must do (desktalk.ports.PictureTools). Pictures are numpy arrays: BGR colour
(height x width x 3) or gray (height x width)."""

from __future__ import annotations

import math

import numpy as np

from desktalk.domain import Size


def colour(width: int = 64, height: int = 48, value: int = 128) -> np.ndarray:
    return np.full((height, width, 3), value, np.uint8)


def gray(width: int = 64, height: int = 48, value: int = 128) -> np.ndarray:
    return np.full((height, width), value, np.uint8)


class PictureToolsContract:
    """Subclass and give a `tools` fixture."""

    def test_a_picture_comes_back_from_its_jpeg(self, tools):
        jpeg = tools.encode_jpeg(colour(value=128), 90)
        assert isinstance(jpeg, bytes) and jpeg
        back = tools.decode_jpeg(jpeg)
        assert tools.size(back) == Size(64, 48)
        assert abs(tools.brightness(tools.to_gray(back)) - 128) < 3

    def test_a_jpeg_can_be_read_straight_into_gray(self, tools):
        back = tools.decode_jpeg(tools.encode_jpeg(colour(value=200), 90), gray=True)
        assert np.asarray(back).ndim == 2
        assert tools.size(back) == Size(64, 48)
        assert abs(tools.brightness(back) - 200) < 3

    def test_something_that_is_not_a_jpeg_reads_as_none(self, tools):
        assert tools.decode_jpeg(b'') is None
        assert tools.decode_jpeg(b'this is not a picture') is None
        assert tools.decode_jpeg(b'this is not a picture', gray=True) is None

    def test_an_empty_picture_makes_no_jpeg(self, tools):
        assert tools.encode_jpeg(np.zeros((0, 0, 3), np.uint8), 90) is None

    def test_size_is_width_then_height(self, tools):
        assert tools.size(colour(64, 48)) == Size(width=64, height=48)
        assert tools.size(gray(30, 20)) == Size(width=30, height=20)

    def test_shrink_keeps_the_shape_unless_told_the_height(self, tools):
        assert tools.size(tools.shrink(colour(64, 48), 32)) == Size(32, 24)
        assert tools.size(tools.shrink(gray(64, 48), 16, 16)) == Size(16, 16)

    def test_to_gray_keeps_the_size_and_the_brightness(self, tools):
        out = tools.to_gray(colour(64, 48, value=100))
        assert np.asarray(out).ndim == 2
        assert tools.size(out) == Size(64, 48)
        assert abs(tools.brightness(out) - 100) <= 1

    def test_a_gray_picture_stays_as_it_is(self, tools):
        out = tools.to_gray(gray(value=70))
        assert np.asarray(out).ndim == 2 and abs(tools.brightness(out) - 70) <= 1

    def test_crop_cuts_out_the_box_and_stays_inside_the_picture(self, tools):
        assert tools.size(tools.crop(colour(64, 48), (10, 5, 30, 25))) == Size(20, 20)
        assert tools.size(tools.crop(colour(64, 48), (-10, -10, 1000, 1000))) == Size(64, 48)

    def test_brightness_and_glare(self, tools):
        black, white = gray(value=0), gray(value=255)
        half = gray(value=0)
        half[:, :32] = 255
        assert tools.brightness(black) == 0 and tools.brightness(white) == 255
        assert tools.glare(black, 250) == 0 and tools.glare(white, 250) == 1
        assert tools.glare(half, 250) == 0.5

    def test_difference_is_0_to_255(self, tools):
        assert tools.difference(gray(value=40), gray(value=40)) == 0
        assert tools.difference(gray(value=0), gray(value=255)) == 255
        assert tools.difference(gray(value=100), gray(value=110)) == 10

    def test_pictures_of_different_sizes_are_completely_different(self, tools):
        assert math.isinf(tools.difference(gray(64, 48), gray(32, 24)))

    def test_a_flat_picture_is_not_sharp_and_stripes_are(self, tools):
        stripes = gray(value=40)
        stripes[::4] = 220
        assert tools.sharpness(gray(value=128)) < 1
        assert tools.sharpness(stripes) > 100
