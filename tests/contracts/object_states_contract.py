"""What every ObjectStatesView must do (desktalk/ports.py). The fake FakeObjectStates and any real
version run these tests."""

from __future__ import annotations

from desktalk.domain import DeskSnapshot, ObjectSnapshot, StateChange

STATES = ('PRESENT', 'ABSENT', 'UNKNOWN')


class ObjectStatesContract:
    """Subclass this and give three fixtures:

    - `states`: an ObjectStatesView with nothing in it yet.
    - `add_change`: a function `(states, ts, label, kind)` that makes the view remember one change.
    - `put_object`: a function `(states, label, state, since)` that makes the view show that object.
    """

    def test_changes_since_is_strictly_after(self, states, add_change):
        add_change(states, 100.0, 'cup', 'appeared')
        add_change(states, 110.0, 'cup', 'moved')
        add_change(states, 120.0, 'cup', 'removed')
        assert [c.ts for c in states.changes(110.0)] == [120.0]
        assert states.changes(120.0) == []

    def test_changes_are_oldest_first(self, states, add_change):
        add_change(states, 100.0, 'cup', 'appeared')
        add_change(states, 105.0, 'phone', 'appeared')
        add_change(states, 110.0, 'cup', 'removed')
        got = states.changes(0.0)
        assert got == sorted(got, key=lambda c: c.ts)
        assert [(c.ts, c.label, c.change) for c in got] == [
            (100.0, 'cup', 'appeared'), (105.0, 'phone', 'appeared'), (110.0, 'cup', 'removed')]
        assert all(isinstance(c, StateChange) for c in got)

    def test_snapshot_states_are_the_three_strings(self, states, put_object):
        for label, state in (('cup', 'PRESENT'), ('phone', 'ABSENT'), ('book', 'UNKNOWN')):
            put_object(states, label, state, 50.0)
        snap = states.snapshot()
        assert isinstance(snap, DeskSnapshot)
        assert set(snap.objects) == {'cup', 'phone', 'book'}
        for label, obj in snap.objects.items():
            assert isinstance(obj, ObjectSnapshot)
            assert obj.label == label
            assert obj.state in STATES
