"""Sips from the camera (desktalk/core/habits/habit_reader.py): a sip counts only when a drink is seen lifted and then
put back where it rested. A hand over the cup, a covered camera or a cup taken away is no sip.

Pictures are 640×480, three a second; the cup's box is 40×60. With protocols.yaml's numbers, lifted = its middle
rose more than 0.08 × 480 ≈ 38 px, and put back = within 0.15 × 800 = 120 px of where it rested.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from desktalk.config import load_config
from desktalk.core.agents.profiles import ProfileBook
from desktalk.core.habits.tracker import HabitTracker
from desktalk.domain import BBox, Detection, DetectionEvent, HabitEvent, Light
from desktalk.util import at_time_today
from tests.fakes import FakeClock, FakeDeskScene, FakePreferences, InMemoryKeyValueStore, SyncEventBus

ROOT = Path(__file__).resolve().parents[2]
SIZE = (640, 480)
PER_SEC = 3
CUP_DOWN = BBox(300, 380, 340, 440)          # resting on the desk
CUP_UP = BBox(300, 300, 340, 360)            # 80 px higher: in the hand
LAPTOP = BBox(60, 200, 260, 350)
PHONE = BBox(450, 380, 500, 460)


@pytest.fixture(scope='module')
def config(tmp_path_factory):
    root = tmp_path_factory.mktemp('project')
    (root / 'config').mkdir()
    for name in ('settings.yaml', 'agents.yaml', 'protocols.yaml', 'prompts.yaml'):
        shutil.copy(ROOT / 'config' / name, root / 'config' / name)
    return load_config(root, environ={})


def shifted(box: BBox, dx: float = 0, dy: float = 0) -> BBox:
    return BBox(box.x1 + dx, box.y1 + dy, box.x2 + dx, box.y2 + dy)


class Desk:
    """A habit tracker fed made-up pictures, three a second, each with the boxes given."""

    def __init__(self, config, **hydration):
        rules = config.protocols
        if hydration:
            rules = rules.model_copy(update={'hydration': rules.hydration.model_copy(update=hydration)})
        self.clock = FakeClock(start=at_time_today('09:00', FakeClock().now()))
        self.bus = SyncEventBus()
        profiles = ProfileBook(config.agents_file, InMemoryKeyValueStore(), personality_max_chars=400)
        self.tracker = HabitTracker(
            self.clock, self.bus, InMemoryKeyValueStore(), profiles,
            FakePreferences({'wake_time': '07:00', 'bed_time': '22:30'}), FakeDeskScene(), rules,
            config.habit_tracker, config.presence, config.agents, phone_counts=True, words=config.prompts.habits)

    def picture(self, boxes: dict[str, BBox]) -> None:
        dets = tuple(Detection(label, 0.9, box) for label, box in boxes.items())
        self.bus.publish(DetectionEvent(ts=self.clock.now(), frame_id='f', source='pi', size=SIZE,
                                        detections=dets, person=True, light=Light(brightness=120)))
        self.clock.advance(1 / PER_SEC)

    def show(self, seconds: float, **boxes: BBox) -> None:
        """`seconds` of pictures, each with these boxes (none = an empty picture)."""
        for _ in range(round(seconds * PER_SEC)):
            self.picture(boxes)

    def move(self, seconds: float, start: BBox, end: BBox, **others: BBox) -> None:
        """The cup moves evenly from `start` to `end` over `seconds` (ending at `end`), the others stay put."""
        steps = round(seconds * PER_SEC)
        for i in range(1, steps + 1):
            f = i / steps
            cup = BBox(*(a + (b - a) * f for a, b in zip(start.to_list(), end.to_list(), strict=True)))
            self.picture({**others, 'cup': cup})

    def sips(self) -> int:
        return self.tracker.today(self.clock.now())['hydration']['sips']


def test_lift_and_put_back_is_a_sip(config):
    d = Desk(config)
    d.show(10, cup=CUP_DOWN)
    d.move(1, CUP_DOWN, CUP_UP)                 # picked up
    d.show(4, cup=CUP_UP)                       # held up: drinking
    d.move(1, CUP_UP, CUP_DOWN)                 # put back
    d.show(3, cup=CUP_DOWN)
    assert d.sips() == 1
    sip = [e for e in d.bus.of_type(HabitEvent) if e.action == 'sip']
    assert len(sip) == 1 and sip[0].to_dict()['how'] == 'camera'


def test_hand_over_cup_is_not_a_sip(config):
    d = Desk(config)
    d.show(10, cup=CUP_DOWN, laptop=LAPTOP)
    d.show(5, laptop=LAPTOP)                    # a hand over the cup: it vanishes where it stands
    d.show(5, cup=CUP_DOWN, laptop=LAPTOP)
    assert d.sips() == 0


def test_covered_camera_is_not_a_sip(config):
    d = Desk(config)
    d.show(10, cup=CUP_DOWN, laptop=LAPTOP, phone=PHONE)
    d.show(5)                                   # everything vanishes in the same picture
    d.show(5, cup=CUP_DOWN, laptop=LAPTOP, phone=PHONE)
    assert d.sips() == 0


def test_lifted_then_lost_then_back_is_a_sip(config):
    d = Desk(config)
    d.show(10, cup=CUP_DOWN, laptop=LAPTOP)
    d.move(1, CUP_DOWN, CUP_UP, laptop=LAPTOP)
    d.show(6, laptop=LAPTOP)                    # the camera loses it while it's up in the hand
    d.show(5, cup=CUP_DOWN, laptop=LAPTOP)
    assert d.sips() == 1


def test_lifted_then_camera_covered_is_not_a_sip(config):
    d = Desk(config)
    d.show(10, cup=CUP_DOWN, laptop=LAPTOP, phone=PHONE)
    d.move(1, CUP_DOWN, CUP_UP, laptop=LAPTOP, phone=PHONE)
    d.show(5)                                   # then the camera is covered
    d.show(5, cup=CUP_DOWN, laptop=LAPTOP, phone=PHONE)   # uncovered, the cup back on the desk
    assert d.sips() == 0


def test_lifted_then_lost_then_covered_is_not_a_sip(config):
    d = Desk(config)
    d.show(10, cup=CUP_DOWN, laptop=LAPTOP, phone=PHONE)
    d.move(1, CUP_DOWN, CUP_UP, laptop=LAPTOP, phone=PHONE)
    d.show(2, laptop=LAPTOP, phone=PHONE)       # lost in the hand ...
    d.show(4)                                   # ... and then the camera is covered too
    d.show(5, cup=CUP_DOWN, laptop=LAPTOP, phone=PHONE)
    assert d.sips() == 0


def test_lifted_never_back_is_no_sip(config):
    d = Desk(config)
    d.show(10, cup=CUP_DOWN, laptop=LAPTOP)
    d.move(1, CUP_DOWN, CUP_UP, laptop=LAPTOP)
    d.show(300, laptop=LAPTOP)                  # taken to the kitchen
    d.show(10, cup=BBox(500, 400, 540, 460), laptop=LAPTOP)    # 5 minutes later a cup appears somewhere else
    assert d.sips() == 0


def test_a_new_cup_that_appears_after_the_old_one_was_taken_away_can_still_be_sipped(config):
    d = Desk(config)
    d.show(10, cup=CUP_DOWN, laptop=LAPTOP)
    d.move(1, CUP_DOWN, CUP_UP, laptop=LAPTOP)
    d.show(300, laptop=LAPTOP)
    elsewhere = BBox(500, 400, 540, 460)
    d.show(10, cup=elsewhere, laptop=LAPTOP)
    d.move(1, elsewhere, shifted(elsewhere, dy=-80), laptop=LAPTOP)
    d.show(3, cup=shifted(elsewhere, dy=-80), laptop=LAPTOP)
    d.move(1, shifted(elsewhere, dy=-80), elsewhere, laptop=LAPTOP)
    d.show(3, cup=elsewhere, laptop=LAPTOP)
    assert d.sips() == 1


def test_put_back_too_late_is_no_sip(config):
    d = Desk(config)                            # sip_gone_max_sec is 120
    d.show(10, cup=CUP_DOWN, laptop=LAPTOP)
    d.move(1, CUP_DOWN, CUP_UP, laptop=LAPTOP)
    d.show(180, laptop=LAPTOP)
    d.show(5, cup=CUP_DOWN, laptop=LAPTOP)
    assert d.sips() == 0


def test_held_up_too_long_is_no_sip(config):
    d = Desk(config)
    d.show(10, cup=CUP_DOWN)
    d.move(1, CUP_DOWN, CUP_UP)
    d.show(180, cup=CUP_UP)                     # held up in view for 3 minutes
    d.move(1, CUP_UP, CUP_DOWN)
    d.show(5, cup=CUP_DOWN)
    assert d.sips() == 0


def test_a_quick_bob_is_no_sip(config):
    d = Desk(config)                            # sip_gone_min_sec is 3
    d.show(10, cup=CUP_DOWN)
    d.show(1, cup=CUP_UP)
    d.show(5, cup=CUP_DOWN)
    assert d.sips() == 0


def test_small_wobble_is_no_sip(config):
    d = Desk(config)
    d.show(10, cup=CUP_DOWN)
    d.show(5, cup=shifted(CUP_DOWN, dy=-10))     # the box moves 10 px up ...
    d.show(5, cup=CUP_DOWN)                     # ... and back
    assert d.sips() == 0


def test_the_cameras_jitter_does_not_move_the_resting_spot(config):
    d = Desk(config)
    wobble = [(0, 0), (3, -4), (-2, 3), (4, 2), (-3, -3), (1, 4)]    # a few pixels each picture, as cameras do
    for i in range(60 * PER_SEC):
        dx, dy = wobble[i % len(wobble)]
        d.picture({'cup': shifted(CUP_DOWN, dx, dy)})
    assert d.sips() == 0
    d.move(1, CUP_DOWN, CUP_UP)                 # then a real sip still counts
    d.show(3, cup=CUP_UP)
    d.move(1, CUP_UP, CUP_DOWN)
    d.show(3, cup=CUP_DOWN)
    assert d.sips() == 1


def test_a_cup_slid_along_the_desk_and_back_is_no_sip(config):
    d = Desk(config)
    d.show(10, cup=CUP_DOWN)
    d.move(1, CUP_DOWN, shifted(CUP_DOWN, dx=-200))
    d.show(5, cup=shifted(CUP_DOWN, dx=-200))
    d.move(1, shifted(CUP_DOWN, dx=-200), CUP_DOWN)
    d.show(5, cup=CUP_DOWN)
    assert d.sips() == 0


def test_put_down_somewhere_else_is_no_sip_and_it_rests_there_after(config):
    d = Desk(config)
    d.show(10, cup=CUP_DOWN)
    d.move(1, CUP_DOWN, CUP_UP)
    d.show(3, cup=CUP_UP)
    far = shifted(CUP_DOWN, dx=-250)
    d.move(1, CUP_UP, far)                      # put down a long way from where it was
    d.show(130, cup=far)
    assert d.sips() == 0
    d.move(1, far, shifted(far, dy=-80))        # a sip from its new spot counts
    d.show(3, cup=shifted(far, dy=-80))
    d.move(1, shifted(far, dy=-80), far)
    d.show(3, cup=far)
    assert d.sips() == 1


def test_each_drink_is_watched_on_its_own(config):
    d = Desk(config)
    bottle = BBox(450, 340, 490, 440)
    d.show(10, cup=CUP_DOWN, bottle=bottle)
    d.move(1, CUP_DOWN, CUP_UP, bottle=bottle)  # the mug is lifted; the bottle stays
    d.show(3, cup=CUP_UP, bottle=bottle)
    d.move(1, CUP_UP, CUP_DOWN, bottle=bottle)
    d.show(3, cup=CUP_DOWN, bottle=bottle)
    assert d.sips() == 1


def test_how_far_up_comes_from_protocols_yaml(config):
    d = Desk(config, sip_lift_share=0.2)        # 96 px: an 80 px lift is not enough now
    d.show(10, cup=CUP_DOWN)
    d.move(1, CUP_DOWN, CUP_UP)
    d.show(4, cup=CUP_UP)
    d.move(1, CUP_UP, CUP_DOWN)
    d.show(3, cup=CUP_DOWN)
    assert d.sips() == 0


@pytest.mark.parametrize(('back_share', 'sips'), [(0.15, 1), (0.1, 0)])
def test_how_close_to_put_it_back_comes_from_protocols_yaml(config, back_share, sips):
    d = Desk(config, sip_back_share=back_share)  # put back 100 px to the side: 120 px is close enough, 80 px isn't
    d.show(10, cup=CUP_DOWN)
    d.move(1, CUP_DOWN, CUP_UP)
    d.show(4, cup=CUP_UP)
    d.show(3, cup=shifted(CUP_DOWN, dx=100))
    assert d.sips() == sips


@pytest.mark.parametrize(('cover_share', 'sips'), [(0.6, 1), (0.5, 0)])
def test_how_much_vanishing_means_covered_comes_from_protocols_yaml(config, cover_share, sips):
    d = Desk(config, sip_cover_share=cover_share)
    d.show(10, cup=CUP_DOWN, laptop=LAPTOP, phone=PHONE)
    d.move(1, CUP_DOWN, CUP_UP, laptop=LAPTOP, phone=PHONE)
    d.show(4, laptop=LAPTOP)                    # the phone (half of the rest) vanishes with the cup
    d.show(5, cup=CUP_DOWN, laptop=LAPTOP, phone=PHONE)
    assert d.sips() == sips


def test_the_lift_to_put_back_times_come_from_protocols_yaml(config):
    d = Desk(config, sip_gone_min_sec=6, sip_gone_max_sec=10)
    d.show(10, cup=CUP_DOWN)
    d.move(1, CUP_DOWN, CUP_UP)
    d.show(3, cup=CUP_UP)                       # about 4 s from leaving to settling: too quick now
    d.move(1, CUP_UP, CUP_DOWN)
    d.show(3, cup=CUP_DOWN)
    assert d.sips() == 0
    d.move(1, CUP_DOWN, CUP_UP)
    d.show(6, cup=CUP_UP)                       # about 7 s: a sip
    d.move(1, CUP_UP, CUP_DOWN)
    d.show(3, cup=CUP_DOWN)
    assert d.sips() == 1
