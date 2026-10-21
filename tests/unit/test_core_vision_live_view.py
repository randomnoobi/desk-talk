"""The live view ("Boxes" in the app): detection boxes, the chat's agents highlighted, and the desk overlays."""

import logging
from types import SimpleNamespace

import numpy as np

from desktalk.core.vision.analyzer import DeskAnalyzer
from desktalk.core.vision.findings import Thing
from desktalk.core.vision.live_view import DeskPainter
from desktalk.core.vision.rules import SafetyRules
from desktalk.desk_state import DeskState
from desktalk.domain import AgentProfile, BBox, Detection
from desktalk.ports import LiveViewPainter
from tests.fakes import FakeProfiles, InMemoryKeyValueStore
from tests.unit.test_core_vision_kit import app_config, desk_object

GREEN, GREY, RED = (60, 200, 90), (150, 150, 150), (0, 0, 255)
LAPTOP, CUP_NEAR = (200, 200, 400, 350), (410, 250, 450, 320)
PROFILES = FakeProfiles([AgentProfile('computer', 'Computer', labels=['laptop'], fragile=True),
                         AgentProfile('cup', 'Cup', labels=['cup'])])


def blank():
    return np.full((480, 640, 3), 100, np.uint8)


def rig(agents=None):
    """A painter, with the analyzer having looked at `agents` (agent → (label, box)) once."""
    desk = DeskState()
    desk.objects = {a: desk_object(label, box) for a, (label, box) in (agents or {}).items()}
    analyzer = DeskAnalyzer(SafetyRules(InMemoryKeyValueStore(), app_config().safety))
    if agents:
        analyzer.analyze({a: Thing.from_desk(a, o) for a, o in desk.objects.items()}, (640, 480), 1000)
    return DeskPainter(desk, PROFILES, analyzer), desk


def det(label, box):
    return Detection(label, 0.9, BBox(*box))


def pixel(picture, x, y):
    return tuple(int(v) for v in picture[y, x])


def test_it_is_a_live_view_painter():
    assert isinstance(rig()[0], LiveViewPainter)


def test_paint_leaves_the_picture_it_was_given_alone():
    picture = blank()
    painted = rig()[0].paint(picture, [det('cup', (100, 100, 200, 200))])
    assert (picture == 100).all() and not (painted == 100).all()


def test_with_nobody_in_the_chat_every_box_is_green():
    painted = rig()[0].paint(blank(), [det('cup', (100, 100, 200, 200)), det('pen', (300, 300, 400, 400))])
    assert pixel(painted, 100, 150) == GREEN and pixel(painted, 300, 350) == GREEN


def test_agents_in_the_chat_are_green_and_the_rest_grey():
    painter, _ = rig({'cup': ('cup', (100, 100, 200, 200))})
    painted = painter.paint(blank(), [det('cup', (100, 100, 200, 200)), det('pen', (300, 300, 400, 400))])
    assert pixel(painted, 100, 150) == GREEN and pixel(painted, 300, 350) == GREY


def test_a_danger_zone_with_something_risky_in_it_is_drawn_red():
    painter, _ = rig({'computer': ('laptop', LAPTOP), 'cup': ('cup', CUP_NEAR)})
    painted = painter.paint(blank(), [det('laptop', LAPTOP), det('cup', CUP_NEAR)])
    assert pixel(painted, 120, 380) == RED          # the left side of the laptop's zone (80 px around it)


def test_the_risk_badge_is_drawn_in_the_corner():
    painter, _ = rig({'computer': ('laptop', LAPTOP), 'cup': ('cup', CUP_NEAR)})
    painted = painter.paint(blank(), [])
    assert pixel(painted, 8, 30) == (0, 220, 220)    # one zone intrusion = risk level 1: a yellow border
    assert pixel(painted, 150, 47) == (0, 0, 0)      # the badge is black inside


def test_no_overlays_before_the_desk_was_analysed():
    desk = DeskState()
    desk.objects = {'computer': desk_object('laptop', LAPTOP)}
    analyzer = DeskAnalyzer(SafetyRules(InMemoryKeyValueStore(), app_config().safety))
    painted = DeskPainter(desk, PROFILES, analyzer).paint(blank(), [])
    assert (painted == 100).all()


def test_no_overlays_without_agents_in_the_chat():
    painter, desk = rig({'computer': ('laptop', LAPTOP), 'cup': ('cup', CUP_NEAR)})
    desk.objects = {}
    painted = painter.paint(blank(), [])
    assert (painted == 100).all()


def test_boxes_still_show_when_the_overlays_fail(caplog):
    desk = DeskState()
    desk.objects = {'computer': desk_object('laptop', LAPTOP)}
    broken = SimpleNamespace(latest={'no': 'relationships'}, rules=None, history=lambda: [])
    with caplog.at_level(logging.ERROR):
        painted = DeskPainter(desk, PROFILES, broken).paint(blank(), [det('laptop', LAPTOP)])
    assert pixel(painted, 200, 300) == GREEN
    assert 'overlays' in caplog.text


def test_overlays_that_keep_failing_the_same_way_are_logged_once(caplog):
    """The live view is painted about 3 times a second: the same full error each time would flood the log."""
    desk = DeskState()
    desk.objects = {'computer': desk_object('laptop', LAPTOP)}
    broken = SimpleNamespace(latest={'no': 'relationships'}, rules=None, history=lambda: [])
    painter = DeskPainter(desk, PROFILES, broken)
    with caplog.at_level(logging.ERROR):
        for _ in range(5):
            painter.paint(blank(), [])
        broken.history = lambda: 1 / 0                          # a different problem: logged again
        for _ in range(5):
            painter.paint(blank(), [])
    assert len([r for r in caplog.records if 'overlays' in r.getMessage()]) == 2
