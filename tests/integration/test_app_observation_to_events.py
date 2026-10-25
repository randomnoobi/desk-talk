"""
From what the camera sees to what the app remembers, through the whole app built by bootstrap: each picture's
boxes (what the detector observed) go through the smoothing and the state machine (the object's state), and every
real change comes out as an object_state event in the event log and a row in the SQLite file. These tests check
that each step turns into the next, and that the three places agree.
"""

from __future__ import annotations

import pytest

from desktalk.domain import BBox, Detection
from tests.integration.app_kit import DeskRig

STEP = 1 / 3                          # the camera sends 3 pictures a second
PICTURE_WIDTH = 640                   # app_kit's desk pictures are 640 x 480


def bottle(conf: float = 0.9, left: float = 100) -> Detection:
    return Detection('bottle', conf, BBox(left, 300, left + 40, 400))


def show(rig: DeskRig, pictures: list[list[Detection]]) -> None:
    """One picture per entry, STEP seconds apart: the boxes the detector finds in it (no person)."""
    for boxes in pictures:
        rig.detector.detections = boxes
        rig.picture()
        rig.clock.advance(STEP)


def pictures_for(seconds: float) -> int:
    return round(seconds / STEP)


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
    assert (there.bbox.x1 - here.bbox.x1) / PICTURE_WIDTH > s.move_fraction

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
    sure = bottle(s.on_confidence + 0.02)
    unsure = bottle((s.on_confidence + s.off_confidence) / 2)      # under the "on" line, over the "off" line
    show(rig, [[sure], [unsure]] * pictures_for(s.appear_sec + s.gone_sec + 4))

    assert state(rig, 'bottle') == 'PRESENT'
    assert [e['change'] for e in logged(rig, 'bottle')] == ['appeared']
    assert [r.type for r in saved(rig, 'bottle')] == ['appeared']


def test_missed_pictures_and_a_short_gap_do_not_remove_it(tmp_path):
    rig = DeskRig(tmp_path)
    s = rig.config.object_states
    rig.watch(s.appear_sec + 2, bottle(), person=False)
    # The detector misses it in every third picture...
    show(rig, [[bottle()], [bottle()], []] * pictures_for(s.gone_sec + 3))
    # ...then loses it for a moment, shorter than gone_sec.
    show(rig, [[]] * pictures_for(s.gone_sec / 2))
    rig.watch(s.appear_sec + 2, bottle(), person=False)

    assert state(rig, 'bottle') == 'PRESENT'
    assert [e['change'] for e in logged(rig, 'bottle')] == ['appeared']
    assert [r.type for r in saved(rig, 'bottle')] == ['appeared']


def test_one_stray_box_somewhere_else_is_not_a_move(tmp_path):
    rig = DeskRig(tmp_path)
    s = rig.config.object_states
    rig.watch(s.appear_sec + 2, bottle(left=100), person=False)
    show(rig, [[bottle(left=500)]])                     # one picture puts the box across the desk
    rig.watch(s.appear_sec + 2, bottle(left=100), person=False)

    assert [e['change'] for e in logged(rig, 'bottle')] == ['appeared']
    assert [r.type for r in saved(rig, 'bottle')] == ['appeared']


def test_a_box_never_sure_enough_never_appears(tmp_path):
    rig = DeskRig(tmp_path)
    s = rig.config.object_states
    unsure = bottle((s.on_confidence + s.off_confidence) / 2)
    rig.watch(s.appear_sec + s.gone_sec + 4, unsure, person=False)

    assert state(rig, 'bottle') != 'PRESENT'
    assert logged(rig, 'bottle') == []
    assert saved(rig, 'bottle') == []
