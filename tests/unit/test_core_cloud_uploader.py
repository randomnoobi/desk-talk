"""The background job that sends filtered events to a linked cloud server (desktalk/core/cloud/uploader.py).
It reads the event log from a saved place, so there is no second copy of the events, and it does nothing at
all until the computer is linked."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from desktalk.adapters.storage import EventLog
from desktalk.config import CloudSettings, EventLogSettings, load_config
from desktalk.core.cloud.uploader import CloudUploader
from desktalk.core.habits.habit_days import day_key
from desktalk.domain import (
    Alert,
    CameraNotice,
    CloudNotLinked,
    CloudRefused,
    CloudUnavailable,
    Compaction,
    HabitEvent,
)
from desktalk.ports import CloudSending, PeriodicWorker
from desktalk_shared.cloud_api import IngestReply, Refusal
from tests.fakes import FakeClock, FakePreferences, InMemoryEventStore, InMemoryKeyValueStore, RecordingCloudLink

WORDS = load_config(environ={}, dotenv=Path('/nonexistent')).prompts.cloud
START = 1_760_000_000.0                      # 9 October 2025, 08:53 UTC
DAY_SEC, HOUR, MIN = 86_400, 3_600, 60
SETTINGS = CloudSettings(link_key='cloud', send_every_sec=30, idle_check_every_sec=60, idle_beat_min=15, batch_size=500,
                         overlap_sec=120, timeout_sec=10, retry_start_sec=30, retry_max_sec=240,
                         totals_every_min=15, goals_every_hours=24, error_text_chars=200)
SERVER, TOKEN = 'http://cloud.test:8000', 'secret-token-abc'
GOALS = {'water_ml': 2400, 'phone_free_min': 120}


class Rig:
    """The uploader on a real event log (in a temp folder), a stand-in for the cloud server and an in-memory
    store. Nothing is linked until link() is called."""

    def __init__(self, tmp_path: Path, settings: CloudSettings = SETTINGS, keep_days: int = 30):
        self.clock = FakeClock(START)
        self.log = EventLog(tmp_path / 'events', EventLogSettings(
            folder=tmp_path / 'events', compact_every_min=10, merge_gap_sec=3, keep_days=keep_days,
            log_empty_frames=True, prune_every_min=60), self.clock)
        self.store = InMemoryKeyValueStore()
        self.link = RecordingCloudLink()
        self.prefs = FakePreferences({'wake_time': '07:00', 'bed_time': '22:30'})
        self.goals = dict(GOALS)
        self.settings = settings
        self.made: list[tuple[str, str]] = []
        self.uploader = self.new_uploader()

    def new_uploader(self) -> CloudUploader:
        def factory(server: str, token: str):
            self.made.append((server, token))
            return self.link
        return CloudUploader(factory, self.log, self.store, self.prefs, self.goals, self.settings, self.clock,
                             WORDS)

    def linked(self, **changes) -> dict:
        """Link now (place and start at this moment), as scripts/link_cloud.py does."""
        saved = {'server': SERVER, 'device_id': 'dev-1', 'token': TOKEN, 'place_ts': self.clock.now(),
                 'start_ts': self.clock.now(), 'recent_ids': [], 'stopped': False, **changes}
        self.store.save(self.settings.link_key, saved)
        return saved

    def saved(self) -> dict:
        return self.store.load(self.settings.link_key)

    def habit(self, ts: float, id: str | None = None, **extra) -> str:
        id = id or f'h-{ts:g}'
        self.log.write(HabitEvent(ts=ts, id=id, habit='hydration', action='sip', extra={'how': 'camera', **extra}))
        return id

    def tick(self, after: float = 0.0) -> float:
        self.clock.advance(after)
        return self.uploader.tick(self.clock.now())

    def habits_sent(self) -> list[str]:
        return self.link.sent_ids('habit')


@pytest.fixture
def rig(tmp_path):
    return Rig(tmp_path)


# ── what it is ──
def test_it_is_a_periodic_worker_with_a_status(rig):
    assert isinstance(rig.uploader, PeriodicWorker) and isinstance(rig.uploader, CloudSending)
    assert rig.uploader.name == 'cloud uploader'


# ── not linked: nothing at all ──
def test_no_link_calls_nothing_and_waits_long(rig):
    rig.habit(START + 5)
    assert rig.tick(after=10) == SETTINGS.idle_check_every_sec
    assert rig.made == [] and rig.link.calls == []


def test_a_link_without_a_token_counts_as_no_link(rig):
    rig.linked(token='')
    assert rig.tick(after=10) == SETTINGS.idle_check_every_sec
    assert rig.made == []


def test_status_before_linking_says_so(rig):
    assert rig.uploader.status() == {'linked': False, 'stopped': False, 'last_sent_ts': None, 'waiting': 0}


# ── sending ──
def test_sends_oldest_first_and_moves_place(rig):
    rig.linked()
    for offset in (3, 1, 2):                                   # written out of order
        rig.habit(START + offset, id=f'h{offset}')
    wait = rig.tick(after=10)
    assert wait == SETTINGS.send_every_sec
    assert rig.habits_sent() == ['h1', 'h2', 'h3']
    assert rig.saved()['place_ts'] == START + 3
    assert rig.made == [(SERVER, TOKEN)]


def test_what_goes_is_the_filtered_event(rig):
    rig.linked()
    rig.log.write(HabitEvent(ts=START + 1, id='t1', habit='tasks', action='done', say='Done: english essay',
                             extra={'task': 'english essay'}))
    rig.tick(after=10)
    [sent] = [e for e in rig.link.calls[0][0] if e['type'] == 'habit']
    assert sent == {'id': 't1', 'ts': START + 1, 'type': 'habit', 'habit': 'tasks', 'action': 'done'}


def test_starts_from_the_link_moment(rig):
    rig.habit(START - 500, id='long-ago')
    rig.habit(START - 5, id='just-before')               # inside the overlap, but before the link
    rig.linked()
    rig.habit(START + 5, id='after')
    rig.tick(after=10)
    assert rig.habits_sent() == ['after']


def test_events_that_stay_home_still_let_the_place_move(rig):
    rig.linked()
    rig.log.write(Compaction(ts=START + 4, id='c1', frames=10, intervals=2))
    rig.habit(START + 2, id='h2')
    rig.tick(after=10)
    assert rig.habits_sent() == ['h2'] and rig.saved()['place_ts'] == START + 4
    assert 'c1' not in rig.link.sent_ids()


def test_demo_events_are_never_sent(rig):
    rig.linked()
    rig.habit(START + 1, id='real')
    rig.habit(START + 2, id='pretend', demo=True)
    rig.log.write(Alert(ts=START + 3, id='pretend-alert', kind='proximity', agent='cup', extra={'demo': True}))
    rig.log.write(CameraNotice(ts=START + 4, id='pretend-camera', key='covered', kind='problem', text='x',
                               extra={'demo': True}))
    rig.tick(after=10)
    sent = [e['id'] for events, _ in rig.link.calls for e in events if e['type'] not in ('goals', 'daily_totals')]
    assert sent == ['real']


# ── the server is off or failing ──
def test_failure_keeps_place_and_waits_longer(rig):
    rig.linked()
    rig.habit(START + 1, id='h1')
    rig.link.script = [CloudUnavailable('off')] * 4
    waits = [rig.tick(after=10), rig.tick(), rig.tick(), rig.tick()]
    assert waits == [30, 60, 120, 240]                          # retry_start_sec doubling up to retry_max_sec
    assert rig.saved()['place_ts'] == START and rig.saved()['recent_ids'] == []
    assert rig.tick() == SETTINGS.send_every_sec                # it worked: back to the normal wait
    assert rig.habits_sent().count('h1') == 5 and rig.saved()['place_ts'] == START + 1
    rig.link.script = [CloudUnavailable('off')]
    rig.habit(START + 2, id='h2')
    assert rig.tick() == SETTINGS.retry_start_sec               # the count started over


def test_a_failure_is_logged_but_never_raised_and_never_shows_the_token(rig, caplog):
    rig.linked()
    rig.habit(START + 1)
    rig.link.script = [CloudUnavailable('no answer within 10 s'), CloudNotLinked('the server said 401')]
    with caplog.at_level(logging.DEBUG):
        rig.tick(after=10)
        rig.tick()
    assert 'no answer within 10 s' in caplog.text
    assert TOKEN not in caplog.text and TOKEN not in str(rig.uploader.status())


def test_any_other_error_from_the_link_is_left_to_the_supervisor(rig):
    rig.linked()
    rig.habit(START + 1)
    rig.link.script = [RuntimeError('a bug in a link')]
    with pytest.raises(RuntimeError):
        rig.tick(after=10)
    assert rig.saved()['place_ts'] == START


def test_a_log_that_cannot_be_read_is_left_to_the_supervisor(rig):
    rig.linked()

    class Broken(InMemoryEventStore):
        def days(self):
            raise OSError('disk gone')
    rig.uploader = CloudUploader(lambda s, t: rig.link, Broken(), rig.store, rig.prefs, rig.goals, rig.settings,
                                 rig.clock, WORDS)
    with pytest.raises(OSError):                          # raised to the supervisor, which logs it and tries again
        rig.tick(after=10)


# ── the server forgot us ──
def test_not_linked_stops_until_linked_again(rig):
    rig.linked()
    rig.habit(START + 1, id='h1')
    rig.link.script = [CloudNotLinked('401')]
    assert rig.tick(after=10) == SETTINGS.idle_check_every_sec
    assert rig.saved()['stopped'] is True
    assert rig.uploader.status()['stopped'] is True and rig.uploader.status()['linked'] is True
    calls = len(rig.link.calls)
    rig.habit(START + 20, id='h2')
    assert rig.tick(after=60) == SETTINGS.idle_check_every_sec
    assert len(rig.link.calls) == calls                           # nothing more is sent
    rig.linked(token='a-new-token', place_ts=START, start_ts=START)   # linked again: a fresh link clears it
    assert rig.tick(after=10) == SETTINGS.send_every_sec
    assert 'h2' in rig.habits_sent() and rig.made[-1] == (SERVER, 'a-new-token')
    assert rig.uploader.status()['stopped'] is False


def test_a_link_made_while_it_was_stopping_is_not_stopped_by_the_old_answer(rig):
    rig.linked()
    rig.habit(START + 1)

    class Relinks(RecordingCloudLink):
        def send(self, events, *, caught_up):
            rig.linked(token='fresh-token')                      # linked again while the request was out
            raise CloudNotLinked('401 for the old token')
    rig.link = Relinks()
    rig.tick(after=10)
    assert rig.saved()['token'] == 'fresh-token' and rig.saved()['stopped'] is False


# ── refused events ──
def test_refused_events_do_not_hold_up_the_rest(rig, caplog):
    rig.linked()
    for n in (1, 2, 3):
        rig.habit(START + n, id=f'h{n}')
    rig.link.script = [IngestReply(accepted=2, repeats=0, refused=(Refusal(id='h2', why='an odd field'),))]
    with caplog.at_level(logging.INFO):
        assert rig.tick(after=10) == SETTINGS.send_every_sec
    assert rig.saved()['place_ts'] == START + 3
    assert 'an odd field' in caplog.text
    rig.tick(after=60)
    assert rig.habits_sent() == ['h1', 'h2', 'h3']                # h2 is not tried again


# ── batches ──
def test_caught_up_only_on_the_batch_that_empties_the_backlog(tmp_path):
    rig = Rig(tmp_path, SETTINGS.model_copy(update={'batch_size': 2}))
    rig.linked()
    for n in range(1, 6):
        rig.habit(START + n, id=f'h{n}')
    rig.clock.advance(10)
    for _ in range(3):
        rig.tick()
    habits = [[e['id'] for e in events if e['type'] == 'habit'] for events, _ in rig.link.calls]
    assert habits == [['h1'], ['h2', 'h3'], ['h4', 'h5']]          # the goals took one place of the first batch
    assert [caught_up for _, caught_up in rig.link.calls] == [False, False, True]
    assert all(len(events) <= 2 for events, _ in rig.link.calls)
    assert rig.tick() == SETTINGS.send_every_sec
    assert len(rig.link.calls) == 3                               # nothing new: no call


def test_a_backlog_that_is_exactly_one_batch_is_caught_up(tmp_path):
    rig = Rig(tmp_path, SETTINGS.model_copy(update={'batch_size': 3}))      # the goals and two events fill it
    rig.linked()
    rig.habit(START + 1)
    rig.habit(START + 2)
    rig.tick(after=10)
    assert [caught_up for _, caught_up in rig.link.calls] == [True]


def test_nothing_new_means_no_call(rig):
    rig.linked()
    rig.tick(after=10)
    assert rig.link.calls == [] or all(e['type'] in ('goals', 'daily_totals') for e in rig.link.calls[0][0])
    calls = len(rig.link.calls)
    rig.tick(after=30)
    assert len(rig.link.calls) == calls


# ── nothing to send: the server still hears that this computer has caught up ──
BEAT = SETTINGS.idle_beat_min * MIN


def linked_and_up_to_date(rig) -> None:
    rig.linked()
    rig.tick()                                                 # the goals go on the first round
    rig.link.calls.clear()


def test_an_idle_computer_tells_the_server_it_has_caught_up(rig):
    linked_and_up_to_date(rig)
    rig.tick(after=BEAT - 1)
    assert rig.link.calls == []
    rig.tick(after=1)
    assert rig.link.calls == [([], True)]
    assert rig.saved()['last_sent_ts'] == rig.clock.now()


def test_the_idle_beat_goes_at_most_every_idle_beat_min(rig):
    linked_and_up_to_date(rig)
    rig.tick(after=BEAT)
    rig.tick(after=SETTINGS.send_every_sec)
    rig.tick(after=BEAT - SETTINGS.send_every_sec - 1)
    assert len(rig.link.calls) == 1
    rig.tick(after=1)
    assert len(rig.link.calls) == 2


def test_a_real_send_puts_off_the_idle_beat(rig):
    linked_and_up_to_date(rig)
    rig.clock.advance(BEAT - 10)
    rig.habit(rig.clock.now(), id='h1')
    rig.tick()
    rig.tick(after=10)
    assert [events[0]['id'] for events, _ in rig.link.calls] == ['h1']


def test_no_idle_beat_when_not_linked_or_stopped(rig):
    rig.tick(after=BEAT * 2)
    assert rig.link.calls == [] and rig.made == []
    linked_and_up_to_date(rig)
    rig.store.save(SETTINGS.link_key, {**rig.saved(), 'stopped': True})
    rig.tick(after=BEAT * 2)
    assert rig.link.calls == []


def test_no_idle_beat_while_a_days_changed_numbers_are_waiting_their_turn(rig):
    """It has not really caught up then: a report could be written from older numbers than the computer has."""
    rig.linked()
    day = rig.clock.local_day(rig.clock.now())
    rig.store.save(day_key(day), {'day': day, 'desk_sec': 60})
    rig.tick()                                                 # the numbers go; next after totals_every_min
    rig.link.calls.clear()
    rig.store.save(day_key(day), {'day': day, 'desk_sec': 120})
    held_by = SETTINGS.totals_every_min * MIN
    beat_settings = SETTINGS.model_copy(update={'idle_beat_min': 1})
    rig.settings = beat_settings
    rig.uploader = rig.new_uploader()
    rig.tick(after=held_by - 1)
    assert rig.link.calls == []                                # numbers held back, so no word of having caught up
    rig.tick(after=1)
    sent, caught_up = rig.link.calls[0]
    assert [e['type'] for e in sent] == ['daily_totals'] and caught_up is True


def test_a_failed_idle_beat_is_tried_again_after_a_wait_and_never_raises(rig):
    linked_and_up_to_date(rig)
    rig.link.script = [CloudUnavailable('the cloud server is off')]
    assert rig.tick(after=BEAT) == SETTINGS.retry_start_sec
    assert rig.saved()['last_sent_ts'] != rig.clock.now()
    rig.tick(after=SETTINGS.retry_start_sec)
    assert rig.link.calls[-1] == ([], True)


def test_an_idle_beat_refused_with_not_linked_stops_sending(rig):
    linked_and_up_to_date(rig)
    rig.link.script = [CloudNotLinked('no such token')]
    assert rig.tick(after=BEAT) == SETTINGS.idle_check_every_sec
    assert rig.saved()['stopped'] is True


def test_a_last_sent_time_in_the_future_does_not_silence_the_idle_beat(rig):
    linked_and_up_to_date(rig)
    rig.store.save(SETTINGS.link_key, {**rig.saved(), 'last_sent_ts': rig.clock.now() + DAY_SEC})
    rig.tick(after=1)
    assert rig.link.calls == [([], True)]


# ── late events and repeats ──
def test_event_written_late_within_overlap_is_still_sent(rig):
    rig.linked()
    rig.habit(START + 1_000, id='first')
    rig.tick(after=2_000)                                        # the place is now START + 1000
    rig.habit(START + 900, id='late')                            # 100 s before the place: inside the 120 s overlap
    rig.habit(START + 800, id='too-late')                        # 200 s before the place: outside it
    rig.tick(after=30)
    assert rig.habits_sent() == ['first', 'late']


def test_recent_ids_stop_resends(rig):
    rig.linked()
    rig.habit(START + 100, id='first')
    rig.tick(after=200)
    assert 'first' in rig.saved()['recent_ids']
    rig.tick(after=30)
    rig.tick(after=30)
    assert rig.habits_sent() == ['first']                         # still inside the overlap, but not sent twice


def test_old_ids_are_forgotten(rig):
    rig.linked()
    rig.habit(START + 10, id='first')
    rig.tick(after=20)
    rig.habit(START + 1_000, id='second')
    rig.tick(after=2_000)
    assert 'first' not in rig.saved()['recent_ids'] and 'second' in rig.saved()['recent_ids']


def test_the_place_is_kept_when_the_app_restarts(rig):
    rig.linked()
    rig.habit(START + 1, id='h1')
    rig.tick(after=10)
    again = rig.new_uploader()
    rig.habit(START + 2, id='h2')
    rig.clock.advance(10)
    again.tick(rig.clock.now())
    assert rig.habits_sent() == ['h1', 'h2']


def test_place_on_a_deleted_day_carries_on(tmp_path):
    rig = Rig(tmp_path, keep_days=3)
    rig.linked(place_ts=START - 10 * DAY_SEC, start_ts=START - 10 * DAY_SEC)     # offline for ten days
    for days_ago, name in ((10, 'gone-1'), (9, 'gone-2'), (2, 'kept-1'), (1, 'kept-2'), (0, 'kept-3')):
        rig.habit(START - days_ago * DAY_SEC, id=name)
    assert rig.log.prune(START) == [rig.clock.local_day(START - 10 * DAY_SEC),
                                    rig.clock.local_day(START - 9 * DAY_SEC)]
    rig.tick(after=10)
    assert rig.habits_sent() == ['kept-1', 'kept-2', 'kept-3']
    assert rig.saved()['place_ts'] == START


def test_events_are_found_across_midnight(rig):
    rig.linked(place_ts=START - 100, start_ts=START - 100)
    midnight = (int(START) // DAY_SEC + 1) * DAY_SEC
    rig.clock.advance(midnight - START + 100)
    rig.habit(midnight - 30, id='before')
    rig.habit(midnight + 30, id='after')
    rig.tick()
    assert rig.habits_sent() == ['before', 'after']


# ── goals ──
def goals_sent(rig) -> list[dict]:
    return [e for events, _ in rig.link.calls for e in events if e['type'] == 'goals']


def test_goals_sent_at_link_on_change_and_daily(rig):
    rig.linked()
    rig.tick(after=10)
    [first] = goals_sent(rig)
    assert first['wake'] == '07:00' and first['bed'] == '22:30' and first['goals'] == GOALS
    assert first['id'] == f'goals-{int(rig.clock.now())}'
    rig.tick(after=HOUR)
    assert len(goals_sent(rig)) == 1                              # unchanged: not again
    rig.prefs.update({'bed_time': '23:00'})
    rig.tick(after=60)
    assert [g['bed'] for g in goals_sent(rig)] == ['22:30', '23:00']
    rig.goals['water_ml'] = 2000
    rig.tick(after=60)
    assert goals_sent(rig)[-1]['goals']['water_ml'] == 2000
    rig.tick(after=23 * HOUR)
    assert len(goals_sent(rig)) == 3                              # not yet a day since the last
    rig.tick(after=2 * HOUR)
    assert len(goals_sent(rig)) == 4                              # a day: sent again though nothing changed


def test_goals_are_not_sent_while_failing_and_go_with_the_next_good_send(rig):
    rig.linked()
    rig.link.script = [CloudUnavailable('off')]
    rig.tick(after=10)
    assert rig.saved().get('goals_sent') is None
    rig.tick(after=30)
    assert len(goals_sent(rig)) == 2                              # tried twice, kept once the server answered
    rig.tick(after=30)
    assert len(goals_sent(rig)) == 2


def test_goals_count_inside_the_batch(tmp_path):
    rig = Rig(tmp_path, SETTINGS.model_copy(update={'batch_size': 2}))
    rig.linked()
    rig.habit(START + 1, id='h1')
    rig.habit(START + 2, id='h2')
    rig.tick(after=10)
    rig.tick()
    assert [len(events) for events, _ in rig.link.calls] == [2, 1]     # goals + h1, then h2
    assert rig.habits_sent() == ['h1', 'h2']
    assert [caught_up for _, caught_up in rig.link.calls] == [False, True]


# ── a day's numbers ──
DAY = '2026-01-01'


def day_numbers(**changes) -> dict:
    return {'day': DAY, 'desk_sec': 3_600.0, 'hourly_activity': [0] * 9 + [600] + [0] * 14, 'water_ml': 800,
            'sips': 10, 'breaks': 2, 'longest_sit_sec': 1_500, 'phone_free_sec': 1_200.0, 'phone_on_desk_sec': 60.0,
            'sunlight_minutes': 15, 'nudges_sent': 3, 'food': [{'label': 'banana', 'healthy': True, 'ts': 1.0,
                                                               'how': 'camera'}], **changes}


def totals_sent(rig) -> list[dict]:
    return [e for events, _ in rig.link.calls for e in events if e['type'] == 'daily_totals']


def put_day(rig, day: str, **changes) -> None:
    rig.store.save(day_key(day), day_numbers(day=day, **changes))


def test_totals_sent_when_changed_at_most_every_totals_every_min(rig):
    today = rig.clock.local_day(START)
    rig.linked()
    put_day(rig, today)
    rig.tick(after=10)
    [first] = totals_sent(rig)
    assert first['id'] == f'daily-{today}' and first['day'] == today
    assert first['totals'] == {'desk_sec': 3_600.0, 'water_ml': 800, 'sips': 10, 'breaks': 2, 'longest_sit_sec': 1_500,
                               'phone_free_sec': 1_200.0, 'phone_on_desk_sec': 60.0, 'sunlight_minutes': 15,
                               'nudges_sent': 3, **{f'hour_{h:02d}': 0 for h in range(24) if h != 9},
                               'hour_09': 600}
    rig.tick(after=2 * MIN)
    assert len(totals_sent(rig)) == 1                             # nothing changed: nothing sent
    put_day(rig, today, water_ml=900)
    rig.tick(after=5 * MIN)
    assert len(totals_sent(rig)) == 1                             # changed, but sent too lately
    rig.tick(after=9 * MIN)
    assert [t['totals']['water_ml'] for t in totals_sent(rig)] == [800, 900]


def test_no_day_file_means_no_totals(rig):
    rig.linked()
    rig.tick(after=10)
    assert totals_sent(rig) == []


def test_totals_never_hold_anything_but_numbers(rig):
    today = rig.clock.local_day(START)
    rig.linked()
    put_day(rig, today, objects_seen=['Cup'], summary_sent=True)
    rig.tick(after=10)
    assert all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in totals_sent(rig)[0]['totals'].values())


def test_yesterdays_last_numbers_are_sent_after_midnight(rig):
    today = rig.clock.local_day(START)
    midnight = (int(START) // DAY_SEC + 1) * DAY_SEC
    rig.linked()
    put_day(rig, today, water_ml=800)
    rig.tick(after=10)
    put_day(rig, today, water_ml=1_000)                           # the last drink of the day
    rig.clock.advance(midnight - rig.clock.now() + 60)
    tomorrow = rig.clock.local_day(rig.clock.now())
    put_day(rig, tomorrow, water_ml=50)
    rig.tick()
    sent = {(t['day'], t['totals']['water_ml']) for t in totals_sent(rig)}
    assert (today, 1_000) in sent and (tomorrow, 50) in sent


def test_a_broken_day_file_is_skipped(rig):
    today = rig.clock.local_day(START)
    rig.linked()
    rig.store.save(day_key(today), ['not', 'a', 'day'])
    rig.tick(after=10)
    assert totals_sent(rig) == []


# ── what the web page may know ──
def test_status_after_sending(rig):
    rig.linked()
    rig.habit(START + 1)
    rig.tick(after=10)
    assert rig.uploader.status() == {'linked': True, 'stopped': False, 'last_sent_ts': START + 10, 'waiting': 0}


def test_status_counts_what_is_waiting_while_the_server_is_off(rig):
    rig.linked()
    for n in (1, 2, 3):
        rig.habit(START + n)
    rig.link.failure = CloudUnavailable('off')
    rig.tick(after=10)
    assert rig.uploader.status() == {'linked': True, 'stopped': False, 'last_sent_ts': None, 'waiting': 3}


def test_status_never_has_the_token_or_the_address(rig):
    rig.linked()
    rig.tick(after=10)
    text = str(rig.uploader.status())
    assert TOKEN not in text and SERVER not in text and 'dev-1' not in text
    assert set(rig.uploader.status()) == {'linked', 'stopped', 'last_sent_ts', 'waiting'}


def test_the_last_sent_time_is_kept_when_the_app_restarts(rig):
    rig.linked()
    rig.habit(START + 1)
    rig.tick(after=10)
    assert rig.new_uploader().status()['last_sent_ts'] == START + 10


# ── a server that refuses an upload that is too big ──
def test_every_upload_fits_the_servers_limit_even_with_goals_and_totals(tmp_path):
    """The real limit: the server takes 500 events and refuses (413) more. A full batch plus the goals and the day's
    numbers used to make 501 to 503 and the sender stalled for good."""
    rig = Rig(tmp_path)
    rig.link = RecordingCloudLink(limit=500)
    rig.linked()
    put_day(rig, rig.clock.local_day(START))
    ids = [rig.habit(START + 1 + n, id=f'e{n}') for n in range(1_200)]
    rig.clock.advance(2_000)
    for _ in range(8):
        assert rig.tick() == SETTINGS.send_every_sec               # never a failure
    assert all(len(events) <= 500 for events, _ in rig.link.calls)
    assert rig.habits_sent() == ids
    flags = [caught_up for _, caught_up in rig.link.calls]
    assert flags[-1] is True and flags.count(True) == 1
    assert rig.uploader.status()['waiting'] == 0


def test_goals_and_totals_bigger_than_the_room_are_split_and_events_still_get_through(tmp_path):
    rig = Rig(tmp_path, SETTINGS.model_copy(update={'batch_size': 2}))
    rig.link = RecordingCloudLink(limit=2)
    rig.linked()
    for back in range(4):                                         # four days of numbers: with the goals, five extras
        put_day(rig, rig.clock.local_day(START + back * DAY_SEC))
    for n in (1, 2, 3):
        rig.habit(START + n, id=f'h{n}')
    rig.clock.advance(3 * DAY_SEC)
    for _ in range(6):
        rig.tick()
    assert all(len(events) <= 2 for events, _ in rig.link.calls)
    assert rig.habits_sent() == ['h1', 'h2', 'h3']
    assert len(goals_sent(rig)) == 1 and len({t['day'] for t in totals_sent(rig)}) == 4


def test_a_server_that_refuses_the_upload_is_logged_apart_from_one_that_is_off(rig, caplog):
    rig.linked()
    rig.habit(START + 1)
    rig.link.script = [CloudRefused('the server answered 413: too many'), CloudUnavailable('no answer within 10 s')]
    with caplog.at_level(logging.WARNING):
        assert rig.tick(after=10) == SETTINGS.retry_start_sec       # it still waits and tries again
        refused = caplog.text
        caplog.clear()
        rig.tick()
    assert 'refused the upload' in refused and 'too many' in refused
    assert 'refused' not in caplog.text and 'no answer within 10 s' in caplog.text


# ── a link that cannot be used ──
@pytest.mark.parametrize('change', [
    {'recent_ids': 5}, {'recent_ids': [1, 2]}, {'server': 'ftp://cloud.test'}, {'server': 'http://'},
    {'server': 'http://[bad'}, {'place_ts': float('nan')}, {'place_ts': float('inf')}, {'place_ts': 'soon'},
    {'start_ts': float('nan')}, {'token': 'abc\ndef'}, {'token': 'abc def'}, {'token': 'secret\r\nX-Evil: 1'},
    {'stopped': 'maybe'}],
    ids=lambda c: next(iter(c)) + '=' + repr(next(iter(c.values())))[:12])
def test_a_damaged_link_counts_as_not_linked(rig, caplog, change):
    rig.linked(**change)
    rig.habit(START + 1, id='h1')
    with caplog.at_level(logging.WARNING):
        assert rig.tick(after=10) == SETTINGS.idle_check_every_sec
        rig.tick()
    assert rig.made == [] and rig.link.calls == []
    assert rig.uploader.status() == {'linked': False, 'stopped': False, 'last_sent_ts': None, 'waiting': 0}
    assert caplog.text.count(WORDS.log_bad_link) == 1                # said once, not every round
    assert 'secret' not in caplog.text and 'abc' not in caplog.text
    rig.linked(place_ts=START, start_ts=START)                         # linked again: it works
    rig.tick()
    assert rig.habits_sent() == ['h1']


def test_a_link_that_is_not_even_a_dict_counts_as_not_linked(rig):
    rig.store.save(SETTINGS.link_key, ['not', 'a', 'link'])
    assert rig.tick(after=10) == SETTINGS.idle_check_every_sec and rig.made == []


# ── the clock ──
def test_a_future_dated_event_does_not_move_the_place_into_the_future(rig):
    rig.linked()
    rig.habit(START + 10 * DAY_SEC, id='future')                   # the computer's clock was ahead for a while
    rig.tick(after=10)
    assert rig.habits_sent() == ['future'] and rig.saved()['place_ts'] == START + 10
    rig.habit(START + 60, id='later')                              # written after the clock was corrected
    rig.tick(after=60)
    assert rig.habits_sent() == ['future', 'later']
    rig.tick(after=60)
    assert rig.habits_sent().count('future') == 1                  # the future one is not sent again


# ── the day's numbers over a long outage ──
def test_totals_of_every_day_of_a_three_day_outage_are_sent_and_none_from_before_linking(rig):
    days = [rig.clock.local_day(START + n * DAY_SEC) for n in range(4)]
    before = rig.clock.local_day(START - DAY_SEC)
    rig.linked()
    put_day(rig, before, water_ml=111)                               # numbers from before linking
    put_day(rig, days[0], water_ml=100)
    rig.link.failure = CloudUnavailable('off')
    rig.tick(after=10)
    for n, day in enumerate(days[1:], start=1):                      # three days go by with the server off
        put_day(rig, day, water_ml=100 * (n + 1))
        rig.tick(after=DAY_SEC)
    rig.link.failure = None
    rig.tick(after=10 * MIN)
    sent = {t['day']: t['totals']['water_ml'] for events, _ in rig.link.calls[-1:] for t in events
            if t['type'] == 'daily_totals'}
    assert sent == dict(zip(days, (100, 200, 300, 400), strict=True))
    assert before not in {t['day'] for t in totals_sent(rig)}


def test_totals_sent_for_a_day_are_not_sent_again_unchanged(rig):
    days = [rig.clock.local_day(START + n * DAY_SEC) for n in range(3)]
    rig.linked()
    for day in days:
        put_day(rig, day)
    rig.tick(after=2 * DAY_SEC)
    rig.tick(after=HOUR)
    assert sorted(t['day'] for t in totals_sent(rig)) == days


# ── pretend sips ──
def test_the_demo_tabs_pretend_sips_are_taken_out_of_the_totals(rig):
    today = rig.clock.local_day(START)
    rig.linked()
    put_day(rig, today, sips=10, water_ml=800, demo_sips=2, demo_water_ml=160)
    rig.tick(after=10)
    [sent] = totals_sent(rig)
    assert sent['totals']['sips'] == 8 and sent['totals']['water_ml'] == 640


# ── numbers no clock can turn into a day ──
@pytest.mark.parametrize('change', [
    {'place_ts': 1e300}, {'place_ts': -1e300}, {'start_ts': 1e300}, {'start_ts': -1e300},
    {'last_sent_ts': 1e300}, {'last_sent_ts': -1e300}, {'place_ts': 1e18}, {'start_ts': -1e18}],
    ids=lambda c: next(iter(c)) + '=' + repr(next(iter(c.values()))))
def test_a_time_no_clock_can_turn_into_a_day_counts_as_a_damaged_link(rig, caplog, change):
    rig.linked(**change)
    rig.habit(START + 1, id='h1')
    with caplog.at_level(logging.WARNING):
        assert rig.tick(after=10) == SETTINGS.idle_check_every_sec     # no OverflowError out of tick
    assert rig.made == [] and rig.uploader.status()['linked'] is False
    assert caplog.text.count(WORDS.log_bad_link) == 1


def test_remembered_times_that_no_clock_can_use_do_not_stop_the_sender(rig):
    odd = {rig.clock.local_day(START): {'sig': 'y', 'ts': -1e300}}
    rig.linked(goals_sent={'sig': 'x', 'ts': 1e300}, totals_sent=odd, totals_from='not a day')
    put_day(rig, rig.clock.local_day(START))
    rig.habit(START + 1, id='h1')
    rig.tick(after=10)
    assert rig.habits_sent() == ['h1'] and len(totals_sent(rig)) == 1


# ── a day's last numbers ──
def test_a_days_last_totals_are_not_lost_when_an_events_only_send_comes_between(rig):
    """Yesterday's numbers go at 23:50, change at 23:58, and are skipped at 00:00 by the 15-minute gate; an
    events-only send at 00:02 must not make yesterday drop out of the days looked at."""
    first = rig.clock.local_day(START)
    midnight = (int(START) // DAY_SEC + 1) * DAY_SEC
    rig.linked()
    put_day(rig, first, water_ml=100)
    rig.clock.advance(midnight - 10 * MIN - rig.clock.now())          # 23:50
    rig.tick()
    assert [t['totals']['water_ml'] for t in totals_sent(rig)] == [100]
    put_day(rig, first, water_ml=150)                                  # 23:58: the last drink of the day
    rig.clock.advance(12 * MIN)                                        # 00:02
    rig.habit(midnight + 60, id='after-midnight')
    rig.tick()
    assert rig.habits_sent() == ['after-midnight']                     # an events-only send...
    assert [t['totals']['water_ml'] for t in totals_sent(rig)] == [100]    # ...the gate still holds yesterday back
    rig.tick(after=20 * MIN)                                           # 00:22
    assert [(t['day'], t['totals']['water_ml']) for t in totals_sent(rig)] == [(first, 100), (first, 150)]
    rig.tick(after=HOUR)
    assert len(totals_sent(rig)) == 2                                  # settled: not sent again
    assert rig.saved()['totals_from'] == rig.clock.local_day(rig.clock.now())      # and no longer looked at


def test_a_day_stays_looked_at_until_it_is_over_and_its_numbers_have_not_changed_for_a_while(rig):
    first = rig.clock.local_day(START)
    rig.linked()
    put_day(rig, first)
    rig.tick(after=10)
    assert rig.saved()['totals_from'] == first                        # the day is not over
    rig.tick(after=DAY_SEC)                                           # it is over, and its numbers still match
    assert rig.saved()['totals_from'] == rig.clock.local_day(rig.clock.now())
    assert len(totals_sent(rig)) == 1


def test_a_day_whose_numbers_changed_after_it_ended_is_sent_before_it_is_left(rig):
    first = rig.clock.local_day(START)
    rig.linked()
    put_day(rig, first, water_ml=100)
    rig.tick(after=10)
    put_day(rig, first, water_ml=999)
    rig.tick(after=DAY_SEC)
    assert [t['totals']['water_ml'] for t in totals_sent(rig)] == [100, 999]
    rig.tick(after=HOUR)
    assert rig.saved()['totals_from'] == rig.clock.local_day(rig.clock.now())


# ── a saved link with odd values in any of its fields ──
ODD = [None, 'abc', [1], {}, 10**400]         # the last is a whole number too big for float maths
ODD_IDS = ['None', 'text', 'list', 'dict', 'huge']


@pytest.mark.parametrize('odd', ODD, ids=ODD_IDS)
@pytest.mark.parametrize('field', ['place_ts', 'start_ts', 'last_sent_ts'])
def test_a_time_field_that_is_not_a_number_counts_as_a_damaged_link(rig, caplog, field, odd):
    rig.linked(**{field: odd})
    rig.habit(START + 1)
    with caplog.at_level(logging.WARNING):
        assert rig.tick(after=10) == SETTINGS.idle_check_every_sec      # never raises out of tick
    assert rig.made == [] and rig.uploader.status() == {'linked': False, 'stopped': False, 'last_sent_ts': None,
                                                        'waiting': 0}
    assert caplog.text.count(WORDS.log_bad_link) == 1


@pytest.mark.parametrize('odd', ODD, ids=ODD_IDS)
@pytest.mark.parametrize('field', ['goals_sent', 'totals_sent', 'totals_from', 'device_id', 'recent_ids', 'stopped'])
def test_any_other_field_with_an_odd_value_never_raises_out_of_tick(rig, field, odd):
    rig.linked(**{field: odd})
    put_day(rig, rig.clock.local_day(START))
    rig.habit(START + 1, id='h1')
    rig.tick(after=10)                                                   # linked or counted as damaged, never raises
    rig.tick(after=60)
    assert rig.link.sent_ids('habit').count('h1') <= 1


@pytest.mark.parametrize('odd', ODD, ids=ODD_IDS)
def test_the_sender_s_own_notes_with_odd_entries_never_raise(rig, odd):
    today = rig.clock.local_day(START)
    rig.linked(goals_sent=odd, totals_sent={today: odd, 'not a day': odd})
    put_day(rig, today)
    rig.tick(after=10)
    assert len(totals_sent(rig)) == 1


# ── a day left alone while an earlier one waits ──
def test_an_unchanged_day_is_sent_once_while_an_earlier_day_is_still_waiting(rig):
    days = [rig.clock.local_day(START + n * DAY_SEC) for n in range(3)]
    rig.linked()
    for day in days:
        put_day(rig, day, water_ml=100)
    rig.tick(after=2 * DAY_SEC)                                          # all three go at once
    put_day(rig, days[0], water_ml=200)                                  # the first changes: it is pinned again
    for _ in range(4):
        rig.tick(after=20 * MIN)
    sent = [(t['day'], t['totals']['water_ml']) for t in totals_sent(rig)]
    assert sorted(sent) == [(days[0], 100), (days[0], 200), (days[1], 100), (days[2], 100)]
    assert rig.saved()['totals_sent'].keys() <= set(days)
