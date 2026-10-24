"""The habit tracker: sips, breaks, phone-free time, food and light from detection events
(desktalk/core/habits/tracker.py).

The day is 15 June 2026 (local time); the clock starts at 09:00. Wake 07:00, bed 22:30, sunset 18:30.
"""

import logging
import shutil
import time
from pathlib import Path

import pytest

from desktalk.config import load_config
from desktalk.core.agents.profiles import ProfileBook
from desktalk.core.habits.tracker import HabitTracker
from desktalk.desk_state import DeskState
from desktalk.domain import Alert, BBox, Detection, DetectionEvent, HabitEvent, Light
from desktalk.ports import HabitsView
from desktalk.util import at_time_today
from tests.fakes import FakeClock, FakePreferences, InMemoryKeyValueStore, SyncEventBus

ROOT = Path(__file__).resolve().parents[2]
MIN = 60
DAY = time.mktime((2026, 6, 15, 12, 0, 0, 0, 0, -1))
DAY_KEY = 'habits/2026-06-15'
DAY_FILE_KEYS = {'day', 'sips', 'water_ml', 'last_sip_ts', 'drinks', 'breaks', 'last_break_ts', 'break_log',
                 'longest_sit_sec', 'desk_sec', 'phone_free_sec', 'phone_on_desk_sec', 'best_phone_free_sec', 'food',
                 'sunlight_ts', 'sunlight_minutes', 'afternoon_light_ts', 'hourly_activity', 'objects_seen',
                 'danger_count', 'lost_alerts', 'nudges_sent', 'summary_sent'}


def at(hhmm, days_later=0):
    return at_time_today(hhmm, DAY) + days_later * 86400


@pytest.fixture(scope='module')
def config(tmp_path_factory):
    root = tmp_path_factory.mktemp('project')
    (root / 'config').mkdir()
    for name in ('settings.yaml', 'agents.yaml', 'protocols.yaml', 'prompts.yaml'):
        shutil.copy(ROOT / 'config' / name, root / 'config' / name)
    return load_config(root, environ={})


class Rig:
    """A habit tracker on a synchronous bus, fed made-up pictures (3 a second)."""

    def __init__(self, config, store=None, phone_counts=True, protocols=None, prefs=None, **tracker_settings):
        self.clock = FakeClock(start=at('09:00'))
        self.bus = SyncEventBus()
        self.store = store if store is not None else InMemoryKeyValueStore()
        self.profiles = ProfileBook(config.agents_file, InMemoryKeyValueStore(), personality_max_chars=400)
        self.prefs = prefs or FakePreferences({'wake_time': '07:00', 'bed_time': '22:30'})
        self.desk = DeskState()
        self.tracker = HabitTracker(
            self.clock, self.bus, self.store, self.profiles, self.prefs, self.desk,
            protocols or config.protocols, config.habit_tracker.model_copy(update=tracker_settings),
            config.presence, config.agents, phone_counts=phone_counts)

    def picture(self, *labels, person=True, light=None, conf=0.9):
        dets = tuple(Detection(label, conf, BBox(0, 0, 10, 10)) for label in labels)
        self.bus.publish(DetectionEvent(ts=self.clock.now(), frame_id='f', source='pi', size=(640, 480),
                                        detections=dets, person=person, light=light or Light(brightness=120)))

    def run(self, seconds, *labels, person=True, light=None, conf=0.9):
        for _ in range(int(seconds * 3)):
            self.picture(*labels, person=person, light=light, conf=conf)
            self.clock.advance(1 / 3)

    def said(self, action):
        return [e for e in self.bus.of_type(HabitEvent) if e.action == action]

    def today(self):
        return self.tracker.today(self.clock.now())

    def state(self, open_todos=()):
        return self.tracker.rule_state(self.clock.now(), open_todos)


def changed(section, **values):
    return section.model_copy(update=values)


def test_it_is_a_habits_view(config):
    assert isinstance(Rig(config).tracker, HabitsView)


# ── hydration ─────────────────────────────────────────────────────────
def test_sip_when_the_cup_leaves_and_comes_back(config):
    r = Rig(config)
    r.run(10, 'cup')
    r.run(8)                       # cup picked up for 8 s
    r.run(5, 'cup')
    sips = r.said('sip')
    assert len(sips) == 1 and sips[0].habit == 'hydration' and sips[0].say.startswith('Sip detected!')
    assert {k: sips[0].to_dict()[k] for k in ('ml', 'total_ml', 'how')} == {'ml': 80, 'total_ml': 80, 'how': 'camera'}
    assert r.today()['hydration']['sips'] == 1 and r.today()['hydration']['water_ml'] == 80
    # gone 3 minutes (refill in the kitchen?): not a sip
    r.run(180)
    r.run(5, 'cup')
    assert r.today()['hydration']['sips'] == 1


def test_a_short_flicker_is_not_a_sip(config):
    r = Rig(config)
    r.run(10, 'cup')
    r.run(1)                        # one second of missed detections
    r.run(5, 'cup')
    assert r.today()['hydration']['sips'] == 0


def test_sip_times_and_size_come_from_protocols_yaml(config):
    hydration = changed(config.protocols.hydration, sip_gone_max_sec=5, ml_per_sip=50)
    r = Rig(config, protocols=config.protocols.model_copy(update={'hydration': hydration}))
    r.run(10, 'cup')
    r.run(8)                        # 8 s is now too long for a sip
    r.run(5, 'cup')
    assert r.today()['hydration']['sips'] == 0
    r.run(3)                        # gone about 4 s in all: a sip
    r.run(5, 'cup')
    assert r.today()['hydration']['water_ml'] == 50


def test_sip_counts_even_with_a_second_drink_on_the_desk(config):
    r = Rig(config)
    r.run(10, 'cup', 'bottle')
    r.run(8, 'bottle')             # the mug is picked up; the water bottle stays
    r.run(5, 'cup', 'bottle')
    assert r.today()['hydration']['sips'] == 1


def test_weak_detections_are_not_counted(config):
    r = Rig(config)
    r.run(5, 'cup', conf=0.5)       # a cup needs 0.55 (agents.yaml)
    assert not r.state().drink_on_desk
    r.run(5, 'cup', conf=0.6)
    assert r.state().drink_on_desk
    r.run(5, 'laptop', conf=0.3)    # below the general 0.45 (agents.min_confidence)
    assert not r.state().screen_on_desk


# ── breaks and sitting ────────────────────────────────────────────────
def test_break_when_away_five_minutes(config):
    r = Rig(config)
    r.run(40, 'laptop')
    sit_start = r.state().sit_since
    assert sit_start == at('09:00') and r.state().at_desk
    r.run(2 * 60, 'laptop', person=False)                  # 2 min away: not a break yet
    r.run(5, 'laptop')
    assert r.today()['breaks']['count'] == 0 and r.state().sit_since == sit_start
    r.run(6 * 60, 'laptop', person=False)                  # 6 min away
    r.run(5, 'laptop')
    assert r.today()['breaks']['count'] == 1
    brk = r.said('break')[0]
    assert brk.to_dict()['minutes'] >= 5 and brk.to_dict()['how'] == 'left the desk'
    assert brk.say.startswith('Nice, you were away for')
    assert r.state().sit_since > sit_start                 # the sitting clock restarted


def test_someone_is_at_the_desk_for_a_while_after_last_being_seen(config):
    r = Rig(config)
    r.run(5, 'laptop')
    r.run(60, 'laptop', person=False)
    assert r.state().at_desk                                # seen 60 s ago (presence.user_active_window_sec 90)
    r.run(40, 'laptop', person=False)
    assert not r.state().at_desk


def test_movement_counts_as_someone_at_the_desk(config):
    r = Rig(config)
    r.bus.publish(DetectionEvent(ts=r.clock.now(), frame_id='f', source='pi', size=(640, 480), detections=(),
                                 person=False, moved=True, light=Light(brightness=120)))
    assert r.state().at_desk


def test_camera_off_for_a_while_counts_as_a_break(config):
    r = Rig(config)
    r.run(10, 'laptop')
    r.clock.advance(7 * MIN)        # no pictures for 7 minutes
    r.run(2, 'laptop')
    assert r.today()['breaks']['count'] == 1 and r.said('break')[0].to_dict()['how'] == 'camera off'


def test_longest_sit_and_sitting_now(config):
    r = Rig(config)
    r.run(90, 'laptop')
    today = r.today()['breaks']
    assert today['longest_sit_sec'] == pytest.approx(90, abs=1)
    assert today['sitting_sec'] == pytest.approx(90, abs=1) and today['sit_limit_min'] == 30


# ── phone-free time ───────────────────────────────────────────────────
def test_phone_free_time_and_streak(config):
    phone = changed(config.protocols.phone_free, celebrate_every_min=1)
    r = Rig(config, protocols=config.protocols.model_copy(update={'phone_free': phone}))
    r.run(70, 'laptop')                                   # 70 s without a phone
    assert r.today()['phone_free']['phone_free_sec'] > 60
    streaks = r.said('streak')
    assert len(streaks) == 1 and streaks[0].to_dict()['minutes'] == 1
    r.run(30, 'laptop', 'cell phone')
    assert r.state().phone_on_desk_since is not None
    pf = r.today()['phone_free']
    assert pf['streak_sec'] == 0 and pf['phone_on_desk_sec'] > 20 and pf['phone_on_desk'] is True
    assert pf['best_sec'] >= 69 and pf['counted'] is True and pf['goal_min'] == 120


def test_no_cheering_when_celebrate_every_min_is_zero(config):
    phone = changed(config.protocols.phone_free, celebrate_every_min=0)
    r = Rig(config, protocols=config.protocols.model_copy(update={'phone_free': phone}))
    r.run(70, 'laptop')
    assert r.said('streak') == []


def test_phone_not_counted_when_it_is_the_camera(config):
    r = Rig(config, phone_counts=False)
    r.run(20, 'laptop', 'cell phone')
    pf = r.today()['phone_free']
    assert pf['phone_free_sec'] == 0 and pf['counted'] is False and pf['phone_on_desk'] is None
    assert r.state().phone_on_desk_since is None and r.state().phone_counts is False


# ── food ──────────────────────────────────────────────────────────────
def test_food_logged_once_and_meal_finished(config):
    r = Rig(config)
    r.run(4 * 60, 'apple')
    assert r.state().food_since == pytest.approx(at('09:00') + 1, abs=0.4) and r.state().food_name == 'apple'
    r.run(5)
    food = r.said('logged')
    assert len(food) == 1 and food[0].to_dict()['label'] == 'apple' and food[0].to_dict()['healthy'] is True
    assert food[0].say.startswith('\U0001F34E Spotted an apple on the desk')
    assert r.state().meal_done_ts is not None and r.state().meal_name == 'apple' and r.state().food_since is None
    assert [f['label'] for f in r.today()['food']['entries']] == ['apple']


def test_a_quick_snack_is_not_a_meal(config):
    r = Rig(config)
    r.run(60, 'donut')                                     # breaks.meal_min_on_desk_min is 3
    r.run(5)
    assert r.said('logged')[0].to_dict()['healthy'] is False
    assert r.state().meal_done_ts is None


# ── light ─────────────────────────────────────────────────────────────
def test_light_readings(config):
    r = Rig(config)
    r.run(3, 'laptop', light=Light(brightness=180, lux=500))
    assert r.state().bright_since == at('09:00') and r.state().dim_since is None
    assert (r.today()['light']['lux'], r.today()['light']['brightness']) == (500, 180)
    r.run(3, 'laptop', light=Light(brightness=30, lux=20))
    assert r.state().bright_since is None and r.state().dim_since is not None
    assert r.state().lux == 20 and r.state().brightness == 30


def test_screen_on_the_desk(config):
    r = Rig(config)
    r.run(5, 'laptop')
    assert r.state().screen_on_desk
    r.run(5)
    assert not r.state().screen_on_desk


# ── the buttons in the app ────────────────────────────────────────────
def test_manual_logs_and_saving(config):
    store = InMemoryKeyValueStore()
    r = Rig(config, store=store)
    r.tracker.log('hydration', 'glass')
    r.tracker.log('hydration', 'sip')
    r.tracker.log('breaks', 'break')
    r.tracker.log('light', 'sunlight', minutes=15)
    out = r.tracker.log('food', 'food', label='almonds', healthy=True)
    assert out == r.today()
    assert out['hydration']['water_ml'] == 320 and out['breaks']['count'] == 1
    assert out['light']['sunlight_minutes'] == 15 and out['food']['entries'][0]['label'] == 'almonds'
    # a restart keeps today's numbers
    again = Rig(config, store=store)
    assert again.today()['hydration']['water_ml'] == 320 and again.today()['breaks']['count'] == 1


def test_button_sizes_come_from_the_settings(config):
    r = Rig(config, glass_ml=300, button_break_min=7, sunlight_default_min=12)
    r.tracker.log('hydration', 'glass')
    r.tracker.log('breaks', 'break')
    r.tracker.log('light', 'sunlight')
    today = r.today()
    assert today['hydration']['water_ml'] == 300
    assert today['breaks']['log'][0]['minutes'] == 7 and today['light']['sunlight_minutes'] == 12


def test_logged_amounts_win_over_the_defaults(config):
    r = Rig(config)
    r.tracker.log('hydration', 'sip', ml=120)
    r.tracker.log('breaks', 'break', minutes=9)
    assert r.today()['hydration']['water_ml'] == 120 and r.today()['breaks']['log'][0]['minutes'] == 9


def test_a_button_break_restarts_the_sitting_clock(config):
    r = Rig(config)
    r.run(30, 'laptop')
    r.tracker.log('breaks', 'break')
    assert r.state().sit_since == r.clock.now()
    assert r.said('break')[0].say == 'Break logged. Roll your shoulders while you are at it.'


def test_sunlight_close_to_sunset_counts_as_afternoon_light(config):
    r = Rig(config)
    r.clock.advance(6 * 3600)                                  # 15:00, sunset 18:30
    r.tracker.log('light', 'sunlight')
    assert r.today()['light']['afternoon_light_ts'] == at('15:00') and r.today()['light']['sunlight_ts'] is None
    assert 'late-afternoon' in r.said('sunlight')[0].say
    r2 = Rig(config, afternoon_light_hours=3)                  # from 15:30 now
    r2.clock.advance(6 * 3600)
    r2.tracker.log('light', 'sunlight')
    assert r2.today()['light']['sunlight_ts'] == at('15:00') and r2.today()['light']['afternoon_light_ts'] is None


def test_food_label_is_cut_and_entries_can_be_removed(config):
    r = Rig(config, food_label_max_chars=5)
    r.tracker.log('food', 'food', label='blueberry muffin', healthy=False)
    r.tracker.log('food', 'food')
    assert [f['label'] for f in r.today()['food']['entries']] == ['blueb', 'snack']
    r.tracker.log('food', 'remove', index=0)
    r.tracker.log('food', 'remove', index=7)                   # no such entry: nothing happens
    assert [f['label'] for f in r.today()['food']['entries']] == ['snack']


def test_kept_and_shown_lists_come_from_the_settings(config):
    store = InMemoryKeyValueStore()
    r = Rig(config, store=store, breaks_kept=2, breaks_shown=1, drinks_kept=2)
    for _ in range(3):
        r.tracker.log('breaks', 'break')
        r.tracker.log('hydration', 'sip')
    assert len(store.data[DAY_KEY]['break_log']) == 2 and len(store.data[DAY_KEY]['drinks']) == 2
    assert len(r.today()['breaks']['log']) == 1
    assert r.today()['breaks']['count'] == 3 and r.today()['hydration']['sips'] == 3


def test_quiet_logs_are_marked_quiet(config):
    r = Rig(config)
    r.tracker.log('hydration', 'glass', how='chat', quiet=True)
    sip = r.said('sip')[0]
    assert sip.quiet and sip.to_dict()['how'] == 'chat' and sip.say.startswith('Logged! About 0.2 of 2.4 L')


def test_unknown_log_is_refused(config):
    r = Rig(config)
    with pytest.raises(ValueError, match='unknown habit log: dance/jig'):
        r.tracker.log('dance', 'jig')


# ── counters, nudges and the day file ─────────────────────────────────
def test_bump_counts_things_other_parts_notice(config):
    r = Rig(config)
    r.tracker.bump('danger_count')
    r.tracker.bump('danger_count', 2)
    r.tracker.bump('lost_alerts')
    assert r.today()['danger_count'] == 3 and r.today()['lost_alerts'] == 1


def test_nudges_wait_after_being_sent(config):
    p = config.protocols
    quick = p.model_copy(update={'breaks': changed(p.breaks, sit_limit_min=1, repeat_after_min=1),
                                 'hydration': changed(p.hydration, enabled=False),
                                 'light': changed(p.light, enabled=False)})
    store = InMemoryKeyValueStore()
    r = Rig(config, store=store, protocols=quick)
    r.run(70, 'laptop')
    nudges = r.tracker.nudges(r.clock.now(), [])
    assert [n.key for n in nudges] == ['breaks.sit']
    r.tracker.note_fired(nudges[0], r.clock.now())
    assert r.tracker.nudges(r.clock.now(), []) == []           # not again so soon
    r.run(61, 'laptop')
    assert [n.key for n in r.tracker.nudges(r.clock.now(), [])] == ['breaks.sit']
    r.tracker.save()
    assert store.data[DAY_KEY]['nudges_sent'] == 1


def test_rule_state_feeds_the_protocols(config):
    r = Rig(config)
    r.desk.session_start = at('08:30')
    r.run(5, 'cup', 'laptop')
    s = r.state(open_todos=['Essay'])
    assert s.at_desk and s.drink_on_desk and s.screen_on_desk and s.open_todos == ('Essay',)
    assert (s.wake_ts, s.bed_ts, s.sunset_ts) == (at('07:00'), at('22:30'), at('18:30'))
    assert s.session_start == at('08:30')


def test_day_times_follow_the_preferences(config):
    r = Rig(config, prefs=FakePreferences({'wake_time': '06:15', 'bed_time': '23:45'}))
    times = r.today()['times']
    assert times == {'wake_ts': at('06:15'), 'bed_ts': at('23:45'), 'sunset_ts': at('18:30'), 'sunrise_ts': None}
    assert r.tracker.day_times(r.clock.now()) == times


def test_today_has_the_same_shape_as_before(config):
    r = Rig(config)
    r.desk.objects = {'computer': {'class_name': 'laptop'}}     # the roster put Computer on the desk
    r.run(3, 'laptop')
    t = r.today()
    assert set(t) == {'day', 'times', 'hydration', 'breaks', 'phone_free', 'light', 'food', 'desk_sec',
                      'danger_count', 'lost_alerts', 'hourly_activity', 'objects_seen'}
    assert set(t['hydration']) == {'sips', 'water_ml', 'goal_ml', 'on_waking_ml', 'pace_ml', 'last_sip_ts',
                                   'ml_per_sip'}
    assert set(t['breaks']) == {'count', 'last_break_ts', 'sitting_sec', 'longest_sit_sec', 'sit_limit_min', 'log'}
    assert set(t['phone_free']) == {'counted', 'phone_free_sec', 'phone_on_desk_sec', 'streak_sec', 'best_sec',
                                    'phone_on_desk', 'goal_min'}
    assert set(t['light']) == {'sunlight_ts', 'afternoon_light_ts', 'sunlight_minutes', 'lux', 'brightness'}
    assert t['food'] == {'entries': []} and t['day'] == '2026-06-15'
    assert t['objects_seen'] == ['Computer']
    assert t['hydration']['goal_ml'] == 2400 and t['hydration']['on_waking_ml'] == 600
    assert t['hydration']['pace_ml'] == 480 and t['hydration']['ml_per_sip'] == 80     # 2 of 10 hours


def test_desk_time_counts_short_gaps_and_lands_in_its_hour(config):
    r = Rig(config)
    for gap in (0, 1, 10):            # pictures 1 s and then 10 s apart
        r.clock.advance(gap)
        r.picture('laptop')
    t = r.today()
    assert t['desk_sec'] == 3 and t['hourly_activity'][9] == 3     # habit_tracker.gap_counted_max_sec is 2
    r2 = Rig(config, gap_counted_max_sec=5)
    for gap in (0, 1, 10):
        r2.clock.advance(gap)
        r2.picture('laptop')
    assert r2.today()['desk_sec'] == 6


def test_day_file_keeps_the_same_layout(config):
    store = InMemoryKeyValueStore()
    r = Rig(config, store=store)
    r.tracker.log('hydration', 'glass')
    saved = store.data[DAY_KEY]
    assert set(saved) == DAY_FILE_KEYS
    assert saved['day'] == '2026-06-15' and saved['water_ml'] == 240 and len(saved['hourly_activity']) == 24


def test_day_file_from_before_still_loads(config):
    old = {'day': '2026-06-15', 'sips': 3, 'water_ml': 240, 'breaks': 2, 'danger_count': 1, 'something_old': 5,
           'food': [{'label': 'apple', 'healthy': True, 'ts': at('08:00'), 'how': 'camera'}]}
    r = Rig(config, store=InMemoryKeyValueStore({DAY_KEY: old}))
    t = r.today()
    assert (t['hydration']['sips'], t['hydration']['water_ml'], t['breaks']['count'], t['danger_count']) == \
        (3, 240, 2, 1)
    assert t['food']['entries'][0]['label'] == 'apple'


def test_a_day_file_for_another_day_or_broken_is_ignored(config):
    assert Rig(config, store=InMemoryKeyValueStore({DAY_KEY: {'day': '2026-06-14', 'sips': 3}})) \
        .today()['hydration']['sips'] == 0
    assert Rig(config, store=InMemoryKeyValueStore({DAY_KEY: 'broken'})).today()['hydration']['sips'] == 0


def test_new_day_starts_fresh(config):
    store = InMemoryKeyValueStore()
    r = Rig(config, store=store)
    r.tracker.log('hydration', 'glass')
    r.tracker.bump('danger_count')                 # not saved yet...
    r.clock.advance(86400)
    r.run(1, 'laptop')
    assert r.today()['hydration']['water_ml'] == 0 and r.today()['day'] == '2026-06-16'
    assert store.data[DAY_KEY]['danger_count'] == 1      # ...but saved when the day changed


def test_save_only_when_something_changed(config):
    store = InMemoryKeyValueStore()
    r = Rig(config, store=store)
    r.tracker.save()
    assert store.saves == 0
    r.tracker.save(force=True)
    assert store.saves == 1
    r.tracker.bump('lost_alerts')
    r.tracker.save()
    r.tracker.save()
    assert store.saves == 2


def test_pictures_save_the_day_now_and_then(config):
    store = InMemoryKeyValueStore()
    r = Rig(config, store=store)
    r.run(1, 'laptop')
    assert store.saves == 1
    r.run(20, 'laptop')
    assert store.saves == 1                        # habit_tracker.save_every_sec is 30
    r.run(10, 'laptop')
    assert store.saves == 2


def test_how_often_pictures_save_comes_from_the_settings(config):
    store = InMemoryKeyValueStore()
    r = Rig(config, store=store, save_every_sec=10)
    r.run(21, 'laptop')
    assert store.saves == 3


class FlakyStore(InMemoryKeyValueStore):
    def __init__(self):
        super().__init__()
        self.fail = True

    def save(self, key, value):
        if self.fail:
            raise OSError('disk full')
        super().save(key, value)


def test_a_failed_save_is_logged_and_tried_again(config, caplog):
    store = FlakyStore()
    r = Rig(config, store=store)
    with caplog.at_level(logging.WARNING):
        r.tracker.log('hydration', 'glass')
    assert 'disk full' in caplog.text
    store.fail = False
    r.tracker.save()
    assert store.data[DAY_KEY]['water_ml'] == 240


def test_yesterdays_numbers_are_kept_until_they_can_be_saved(config, caplog):
    store = FlakyStore()                           # the disk is full over midnight
    r = Rig(config, store=store)
    with caplog.at_level(logging.WARNING):
        r.tracker.log('hydration', 'glass')
        r.clock.advance(86400)
        r.run(1, 'laptop')                         # a new day: yesterday can't be saved yet
    assert r.today()['day'] == '2026-06-16' and DAY_KEY not in store.data
    store.fail = False
    r.tracker.save()
    assert store.data[DAY_KEY]['water_ml'] == 240
    assert store.data['habits/2026-06-16']['day'] == '2026-06-16'


@pytest.mark.parametrize('ml', [float('nan'), float('inf'), -50])
def test_a_bad_amount_is_refused_and_counts_no_sip(config, ml):
    r = Rig(config)
    with pytest.raises(ValueError):
        r.tracker.log('hydration', 'sip', ml=ml)
    with pytest.raises(ValueError):
        r.tracker.log('hydration', 'glass', ml=ml)
    hydration = r.today()['hydration']
    assert (hydration['sips'], hydration['water_ml'], hydration['last_sip_ts']) == (0, 0, None)


def test_only_detection_events_are_handled(config):
    r = Rig(config)
    r.bus.publish(Alert(ts=1.0, kind='proximity'))
    assert r.bus.stats() == [{'name': 'habits', 'handled': 0, 'waiting': 0, 'dropped': 0, 'errors': 0}]
