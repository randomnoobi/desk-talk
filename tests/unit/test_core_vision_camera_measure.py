"""Lens's numbers from a picture (brightness, glare, sharpness, change), from boxes (cut off) and from the phone's
tilt. Pictures go through the picture tools, so the fake ones do here."""

import math

import numpy as np
import pytest

from desktalk.core.vision.camera_measure import CameraMeasure, Reading, cut_off_side, tilt_degrees
from desktalk.domain import Size
from tests.fakes import FakePictureTools

TOOLS = FakePictureTools()


def measure(width=240, white_level=250):
    return CameraMeasure(TOOLS, width=width, white_level=white_level)


def test_the_picture_is_measured_from_a_small_gray_copy():
    gray = measure(width=120).gray(np.full((360, 480, 3), 140, np.uint8))
    assert TOOLS.size(gray) == Size(120, 90) and gray.ndim == 2


def test_a_flat_picture_is_as_bright_as_its_pixels_with_no_glare_and_nothing_sharp():
    m = measure()
    assert m.reading(m.gray(np.full((360, 480, 3), 140, np.uint8))) == Reading(brightness=140.0, glare=0.0,
                                                                               sharpness=0.0)


def test_glare_is_the_share_of_pixels_at_the_white_level():
    picture = np.full((360, 480, 3), 255, np.uint8)
    picture[:, :240] = 100
    m = measure(white_level=250)
    assert m.reading(m.gray(picture)).glare == 0.5


def test_texture_is_sharp():
    picture = np.full((360, 480, 3), 140, np.uint8)
    picture[::8] = 30
    m = measure()
    assert m.reading(m.gray(picture)).sharpness > 0


def test_change_between_pictures_of_the_same_size():
    m = measure()
    a, b = m.gray(np.full((360, 480, 3), 100, np.uint8)), m.gray(np.full((360, 480, 3), 130, np.uint8))
    assert m.change(a, b) == 30.0


def test_pictures_of_different_sizes_count_as_completely_different():
    """Like PictureTools.difference: a picture that changed size (the camera turned or was swapped) changed a lot."""
    m = measure()
    a = m.gray(np.full((360, 480, 3), 100, np.uint8))
    b = measure(width=60).gray(np.full((360, 480, 3), 100, np.uint8))
    assert m.change(a, b) == math.inf


@pytest.mark.parametrize('box, side', [
    ((0, 200, 80, 300), 'left'), ((560, 200, 640, 300), 'right'), ((200, 0, 300, 80), 'top'),
    ((200, 400, 300, 480), 'bottom'), ((200, 200, 300, 300), None),
    ((0, 10, 600, 300), None),                  # fills most of the picture: turning the camera wouldn't help
])
def test_which_side_a_box_is_cut_off_at(box, side):
    assert cut_off_side(box, (640, 480), touch_px=6, fills=0.85) == side


def test_tilt_between_two_gravity_readings():
    assert round(tilt_degrees((0, 9.8, 0), (0, 9.8, 4), trusted=1.0)) == 22
    assert tilt_degrees((0, 9.8, 0), (0, 9.8, 0), trusted=1.0) == 0.0


def test_a_tiny_gravity_reading_is_not_trusted():
    assert tilt_degrees((0, 0.5, 0), (9.8, 0, 0), trusted=1.0) == 0.0


def test_how_strong_a_trusted_gravity_reading_must_be_comes_from_settings():
    """settings → lens.trusted_gravity: a reading weaker than that is noise, not a direction."""
    assert round(tilt_degrees((0, 0.5, 0), (0.5, 0, 0), trusted=0.4)) == 90
    assert tilt_degrees((0, 9.8, 0), (9.8, 0, 0), trusted=10) == 0.0
