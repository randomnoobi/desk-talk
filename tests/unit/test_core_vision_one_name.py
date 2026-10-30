"""One thing, one name: a second name the detector gives the same box is left out before anyone sees it."""

import logging

from desktalk.core.vision.one_name import OneNameEach
from desktalk.domain import BBox, Detection

LAPTOP = Detection('laptop', 0.95, BBox(574, 183, 1094, 713))
BOOK_ON_LAPTOP = Detection('book', 0.52, BBox(574, 180, 1126, 716))     # shares about 0.93 with the laptop
PHONE_ON_LAPTOP = Detection('cell phone', 0.8, BBox(700, 400, 780, 560))
PERSON = Detection('person', 0.9, BBox(0, 0, 640, 480))
IGNORE = ('person', 'picture/frame')


def test_the_second_name_for_the_same_box_is_left_out():
    assert OneNameEach(0.7, IGNORE)([BOOK_ON_LAPTOP, LAPTOP]) == [LAPTOP]


def test_a_word_with_its_own_agent_beats_a_new_things_word():
    vase = Detection('vase', 0.52, BBox(100, 100, 140, 160))
    cup = Detection('cup', 0.50, BBox(100, 100, 140, 160))
    assert OneNameEach(0.7, IGNORE, known_labels=('Cup',))([vase, cup]) == [cup]
    assert OneNameEach(0.7, IGNORE, known_labels=('cup', 'vase'))([vase, cup]) == [vase]


def test_the_surer_name_wins():
    book = Detection('book', 0.8, BOOK_ON_LAPTOP.bbox)
    weak_laptop = Detection('laptop', 0.5, LAPTOP.bbox)
    assert OneNameEach(0.7, IGNORE)([weak_laptop, book]) == [book]


def test_a_small_thing_on_a_big_thing_still_counts_and_the_order_is_kept():
    found = [PERSON, PHONE_ON_LAPTOP, LAPTOP]
    assert OneNameEach(0.7, IGNORE)(found) == found


def test_how_much_overlap_means_one_thing_comes_from_the_settings():
    assert OneNameEach(0.99, IGNORE)([LAPTOP, BOOK_ON_LAPTOP]) == [LAPTOP, BOOK_ON_LAPTOP]


def test_an_ignored_word_cannot_knock_out_the_real_name():
    # a monitor the camera calls "tv" (0.60) and, surer, "picture/frame" (ignored: it never becomes anything)
    tv = Detection('tv', 0.6, BBox(100, 50, 500, 300))
    frame = Detection('picture/frame', 0.7, BBox(102, 50, 500, 302))
    assert OneNameEach(0.7, IGNORE)([tv, frame]) == [tv, frame]          # the ignored word stays as it is
    assert OneNameEach(0.7, ('PICTURE/FRAME',))([tv, frame]) == [tv, frame]   # the ignore list's case doesn't matter


def test_an_ignored_word_is_never_knocked_out_either():
    # the producer still needs "person" for someone at the desk
    coat = Detection('jacket', 0.95, PERSON.bbox)
    assert OneNameEach(0.7, IGNORE)([PERSON, coat]) == [PERSON, coat]


def test_each_drop_is_logged_once_per_stretch_with_both_names(caplog):
    one_name = OneNameEach(0.7, IGNORE)
    with caplog.at_level(logging.DEBUG, logger='desktalk.core.vision.one_name'):
        one_name([LAPTOP, BOOK_ON_LAPTOP])
        one_name([LAPTOP, BOOK_ON_LAPTOP])
        one_name([LAPTOP])                                   # the stretch ends
        one_name([LAPTOP, BOOK_ON_LAPTOP])
    drops = [r for r in caplog.records if 'book' in r.getMessage()]
    assert len(drops) == 2
    assert all(r.levelno == logging.DEBUG and 'laptop' in r.getMessage() for r in drops)
