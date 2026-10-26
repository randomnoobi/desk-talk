"""The live view ("Boxes" in the app): detection boxes, the chat's agents highlighted, and the desk overlays."""

import logging
from types import SimpleNamespace
from typing import Any

import numpy as np

from desktalk.adapters.picture.painter import DeskPainter
from desktalk.core.vision.analyzer import DeskAnalyzer
from desktalk.core.vision.findings import Thing
from desktalk.core.vision.rules import SafetyRules
from desktalk.domain import AgentProfile, BBox, Detection
from desktalk.ports import LiveViewPainter
from tests.fakes import FakeDeskScene, FakeProfiles, InMemoryKeyValueStore
from tests.unit.test_core_vision_kit import app_config, seen_object

DRAWING = app_config().drawing
CHECKS = app_config().prompts.desk_checks
RULE_WORDS = app_config().prompts.desk_rules

GREEN, GREY, RED = (60, 200, 90), (150, 150, 150), (0, 0, 255)
LAPTOP, CUP_NEAR = (200, 200, 400, 350), (410, 250, 450, 320)
PROFILES = FakeProfiles([AgentProfile('computer', 'Computer', labels=['laptop'], fragile=True),
                         AgentProfile('cup', 'Cup', labels=['cup'])])


def blank():
    return np.full((480, 640, 3), 100, np.uint8)


def on_desk(agents, chat=None):
    """A desk scene with `agents` (agent → (label, box)) on the desk; the ones in `chat` (all of them when it's
    None) are in the chat too."""
    desk = FakeDeskScene()
    seen = {a: seen_object(label, box) for a, (label, box) in agents.items()}
    desk.set_on_desk(seen, 1000)
    desk.set_members({a: o for a, o in seen.items() if chat is None or a in chat}, 1000)
    return desk


def things_on(desk):
    return {a: Thing.from_seen(a, o) for a, o in desk.on_desk().items()}


def rig(agents=None, drawing=DRAWING, chat=None):
    """A painter, with the analyzer having looked at `agents` (agent → (label, box)) once."""
    desk = on_desk(agents or {}, chat)
    analyzer = DeskAnalyzer(SafetyRules(InMemoryKeyValueStore(), app_config().safety, words=RULE_WORDS), words=CHECKS)
    if agents:
        analyzer.analyze(things_on(desk), (640, 480), 1000)
    return DeskPainter(desk, PROFILES, analyzer, drawing), desk


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


def test_paints_on_desk_objects():
    """Nothing is in the chat, but the warnings watch the laptop and the cup: the live view shows what they see."""
    painter, _ = rig({'computer': ('laptop', LAPTOP), 'cup': ('cup', CUP_NEAR)}, chat=())
    painted = painter.paint(blank(), [det('laptop', LAPTOP), det('cup', CUP_NEAR)])
    assert pixel(painted, 120, 380) == RED          # the laptop's zone, with the cup in it
    assert pixel(painted, 200, 300) == GREEN        # nobody in the chat: every box is green


def test_the_chat_decides_the_box_colours_not_the_desk_list():
    painter, _ = rig({'computer': ('laptop', LAPTOP), 'cup': ('cup', CUP_NEAR)}, chat={'cup'})
    painted = painter.paint(blank(), [det('laptop', LAPTOP), det('cup', CUP_NEAR)])
    assert pixel(painted, 410, 300) == GREEN and pixel(painted, 200, 300) == GREY
    assert pixel(painted, 120, 380) == RED


def test_only_where_the_risky_thing_stands_is_shaded():
    painter, _ = rig({'computer': ('laptop', LAPTOP), 'cup': ('cup', CUP_NEAR)})
    painted = painter.paint(blank(), [])
    assert pixel(painted, 440, 313) != (100, 100, 100)     # the bottom strip of the cup's box
    assert pixel(painted, 440, 260) == (100, 100, 100)     # higher up the cup


def test_the_risk_badge_is_drawn_in_the_corner():
    painter, _ = rig({'computer': ('laptop', LAPTOP), 'cup': ('cup', CUP_NEAR)})
    painted = painter.paint(blank(), [])
    assert pixel(painted, 8, 30) == (0, 220, 220)    # one zone intrusion = risk level 1: a yellow border
    assert pixel(painted, 150, 47) == (0, 0, 0)      # the badge is black inside


def test_no_overlays_before_the_desk_was_analysed():
    desk = on_desk({'computer': ('laptop', LAPTOP)})
    analyzer = DeskAnalyzer(SafetyRules(InMemoryKeyValueStore(), app_config().safety, words=RULE_WORDS), words=CHECKS)
    painted = DeskPainter(desk, PROFILES, analyzer, DRAWING).paint(blank(), [])
    assert (painted == 100).all()


def test_no_overlays_with_nothing_on_the_desk():
    painter, desk = rig({'computer': ('laptop', LAPTOP), 'cup': ('cup', CUP_NEAR)})
    desk.set_on_desk({}, 1001)                       # still in the chat, but the warnings see nothing now
    painted = painter.paint(blank(), [])
    assert (painted == 100).all()


def test_boxes_still_show_when_the_overlays_fail(caplog):
    desk = on_desk({'computer': ('laptop', LAPTOP)})
    broken: Any = SimpleNamespace(latest={'no': 'relationships'}, rules=None, history=lambda: [])
    with caplog.at_level(logging.ERROR):
        painted = DeskPainter(desk, PROFILES, broken, DRAWING).paint(blank(), [det('laptop', LAPTOP)])
    assert pixel(painted, 200, 300) == GREEN
    assert 'overlays' in caplog.text


def test_overlays_that_keep_failing_the_same_way_are_logged_once(caplog):
    """The live view is painted about 3 times a second: the same full error each time would flood the log."""
    desk = on_desk({'computer': ('laptop', LAPTOP)})
    broken: Any = SimpleNamespace(latest={'no': 'relationships'}, rules=None, history=lambda: [])
    painter = DeskPainter(desk, PROFILES, broken, DRAWING)
    with caplog.at_level(logging.ERROR):
        for _ in range(5):
            painter.paint(blank(), [])
        broken.history = lambda: 1 / 0                          # a different problem: logged again
        for _ in range(5):
            painter.paint(blank(), [])
    assert len([r for r in caplog.records if 'overlays' in r.getMessage()]) == 2


# ── each desk overlay, in the colours the old live view used ──
WARN, ZONE_OK, CHAIN, MOVING, WHITE = (0, 190, 255), (200, 160, 0), (220, 80, 255), (255, 200, 0), (255, 255, 255)
FRONT_EDGE = {'desk_outline': [[0, 0], [1, 0], [1, 0.8], [0, 0.8]]}       # the front edge is at y = 384
MORE_PROFILES = FakeProfiles([*PROFILES.all(), AgentProfile('mouse', 'Mouse', labels=['mouse']),
                              AgentProfile('snack', 'Snack', labels=['apple']),
                              AgentProfile('drink', 'Drink', labels=['bottle'])])


def painted(*pictures, saved=None):
    """The live view after the analyzer looked at each picture (agent → (label, box)), one second apart."""
    desk = FakeDeskScene()
    analyzer = DeskAnalyzer(SafetyRules(InMemoryKeyValueStore({'safety_rules': saved} if saved else {}),
                                        app_config().safety, words=RULE_WORDS), words=CHECKS)
    for k, agents in enumerate(pictures):
        desk.set_on_desk({a: seen_object(label, box) for a, (label, box) in agents.items()}, 1000 + k)
        analyzer.analyze(things_on(desk), (640, 480), 1000 + k)
    return DeskPainter(desk, MORE_PROFILES, analyzer, DRAWING).paint(blank(), [])


def near(color, want, most=12):
    """Lines 1 pixel wide are smoothed, so they come out a little lighter than their colour."""
    return all(abs(a - b) <= most for a, b in zip(color, want, strict=True))


def test_the_desk_edges_you_marked_are_drawn_but_not_the_picture_border():
    picture = painted({'snack': ('apple', (300, 100, 340, 140))}, saved=FRONT_EDGE)
    assert pixel(picture, 50, 384) == WARN
    assert pixel(picture, 1, 200) == (100, 100, 100)          # the outline's left side is the picture's border


def test_something_close_to_the_edge_gets_a_red_box():
    picture = painted({'cup': ('cup', (100, 330, 140, 370))}, saved=FRONT_EDGE)
    assert pixel(picture, 100, 345) == RED


def test_every_danger_zone_is_drawn_dashed_even_with_nothing_in_it():
    picture = painted({'computer': ('laptop', (200, 200, 400, 350))})
    # The zone is around the laptop's base (the bottom half, y 275-350), 80 px wider: 120, 195, 480, 430
    assert near(pixel(picture, 480, 283), ZONE_OK)              # the right side, 80 px out from the laptop
    assert pixel(picture, 480, 291) == (100, 100, 100)          # a gap between dashes
    assert pixel(picture, 130, 200) == (60, 60, 60)             # the zone's name, on a dark grey label
    assert pixel(picture, 480, 150) == (100, 100, 100)          # nothing up beside the screen


def test_a_thing_in_between_a_danger_pair_gets_a_magenta_ring():
    picture = painted({'computer': ('laptop', (60, 250, 340, 300)), 'cup': ('cup', (400, 270, 430, 300)),
                       'mouse': ('mouse', (350, 270, 370, 290))})
    assert pixel(picture, 376, 280) == pixel(picture, 360, 264) == pixel(picture, 360, 296) == CHAIN


def test_a_crowd_is_shaded():
    picture = painted({'cup': ('cup', (300, 300, 330, 340)), 'snack': ('apple', (340, 300, 370, 330)),
                       'drink': ('bottle', (380, 300, 400, 340))})
    assert pixel(picture, 230, 200) == (90, 109, 116)           # a tenth of WARN over the grey picture
    assert pixel(picture, 600, 200) == (100, 100, 100)          # outside the crowd


def test_things_moving_closer_get_a_cyan_arrow():
    picture = painted(*[{'cup': ('cup', (5 * k, 200, 5 * k + 20, 220)), 'snack': ('apple', (300, 200, 320, 220))}
                        for k in range(10)])
    assert pixel(picture, 160, 210) == pixel(picture, 260, 210) == MOVING


def test_going_back_into_a_zone_again_and_again_gets_a_red_dot_with_the_count():
    near_cup, far_cup = ('cup', CUP_NEAR), ('cup', (560, 380, 600, 450))
    picture = painted(*[{'computer': ('laptop', LAPTOP), 'cup': near_cup if k % 2 == 0 else far_cup}
                        for k in range(5)])
    assert pixel(picture, 453, 260) == pixel(picture, 475, 260) == RED      # the dot, right of the cup
    assert pixel(picture, 462, 260) == WHITE                                # the count, 3


def test_things_that_move_leave_a_trail():
    picture = painted(*[{'cup': ('cup', (100 + 20 * k, 100, 140 + 20 * k, 140))} for k in range(4)])
    assert [pixel(picture, x, 120) for x in (120, 140, 160)] == [(80, 80, 80), (110, 110, 110), (140, 140, 140)]


# ── how it is drawn comes from settings → drawing ──
def drawing_with(**changes):
    return DRAWING.model_copy(update=changes)


def edge_width(line_px):
    """How many pixels wide a box's left edge comes out (counted across it, halfway down)."""
    painter, _ = rig(drawing=drawing_with(line_px=line_px))
    picture = painter.paint(blank(), [det('cup', (100, 100, 200, 200))])
    return [pixel(picture, x, 150) for x in range(90, 111)].count(GREEN)


def test_painter_uses_drawing_settings():
    # OpenCV draws a line of thickness n a little wider than n pixels (2 → 3, 5 → 7), so: at least 5, and wider
    assert edge_width(5) >= 5 and edge_width(5) > edge_width(2) > edge_width(1) == 1


def test_box_colors_come_from_the_settings():
    pink = (200, 100, 250)
    colors = DRAWING.colors.model_copy(update={'box_on': pink})
    painter, _ = rig(drawing=drawing_with(colors=colors))
    assert pixel(painter.paint(blank(), [det('cup', (100, 100, 200, 200))]), 100, 150) == pink


def test_the_risk_badge_box_comes_from_the_settings():
    painter, _ = rig({'computer': ('laptop', LAPTOP), 'cup': ('cup', CUP_NEAR)},
                     drawing=drawing_with(badge=(20, 20, 200, 70)))
    picture = painter.paint(blank(), [])
    assert pixel(picture, 8, 30) == (100, 100, 100) and pixel(picture, 20, 40) == (0, 220, 220)
