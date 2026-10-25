"""
From what the camera sees to what the app remembers, through the whole app built by bootstrap: each picture's
boxes (what the detector observed) go through the smoothing and the state machine (the object's state), and every
real change comes out as an object_state event in the event log and a row in the SQLite file. These tests check
that each step turns into the next, and that the three places agree.
"""

from __future__ import annotations

import math

import pytest

from desktalk.domain import BBox, Detection
from tests.integration.app_kit import DESK_PICTURE_SIZE, DeskRig, changed, desk_config


def bottle(conf: float = 0.9, left: float = 100) -> Detection:
    return Detection('bottle', conf, BBox(left, 300, left + 40, 400))


def logged(rig: DeskRig, label: str) -> list[dict]:
    """The label's object_state events in the event log, oldest first."""
    day = rig.app.event_log.days()[0]
    return [e for e in reversed(rig.app.event_log.events(day, {'object_state'}, 100)) if e['object'] == label]


def saved(rig: DeskRig, label: str) -> list:
    """The label's change rows in the SQLite file, oldest first."""
    return [row for row in rig.app.state_store.changes(0) if row.label == label]


def state(rig: DeskRig, label: str) -> str | None:
    found = rig.app.object_states.snapshot().objects.get(label)
    return found.state if found is not None else None


def test_a_bottle_put_down_moved_and_taken_away(tmp_path):
    rig = DeskRig(tmp_path)
    s = rig.config.object_states
    here, there = bottle(left=100), bottle(left=500)
    assert (there.bbox.x1 - here.bbox.x1) / DESK_PICTURE_SIZE[0] > s.move_fraction

    put_down = rig.clock.now()
    rig.watch(s.appear_sec + 2, here, person=False)
    assert state(rig, 'bottle') == 'PRESENT'
    rig.watch(s.appear_sec + 2, there, person=False)
    taken_away = rig.clock.now()
    rig.watch(s.gone_sec + 2, person=False)
    assert state(rig, 'bottle') == 'ABSENT'

    expected = [('appeared', 'UNKNOWN', 'PRESENT'), ('moved', 'PRESENT', 'PRESENT'), ('removed', 'PRESENT', 'ABSENT')]
    events, rows = logged(rig, 'bottle'), saved(rig, 'bottle')
    assert [(e['change'], e['prev_state'], e['new_state']) for e in events] == expected
    assert [(r.type, r.prev_state, r.new_state) for r in rows] == expected
    assert [e['ts'] for e in events] == pytest.approx([r.ts for r in rows])
    assert [c.change for c in rig.app.object_states.changes(0) if c.label == 'bottle'] == [c for c, _, _ in expected]

    # Only after the waits in the settings, never on the first picture.
    appeared, moved, removed = events
    assert appeared['ts'] - put_down > s.appear_sec
    assert removed['ts'] - taken_away > s.gone_sec
    # What the detector saw is carried along: its confidence, and the box at each place.
    assert appeared['conf_avg'] == pytest.approx(here.confidence)
    assert rows[0].conf_avg == pytest.approx(here.confidence)
    assert appeared['bbox'] == pytest.approx(here.bbox.to_list())
    assert moved['bbox'] == pytest.approx(there.bbox.to_list())
    assert list(rows[1].bbox) == pytest.approx(there.bbox.to_list())
    assert removed['dwell_sec'] == pytest.approx(removed['ts'] - appeared['ts'])


def test_a_wobbly_confidence_is_one_steady_appearance(tmp_path):
    rig = DeskRig(tmp_path)
    s = rig.config.object_states
    sure = bottle((s.on_confidence + 1) / 2)                       # over the "on" line
    unsure = bottle((s.on_confidence + s.off_confidence) / 2)      # under the "on" line, over the "off" line
    rig.show(s.appear_sec + s.gone_sec + 4, [[sure], [unsure]])

    assert state(rig, 'bottle') == 'PRESENT'
    assert [e['change'] for e in logged(rig, 'bottle')] == ['appeared']
    assert [r.type for r in saved(rig, 'bottle')] == ['appeared']


def test_the_vote_keeps_it_through_missed_pictures(tmp_path):
    config = desk_config(tmp_path)
    s = config.object_states
    step = 1 / config.camera.fps
    # Seen in just enough pictures to win every vote, then missed in the rest of the window, over and over.
    seen = math.floor(s.vote_share * s.window_pictures) + 1
    missed = s.window_pictures - seen
    pattern = [[bottle()]] * seen + [[]] * missed
    # gone_sec shorter than one picture: without the vote, the run of missed pictures would already remove it.
    quick_gone = step / 2
    assert (missed - 1) * step > quick_gone
    rig = DeskRig(tmp_path, config=changed(config, 'object_states', gone_sec=quick_gone))
    rig.show(s.appear_sec + 2, [[bottle()]])
    rig.show(s.appear_sec + s.gone_sec + 4, pattern)

    assert state(rig, 'bottle') == 'PRESENT'
    assert [e['change'] for e in logged(rig, 'bottle')] == ['appeared']
    assert [r.type for r in saved(rig, 'bottle')] == ['appeared']


def test_a_short_gap_does_not_remove_it(tmp_path):
    rig = DeskRig(tmp_path)
    s = rig.config.object_states
    rig.show(s.appear_sec + 2, [[bottle()]])
    rig.show(s.gone_sec / 2, [[]])                      # lost for a moment, shorter than gone_sec
    rig.show(s.appear_sec + 2, [[bottle()]])

    assert state(rig, 'bottle') == 'PRESENT'
    assert [e['change'] for e in logged(rig, 'bottle')] == ['appeared']
    assert [r.type for r in saved(rig, 'bottle')] == ['appeared']


def test_one_stray_box_somewhere_else_is_not_a_move(tmp_path):
    rig = DeskRig(tmp_path)
    s = rig.config.object_states
    rig.watch(s.appear_sec + 2, bottle(left=100), person=False)
    rig.show(1 / rig.config.camera.fps, [[bottle(left=500)]])     # one picture puts the box across the desk
    rig.watch(s.appear_sec + 2, bottle(left=100), person=False)

    assert [e['change'] for e in logged(rig, 'bottle')] == ['appeared']
    assert [r.type for r in saved(rig, 'bottle')] == ['appeared']


def test_a_box_never_sure_enough_never_appears(tmp_path):
    rig = DeskRig(tmp_path)
    s = rig.config.object_states
    unsure = bottle((s.on_confidence + s.off_confidence) / 2)
    rig.watch(s.appear_sec + s.gone_sec + 4, unsure, person=False)

    assert state(rig, 'bottle') == 'ABSENT'
    assert logged(rig, 'bottle') == []
    assert saved(rig, 'bottle') == []
