"""Seen / not seen → steady present / gone (desktalk/core/agents/presence.py)."""

from desktalk.core.agents.presence import PresenceTracker


def test_presence_needs_time_to_appear_and_to_go():
    p = PresenceTracker(appear_sec=3, gone_sec=10)
    t = 0.0
    assert p.update({'cup'}, t) == ([], [])
    assert p.update({'cup'}, t + 2.9) == ([], [])
    assert p.update({'cup'}, t + 3.0) == (['cup'], [])
    assert p.is_present('cup')
    assert p.update(set(), t + 12.9) == ([], [])          # missing 9.9 s: still here
    assert p.update(set(), t + 13.0) == ([], ['cup'])
    assert not p.is_present('cup')


def test_presence_flicker_restarts_the_count():
    p = PresenceTracker(appear_sec=3, gone_sec=1)
    p.update({'cup'}, 0)
    p.update({'cup'}, 1)
    p.update({'cup'}, 5)                                   # 4 s gap > gone_sec: a new sighting
    assert not p.is_present('cup')
    for t in (5.5, 6, 6.5, 7, 7.5):
        assert p.update({'cup'}, t) == ([], [])
    assert p.update({'cup'}, 8) == (['cup'], [])           # 3 s after the new sighting began


def test_last_seen_and_absent_for():
    p = PresenceTracker(appear_sec=0, gone_sec=10)
    assert p.absent_for('cup', 5) is None and p.last_seen('cup') is None
    p.update({'cup'}, 2)
    assert p.last_seen('cup') == 2 and p.absent_for('cup', 7) == 5


def test_dropped_key_appears_again_on_the_next_sighting():
    p = PresenceTracker(appear_sec=3, gone_sec=10)
    p.update({'cup'}, 0)
    assert p.update({'cup'}, 3) == (['cup'], [])
    p.drop('cup')
    assert not p.is_present('cup')
    assert p.update({'cup'}, 4) == (['cup'], [])           # still seen without a gap: back at once
