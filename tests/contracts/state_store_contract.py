"""What every ObjectStateStore must do (desktalk.ports.ObjectStateStore)."""

from __future__ import annotations

from desktalk.domain.object_states import Phase, StateRecord

ZONE = '+08:00'


def a_record(ts, label='bottle', type='appeared', prev_state=Phase.ABSENT, new_state=Phase.PRESENT, **more):
    return StateRecord(ts=ts, timezone=ZONE, device_id='pi-1', type=type, label=label,
                       prev_state=prev_state, new_state=new_state, **more)


class StateStoreContract:
    """Subclass and give a `store` fixture (empty)."""

    def test_records_come_back_as_written(self, store):
        full = a_record(100.0, type='removed', prev_state=Phase.PRESENT, new_state=Phase.ABSENT,
                        conf_avg=0.83, bbox=(10.0, 20.0, 50.0, 100.0), dwell_sec=42.5)
        bare = a_record(101.0, label='cup')
        store.append([full, bare])
        assert store.changes(0.0) == [full, bare]
        assert store.changes(0.0)[1].bbox is None

    def test_changes_leave_out_snapshots_and_are_oldest_first(self, store):
        later = a_record(120.0, type='removed', prev_state=Phase.PRESENT, new_state=Phase.ABSENT)
        earlier = a_record(100.0)
        moved = a_record(110.0, type='moved', prev_state=Phase.PRESENT, new_state=Phase.PRESENT)
        snapshot = a_record(115.0, type='snapshot', prev_state=Phase.PRESENT, new_state=Phase.PRESENT)
        store.append([later, earlier, moved, snapshot])
        assert store.changes(0.0) == [earlier, moved, later]

    def test_changes_with_the_same_time_keep_the_order_they_were_added(self, store):
        first, second = a_record(100.0, label='bottle'), a_record(100.0, label='cup')
        store.append([first])
        store.append([second])
        assert store.changes(0.0) == [first, second]

    def test_changes_since_is_strictly_after(self, store):
        at_since, after = a_record(100.0), a_record(100.5, label='cup')
        store.append([at_since, after])
        assert store.changes(100.0) == [after]

    def test_latest_states_skip_moved_rows(self, store):
        appeared = a_record(100.0, dwell_sec=5.0)
        moved = a_record(110.0, type='moved', prev_state=Phase.PRESENT, new_state=Phase.PRESENT, dwell_sec=10.0)
        store.append([appeared, moved])
        assert store.latest_states(0.0) == {'bottle': appeared}

    def test_latest_states_take_the_newest_row_per_object(self, store):
        appeared = a_record(100.0)
        snapshot = a_record(160.0, type='snapshot', prev_state=Phase.PRESENT, new_state=Phase.PRESENT)
        removed = a_record(200.0, label='cup', type='removed', prev_state=Phase.PRESENT, new_state=Phase.ABSENT)
        store.append([appeared, snapshot, removed])
        assert store.latest_states(0.0) == {'bottle': snapshot, 'cup': removed}

    def test_latest_states_include_a_row_at_exactly_since(self, store):
        row = a_record(100.0)
        store.append([row])
        assert store.latest_states(100.0) == {'bottle': row}

    def test_latest_states_leave_out_old_rows(self, store):
        store.append([a_record(99.0)])
        assert store.latest_states(100.0) == {}

    def test_closing_is_safe(self, store):
        store.close()
