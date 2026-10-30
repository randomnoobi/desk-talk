"""Sips from the camera (desktalk/core/habits/habit_reader.py): a sip counts when a drink is seen lifted and then
put back where it rested, or when it goes missing and comes back near where it rested but moved a little. A hand
over the cup (it comes back exactly where it was), a covered camera, a cup taken away, or a trip with nobody at the
desk is no sip.

Pictures are 640×480, three a second; the cup's box is 40×60. With protocols.yaml's numbers, lifted = its middle
rose more than 0.08 × 480 ≈ 38 px, put back = within 0.15 × 800 = 120 px of where it rested, and moved = its box's
middle is more than 0.007 × 800 = 5.6 px off its resting box's middle.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from desktalk.config import load_config
from desktalk.core.agents.profiles import ProfileBook
from desktalk.core.agents.sensitivity import Slider
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
CUP_BACK = BBox(330, 380, 370, 440)          # back from a drink: 30 px off its old spot, as real drinks come back
BOTTLE = BBox(500, 400, 540, 460)            # a second drink, elsewhere on the desk (a little lower)
HAND = BBox(260, 340, 420, 480)              # an arm reaching over the cup: its box holds the cup's middle
SOMEONE_ELSE = BBox(0, 100, 50, 300)         # a person at the left edge, far from the cup
REAL_DRINKS = ROOT / 'tests' / 'fixtures' / 'real_drinks_2026_10_25.json'


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
        self.person = True                      # whether the pictures show a person (DetectionEvent.person)
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
        self.detections(*(Detection(label, 0.9, box) for label, box in boxes.items()))

    def detections(self, *dets: Detection) -> None:
        """One picture with exactly these detections (two boxes may have the same word)."""
        self.bus.publish(DetectionEvent(ts=self.clock.now(), frame_id='f', source='pi', size=SIZE,
                                        detections=dets, person=self.person, light=Light(brightness=120)))
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


def test_covered_by_a_hand_and_back_in_the_same_place_is_not_a_sip(config):
    """An arm over the cup hides it, and the detector names the arm in only the first second (on the user's desk it
    named it in 1 of 19 such pictures). When the hand goes, the cup is exactly where it was: no sip."""
    d = Desk(config)
    d.show(10, cup=CUP_DOWN, laptop=LAPTOP)
    d.show(1, laptop=LAPTOP, person=HAND)
    d.show(4, laptop=LAPTOP)
    d.show(5, cup=shifted(CUP_DOWN, dx=1, dy=1), laptop=LAPTOP)     # the detector's pixel or two of wobble
    assert d.sips() == 0


def test_a_person_over_its_spot_during_a_real_drink_is_still_a_sip(config):
    """A webcam facing the user sees the whole body, and that box covers the cup's spot the whole time."""
    d = Desk(config)
    d.show(10, cup=CUP_DOWN, laptop=LAPTOP, person=HAND)
    d.show(5, laptop=LAPTOP, person=HAND)
    d.show(5, cup=CUP_BACK, laptop=LAPTOP, person=HAND)
    assert d.sips() == 1


def test_gone_and_back_to_the_same_box_is_no_sip(config):
    """The detector missed it for 3 s (its sureness dipped, the Pi slowed down, a book was over it): it comes back
    the same box within 2 px. Every real drink came back moved."""
    d = Desk(config)
    d.show(10, cup=CUP_DOWN, laptop=LAPTOP)
    d.show(3, laptop=LAPTOP)
    d.show(5, cup=shifted(CUP_DOWN, dx=2, dy=-1), laptop=LAPTOP)
    assert d.sips() == 0


def test_gone_and_back_moved_is_a_sip(config):
    """The camera looks down and the cup sits at the picture's top edge: drinking takes it straight out of the
    picture, never seen rising inside it. It comes back 30 px off its old spot."""
    d = Desk(config)
    d.show(10, cup=CUP_DOWN, laptop=LAPTOP)
    d.show(5, laptop=LAPTOP, person=SOMEONE_ELSE)
    d.show(5, cup=CUP_BACK, laptop=LAPTOP)
    assert d.sips() == 1


def test_gone_and_back_with_the_same_middle_but_a_taller_box_is_no_sip(config):
    """The detector missed it for 3 s and then drew its box 16 px taller around the same middle: only the box
    changed, the cup didn't move. Every real drink's box came back with its middle moved 14 px or more."""
    d = Desk(config)
    d.show(10, cup=CUP_DOWN, laptop=LAPTOP)
    d.show(3, laptop=LAPTOP)
    d.show(5, cup=BBox(300, 372, 340, 448), laptop=LAPTOP)
    assert d.sips() == 0


@pytest.mark.parametrize('how', ['gone and back', 'lifted'])
def test_a_trip_with_nobody_at_the_desk_is_no_sip(config, how):
    """No person in any picture: nobody drank, whatever the cup's box did."""
    d = Desk(config)
    d.person = False
    d.show(10, cup=CUP_DOWN, laptop=LAPTOP)
    if how == 'lifted':
        d.move(1, CUP_DOWN, CUP_UP, laptop=LAPTOP)
        d.show(4, cup=CUP_UP, laptop=LAPTOP)
        d.move(1, CUP_UP, CUP_DOWN, laptop=LAPTOP)
    else:
        d.show(5, laptop=LAPTOP)
    d.show(5, cup=CUP_BACK, laptop=LAPTOP)
    assert d.sips() == 0


def test_a_person_seen_only_before_the_cup_left_is_no_sip(config):
    """Someone was at the desk, then left; after that the cup went missing and came back moved."""
    d = Desk(config)
    d.show(10, cup=CUP_DOWN, laptop=LAPTOP)
    d.person = False
    d.show(5, laptop=LAPTOP)
    d.show(5, cup=CUP_BACK, laptop=LAPTOP)
    assert d.sips() == 0


def test_a_person_seen_in_one_picture_while_the_cup_was_away_is_enough(config):
    """Looking down, the camera may see the arm only once while the cup is out of the picture."""
    d = Desk(config)
    d.person = False
    d.show(10, cup=CUP_DOWN, laptop=LAPTOP)
    d.show(2, laptop=LAPTOP)
    d.person = True
    d.picture({'laptop': LAPTOP})
    d.person = False
    d.show(3, laptop=LAPTOP)
    d.show(5, cup=CUP_BACK, laptop=LAPTOP)
    assert d.sips() == 1


@pytest.mark.parametrize(('needs_person', 'sips'), [(True, 0), (False, 1)])
def test_whether_a_sip_needs_a_person_comes_from_protocols_yaml(config, needs_person, sips):
    d = Desk(config, sip_needs_person=needs_person)
    d.person = False
    d.show(10, cup=CUP_DOWN, laptop=LAPTOP)
    d.show(5, laptop=LAPTOP)
    d.show(5, cup=CUP_BACK, laptop=LAPTOP)
    assert d.sips() == sips


def test_after_gone_and_back_it_rests_where_it_came_back(config):
    """Put down a little higher than before: a detector miss there afterwards is no second sip."""
    d = Desk(config)
    d.show(10, cup=CUP_DOWN, laptop=LAPTOP)
    d.show(5, laptop=LAPTOP)
    higher = shifted(CUP_DOWN, dx=20, dy=-20)
    d.show(5, cup=higher, laptop=LAPTOP)
    assert d.sips() == 1
    d.show(3, laptop=LAPTOP)                    # missed for a while
    d.show(5, cup=higher, laptop=LAPTOP)
    assert d.sips() == 1


@pytest.mark.parametrize(('moved_share', 'sips'), [(0.01, 1), (0.05, 0)])
def test_how_far_it_must_come_back_moved_comes_from_protocols_yaml(config, moved_share, sips):
    d = Desk(config, sip_moved_share=moved_share)  # 30 px off: more than 8 px, less than 40 px
    d.show(10, cup=CUP_DOWN, laptop=LAPTOP)
    d.show(5, laptop=LAPTOP)
    d.show(5, cup=CUP_BACK, laptop=LAPTOP)
    assert d.sips() == sips


@pytest.mark.parametrize('nudge', [0, 6])
def test_a_drink_whose_word_switches_and_back_is_no_sip(config, nudge):
    """Looking down, the detector often calls the same bottle "cup", then "bottle", then "cup" again. While it's
    "bottle" it is still there, even if it was nudged a little meanwhile (6 px: more than a sip's move, but still
    nearly the same box, 0.74 of it)."""
    d = Desk(config)
    d.show(10, cup=CUP_DOWN, laptop=LAPTOP)
    d.show(3, bottle=shifted(CUP_DOWN, dx=nudge), laptop=LAPTOP)
    d.show(5, cup=shifted(CUP_DOWN, dx=nudge), laptop=LAPTOP)
    d.show(3, bottle=shifted(CUP_DOWN, dx=nudge), laptop=LAPTOP)
    d.show(5, cup=shifted(CUP_DOWN, dx=nudge), laptop=LAPTOP)
    assert d.sips() == 0


BOTTLE_BEHIND = BBox(300, 300, 340, 400)     # a bottle at the back of the desk
MUG_IN_FRONT = BBox(280, 330, 380, 450)      # a mug in front of it: its box holds the bottle's middle (0.21 the same)


def test_a_sip_from_a_bottle_behind_a_mug_counts(config):
    """The bottle leaves straight out of the picture and comes back 30 px off. The mug's box covers the bottle's
    middle all the while, but it is another drink, not the bottle under another word."""
    d = Desk(config)
    d.show(10, bottle=BOTTLE_BEHIND, cup=MUG_IN_FRONT, laptop=LAPTOP)
    d.show(4, cup=MUG_IN_FRONT, laptop=LAPTOP)
    d.show(5, bottle=shifted(BOTTLE_BEHIND, dx=30), cup=MUG_IN_FRONT, laptop=LAPTOP)
    assert d.sips() == 1


def test_how_much_the_same_box_another_drink_word_must_be_comes_from_the_settings(config):
    d = Desk(config, sip_same_drink_overlap=0.2)            # now the mug's box counts as the bottle renamed
    d.show(10, bottle=BOTTLE_BEHIND, cup=MUG_IN_FRONT, laptop=LAPTOP)
    d.show(4, cup=MUG_IN_FRONT, laptop=LAPTOP)
    d.show(5, bottle=shifted(BOTTLE_BEHIND, dx=30), cup=MUG_IN_FRONT, laptop=LAPTOP)
    assert d.sips() == 0


def test_two_cups_are_each_watched_on_their_own(config):
    """A mug and a bottle both called "cup": when the mug is up at the mouth, the bottle doesn't take its spot."""
    d = Desk(config)
    mug, bottle = Detection('cup', 0.9, CUP_DOWN), Detection('cup', 0.8, BOTTLE)
    for _ in range(10 * PER_SEC):
        d.detections(mug, bottle)
    for _ in range(5 * PER_SEC):
        d.detections(bottle)                    # the mug is out of the picture
    for _ in range(5 * PER_SEC):
        d.detections(Detection('cup', 0.9, CUP_BACK), bottle)
    assert d.sips() == 1


def test_the_slider_is_read_once_a_picture(config, monkeypatch):
    calls = []
    real_step = Slider.step
    monkeypatch.setattr(Slider, 'step', lambda self: calls.append(1) or real_step(self))
    d = Desk(config)
    d.picture({'cup': CUP_DOWN, 'laptop': LAPTOP, 'person': HAND, 'cell phone': PHONE})
    assert len(calls) == 1


def test_gone_too_long_then_back_is_no_sip(config):
    d = Desk(config)                            # sip_gone_max_sec is 120
    d.show(10, cup=CUP_DOWN, laptop=LAPTOP)
    d.show(180, laptop=LAPTOP)                  # taken to the kitchen and refilled
    d.show(5, cup=CUP_BACK, laptop=LAPTOP)
    assert d.sips() == 0


def test_gone_and_back_somewhere_far_away_is_no_sip_and_it_rests_there_after(config):
    d = Desk(config)
    d.show(10, cup=CUP_DOWN, laptop=LAPTOP)
    d.show(5, laptop=LAPTOP)
    far = shifted(CUP_DOWN, dx=-250)
    d.show(5, cup=far, laptop=LAPTOP)           # put down a long way from where it was
    assert d.sips() == 0
    d.show(5, laptop=LAPTOP)                    # a drink from its new spot counts
    d.show(5, cup=shifted(far, dx=30), laptop=LAPTOP)
    assert d.sips() == 1


def test_gone_for_a_moment_is_no_sip(config):
    d = Desk(config)                            # sip_gone_min_sec is 2
    d.show(10, cup=CUP_DOWN, laptop=LAPTOP)
    d.show(1, laptop=LAPTOP)                    # the detector misses it for a few pictures
    d.show(5, cup=CUP_BACK, laptop=LAPTOP)
    assert d.sips() == 0


def test_no_pictures_for_a_while_while_it_is_gone_is_no_sip(config):
    d = Desk(config)
    d.show(10, cup=CUP_DOWN, laptop=LAPTOP)
    d.show(1, laptop=LAPTOP)
    d.clock.advance(config.protocols.hydration.sip_stall_sec + 0.5)    # the camera stalled
    d.show(3, cup=CUP_BACK, laptop=LAPTOP)
    assert d.sips() == 0


def test_covered_camera_while_it_is_gone_is_no_sip(config):
    d = Desk(config)
    d.show(10, cup=CUP_DOWN, laptop=LAPTOP, phone=PHONE)
    d.show(2, laptop=LAPTOP, phone=PHONE)       # gone ...
    d.show(3)                                   # ... and then the camera is covered too
    d.show(5, cup=CUP_BACK, laptop=LAPTOP, phone=PHONE)
    assert d.sips() == 0


def replay(config, path: Path, **hydration) -> list[float]:
    """Feed a recording of real pictures (and every box in them) to a habit tracker. Returns when each sip was
    counted, in whole seconds from the first picture."""
    pictures = json.loads(path.read_text())
    d = Desk(config, **hydration)
    start = pictures[0]['ts']
    for picture in pictures:
        d.clock.advance(picture['ts'] - d.clock.now())
        dets = tuple(Detection(x['label'], x['conf'], BBox(*x['bbox'])) for x in picture['detections'])
        width, height = picture['size']
        d.bus.publish(DetectionEvent(ts=picture['ts'], frame_id='f', source='pi', size=(width, height),
                                     detections=dets, person=any(x.label == 'person' for x in dets),
                                     light=Light(brightness=120)))
    return [round(e.ts - start) for e in d.bus.of_type(HabitEvent) if e.action == 'sip']


def test_the_users_real_drinks_count(config):
    """90 s of the user's desk (1280×720, about every other picture kept). The camera looks down and the bottle
    rests at the top edge, so each drink takes it straight out of the picture: an arm reaches over it, it is gone
    for 3 to 7 pictures (the detector named the arm in only one of them), then it comes back near where it was but
    moved: its box's middle is 41 to 47 px off, or (the last) 14 px. A person is in every picture. All four
    drinks count, each when the bottle is back: after 4.0, 2.7, 5.4 and 3.4 s away."""
    assert replay(config, REAL_DRINKS) == [15, 26, 36, 52]


def test_the_least_moved_real_drink_needs_sip_moved_share_below_0_0096(config):
    """The four real drinks came back with their box's middle 46, 47, 41 and 14 px off where it rested. The last
    one's 14.1 px is 0.0096 of the 1468 px diagonal: 0.01 (14.7 px) leaves it out, which is why protocols.yaml
    has 0.007 (10.3 px), still far above a detector miss's 2 px. (That drink's box also came back 20 px wider,
    but only the middle counts: a box drawn bigger or smaller around the same middle is the detector, not a
    drink.)"""
    assert replay(config, REAL_DRINKS, sip_moved_share=0.01) == [15, 26, 36]


def test_the_shortest_real_drink_needs_sip_gone_min_sec_below_3(config):
    """The second drink took 2.7 s from the last picture of the bottle resting to it resting again: with 3 s it
    would be left out, which is why protocols.yaml has 2."""
    assert replay(config, REAL_DRINKS, sip_gone_min_sec=3) == [15, 36, 52]


def test_covered_camera_is_not_a_sip(config):
    d = Desk(config)
    d.show(10, cup=CUP_DOWN, laptop=LAPTOP, phone=PHONE)
    d.show(5)                                   # everything vanishes in the same picture
    d.show(5, cup=CUP_BACK, laptop=LAPTOP, phone=PHONE)
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


def test_one_picture_that_looks_covered_while_the_cup_is_in_the_hand_is_still_a_sip(config):
    """The detector misses everything in one picture (it happens): that alone is not a covered camera."""
    d = Desk(config)
    d.show(10, cup=CUP_DOWN, laptop=LAPTOP)
    d.move(1, CUP_DOWN, CUP_UP, laptop=LAPTOP)
    d.show(2, laptop=LAPTOP)                    # lost in the hand
    d.picture({})                               # one picture with nothing sure enough in it
    d.show(2, laptop=LAPTOP)
    d.show(3, cup=CUP_DOWN, laptop=LAPTOP)
    assert d.sips() == 1


def test_how_many_pictures_in_a_row_make_a_covered_camera_comes_from_protocols_yaml(config):
    d = Desk(config, sip_cover_pictures=1)
    d.show(10, cup=CUP_DOWN, laptop=LAPTOP)
    d.move(1, CUP_DOWN, CUP_UP, laptop=LAPTOP)
    d.show(2, laptop=LAPTOP)
    d.picture({})                               # now one such picture is enough
    d.show(2, laptop=LAPTOP)
    d.show(3, cup=CUP_DOWN, laptop=LAPTOP)
    assert d.sips() == 0


def sip_with_a_gap(d: Desk, gap_sec: float) -> int:
    """The cup is lifted, and while it's up the next picture comes `gap_sec` after the one before (a slow or
    stuck camera). Returns the sips counted."""
    d.show(10, cup=CUP_DOWN, laptop=LAPTOP)
    d.move(1, CUP_DOWN, CUP_UP, laptop=LAPTOP)
    d.show(1, laptop=LAPTOP)
    d.clock.advance(gap_sec - 1 / PER_SEC)      # show() already moved the clock on by one picture
    d.show(3, cup=CUP_DOWN, laptop=LAPTOP)
    return d.sips()


def test_no_pictures_for_a_while_during_a_lift_is_not_a_sip(config):
    """The camera stalled while the cup was up: nobody knows what happened, so it doesn't count."""
    assert sip_with_a_gap(Desk(config), config.protocols.hydration.sip_stall_sec + 0.5) == 0


def test_a_slow_detector_still_counts_the_sip(config):
    """The Pi's detector can take a few seconds a picture: a gap up to hydration.sip_stall_sec isn't a stall."""
    stall = config.protocols.hydration.sip_stall_sec
    assert stall > config.presence.disappear_after_sec          # its own number, not the presence one
    assert sip_with_a_gap(Desk(config), stall) == 1
    assert sip_with_a_gap(Desk(config), 5) == 1


def test_the_stall_limit_comes_from_protocols_yaml(config):
    assert sip_with_a_gap(Desk(config, sip_stall_sec=4), 5) == 0


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
    d = Desk(config)                            # sip_gone_min_sec is 2
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
