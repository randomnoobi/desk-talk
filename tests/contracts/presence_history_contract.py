"""What every PresenceHistory must do (desktalk.ports.PresenceHistory)."""

from __future__ import annotations

import time

import pytest

from desktalk.domain import Alert, BBox, Detection, DetectionEvent, Light

TS = 1_760_000_000.0


def picture(ts: float, *labels: str, conf: float = 0.8) -> DetectionEvent:
    """An analysed picture with each label in a box centred on (100, 100)."""
    return DetectionEvent(ts=ts, frame_id=f'test-{ts}', source='pi', size=(640, 480), person=False,
                          light=Light(brightness=120.0),
                          detections=tuple(Detection(lab, conf, BBox(90, 90, 110, 110)) for lab in labels))


class PresenceHistoryContract:
    """Subclass and give a `store` fixture: an empty event log that also has write() and compact().
    Set merge_gap_sec to the store's own merge gap, and override day_of() when the store's clock
    isn't the computer's."""

    merge_gap_sec: float = 3.0

    def day_of(self, ts: float) -> str:
        """The day name the store files `ts` under."""
        return time.strftime('%Y-%m-%d', time.localtime(ts))

    def test_a_day_with_no_log_is_empty(self, store):
        assert store.presence(self.day_of(TS)) == []
        assert store.presence(self.day_of(TS), include_live=False) == []
        assert store.presence('1999-01-01') == []

    def test_pictures_not_compacted_yet_show_only_when_live_is_asked_for(self, store):
        for i in range(3):
            store.write(picture(TS + i, 'cup', conf=0.8))
        day = self.day_of(TS)
        [iv] = store.presence(day, include_live=True)
        assert iv['label'] == 'cup'
        assert iv['interval'] == [TS, TS + 2]
        assert iv['avg_conf'] == pytest.approx(0.8)
        assert store.presence(day, include_live=False) == []

    def test_after_compaction_they_show_without_live_too(self, store):
        for i in range(3):
            store.write(picture(TS + i, 'cup', conf=0.8))
        store.compact(TS + 60)
        day = self.day_of(TS)
        for live in (True, False):
            [iv] = store.presence(day, include_live=live)
            assert iv['label'] == 'cup'
            assert iv['interval'] == [TS, TS + 2]
            assert iv['avg_conf'] == pytest.approx(0.8)

    def test_intervals_carry_the_picture_size(self, store):
        for i in range(3):
            store.write(picture(TS + i, 'cup'))
        day = self.day_of(TS)
        assert [iv['size'] for iv in store.presence(day)] == [[640, 480]]
        store.compact(TS + 60)
        assert [iv['size'] for iv in store.presence(day, include_live=False)] == [[640, 480]]

    def test_compacted_and_live_pictures_join_into_one_interval(self, store):
        store.write(picture(TS, 'cup'))
        store.write(picture(TS + 1, 'cup'))
        store.compact(TS + 1.5)
        store.write(picture(TS + 2, 'cup'))
        day = self.day_of(TS)
        assert [iv['interval'] for iv in store.presence(day)] == [[TS, TS + 2]]
        assert [iv['interval'] for iv in store.presence(day, include_live=False)] == [[TS, TS + 1]]

    def test_two_labels_give_two_intervals_oldest_first(self, store):
        store.write(picture(TS + 10, 'phone'))
        store.write(picture(TS + 11, 'phone'))
        store.write(picture(TS, 'cup'))
        store.write(picture(TS + 1, 'cup'))
        out = store.presence(self.day_of(TS))
        assert [(iv['label'], iv['interval']) for iv in out] == [('cup', [TS, TS + 1]), ('phone', [TS + 10, TS + 11])]

    def test_a_gap_longer_than_the_merge_gap_starts_a_new_interval(self, store):
        later = TS + 1 + self.merge_gap_sec + 1
        for ts in (TS, TS + 1, later, later + 1):
            store.write(picture(ts, 'cup'))
        expected = [[TS, TS + 1], [later, later + 1]]
        day = self.day_of(TS)
        assert [iv['interval'] for iv in store.presence(day)] == expected
        store.compact(later + 60)
        assert [iv['interval'] for iv in store.presence(day, include_live=False)] == expected

    def test_a_gap_within_the_merge_gap_keeps_one_interval(self, store):
        store.write(picture(TS, 'cup'))
        store.write(picture(TS + self.merge_gap_sec, 'cup'))
        assert [iv['interval'] for iv in store.presence(self.day_of(TS))] == [[TS, TS + self.merge_gap_sec]]

    def test_other_events_make_no_interval(self, store):
        store.write(Alert(ts=TS, kind='proximity', agent='cup'))
        store.compact(TS + 60)
        day = self.day_of(TS)
        assert store.presence(day) == []
        assert store.presence(day, include_live=False) == []
