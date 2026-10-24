"""One day's habit numbers and how they are kept (desktalk/core/habits/habit_days.py).

The two files in tests/fixtures/ were written by the habit tracker before HabitDay existed: a busy day and a
fresh one. HabitDay must read them and write them back exactly as they were, so old day files keep working.
"""

import json
import logging
from pathlib import Path

import pytest

from desktalk.core.habits.habit_days import HabitDay, HabitDays
from tests.fakes import FakeClock, InMemoryKeyValueStore

FIXTURES = Path(__file__).resolve().parents[1] / 'fixtures'
BUSY_DAY = json.loads((FIXTURES / 'habit_day_before_cleanup.json').read_text())
FRESH_DAY = json.loads((FIXTURES / 'habit_day_fresh_before_cleanup.json').read_text())
START = 1_781_514_000.0           # 15 June 2026, 09:00 UTC (FakeClock is UTC)
DAY = '2026-06-15'


def test_habit_day_reads_and_writes_todays_file_format():
    day = HabitDay.from_saved(DAY, BUSY_DAY)
    assert day is not None and day.water_ml == 320 and day.objects_seen == ['Cup']
    assert json.dumps(day.to_dict()) == json.dumps(BUSY_DAY)          # same keys, order, values and types


def test_a_fresh_day_is_laid_out_like_before():
    assert json.dumps(HabitDay(day=DAY).to_dict()) == json.dumps(FRESH_DAY)


def test_old_and_unknown_fields_are_handled():
    old = {'day': DAY, 'sips': 3, 'something_old': 5}
    day = HabitDay.from_saved(DAY, old)
    assert day is not None and day.sips == 3 and 'something_old' not in day.to_dict()
    assert HabitDay.from_saved(DAY, {'day': '2026-06-14', 'sips': 3}) is None
    assert HabitDay.from_saved(DAY, 'broken') is None


def test_to_dict_is_a_copy():
    day = HabitDay(day=DAY)
    day.to_dict()['food'].append({'label': 'apple'})
    assert day.food == []


def test_counters_can_be_bumped_by_name_and_others_are_refused():
    day = HabitDay(day=DAY)
    day.bump('danger_count')
    day.bump('lost_alerts', 2)
    assert (day.danger_count, day.lost_alerts) == (1, 2)
    with pytest.raises(ValueError, match='not a daily counter'):
        day.bump('water_ml')


class Rig:
    def __init__(self, store=None):
        self.clock = FakeClock(start=START)
        self.store = store if store is not None else InMemoryKeyValueStore()
        self.days = HabitDays(self.store, self.clock)


def test_starts_with_the_saved_day():
    r = Rig(InMemoryKeyValueStore({f'habits/{DAY}': BUSY_DAY}))
    assert r.days.today.water_ml == 320


def test_a_broken_saved_day_is_logged_and_the_day_starts_fresh(caplog):
    with caplog.at_level(logging.WARNING):
        r = Rig(InMemoryKeyValueStore({f'habits/{DAY}': 'broken'}))
    assert r.days.today == HabitDay(day=DAY) and 'starting the day fresh' in caplog.text


def test_saves_only_after_a_change_unless_forced():
    r = Rig()
    r.days.save()
    assert r.store.saves == 0
    r.days.save(force=True)
    assert r.store.saves == 1
    r.days.today.sips += 1
    r.days.changed()
    r.days.save()
    r.days.save()
    assert r.store.saves == 2 and r.store.data[f'habits/{DAY}']['sips'] == 1


def test_save_is_due_after_the_wait():
    r = Rig()
    r.days.save(force=True)
    assert not r.days.save_due(START + 29, every_sec=30)
    assert r.days.save_due(START + 30, every_sec=30)


def test_the_day_rolls_over_on_the_clocks_local_day():
    r = Rig()
    r.days.today.sips = 4
    r.days.changed()
    assert not r.days.roll(START + 3600)
    assert r.days.roll(START + 86400)
    assert r.days.today.day == '2026-06-16' and r.days.today.sips == 0
    assert r.store.data[f'habits/{DAY}']['sips'] == 4


def test_the_day_follows_the_clocks_time_zone():
    clock = FakeClock(start=START, utc_offset_hours=-10)            # 23:00 the day before
    days = HabitDays(InMemoryKeyValueStore(), clock)
    assert days.today.day == '2026-06-14'
    assert days.roll(START + 2 * 3600)                               # 01:00 local: the 15th


class FlakyStore(InMemoryKeyValueStore):
    def __init__(self):
        super().__init__()
        self.fail = True

    def save(self, key, value):
        if self.fail:
            raise OSError('disk full')
        super().save(key, value)


def test_a_day_that_could_not_be_saved_is_kept_until_it_can(caplog):
    store = FlakyStore()
    r = Rig(store)
    r.days.today.water_ml = 240
    r.days.changed()
    with caplog.at_level(logging.WARNING):
        r.days.roll(START + 86400)
    assert 'disk full' in caplog.text and store.data == {}
    store.fail = False
    r.days.save()
    assert store.data[f'habits/{DAY}']['water_ml'] == 240
    assert store.data['habits/2026-06-16']['day'] == '2026-06-16'


def test_going_back_to_an_unsaved_day_picks_it_up_again():
    store = FlakyStore()
    r = Rig(store)
    r.days.today.water_ml = 240
    r.days.changed()
    r.days.roll(START + 86400)
    r.days.roll(START)                                               # the clock was put back
    assert r.days.today.water_ml == 240
