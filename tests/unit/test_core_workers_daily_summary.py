"""The end-of-day recap: once a day at the recap hour, if the camera ran that day."""

from __future__ import annotations

import random
import time

from desktalk.config import AiReplies, AiReply, DailySummarySettings
from desktalk.core.chat.workers.daily_summary import DailySummary, recap_facts
from tests.fakes import FakeChatModel, FakeHabits, InMemoryKeyValueStore, RecordingNotifier, StaticRoster

SETTINGS = DailySummarySettings(hour=21, start_delay_sec=120, check_every_sec=60, objects_listed=8)
REPLIES = AiReplies.model_construct(daily_recap=AiReply(max_tokens=140, temperature=0.7))

STATS = {
    'day': '2026-10-21',
    'hourly_activity': [0] * 9 + [10, 20] + [0, 0] + [5, 5, 5] + [0] * 8,
    'hydration': {'water_ml': 1200, 'goal_ml': 2400, 'sips': 15},
    'breaks': {'count': 3, 'longest_sit_sec': 3000},
    'phone_free': {'counted': True, 'phone_free_sec': 5400, 'best_sec': 1800.0},
    'light': {'sunlight_ts': 1_761_030_000.0},
    'desk_sec': 18000,
    'danger_count': 2,
    'lost_alerts': 1,
    'objects_seen': ['Computer', 'Cup'],
}
FACTS = ('Busiest period: between 9:00 and 11:00. Water: about 1.2 of 2.4 L (15 sips). '
         'Breaks taken: 3. Longest stretch sitting: 50 minutes. '
         'Phone-free desk time: 90 minutes (best streak 30). Morning sunlight: logged. '
         'Total time at desk: ~300 minutes. Near-spill or danger alerts: 2. Lost-object alerts: 1. '
         'Objects seen today: Computer, Cup.')


def local(hour: int, minute: int = 0, day: int = 21) -> float:
    """hour:minute on October <day> 2026, in this computer's time zone."""
    return time.mktime((2026, 10, day, hour, minute, 0, 0, 0, -1))


def make(ai=None, members=('cup',), stats=None, store=None):
    notifier = RecordingNotifier()
    recap = DailySummary(ai or FakeChatModel('What a day! Well done.'), notifier, StaticRoster(members),
                         FakeHabits(today=STATS if stats is None else stats), store or InMemoryKeyValueStore(),
                         SETTINGS, REPLIES, default_temperature=0.8, rng=random.Random(1))
    return recap, notifier


# ── the facts ──
def test_recap_facts_lists_the_days_numbers():
    assert recap_facts(STATS, objects_listed=8) == FACTS


def test_recap_facts_leaves_out_what_did_not_happen():
    quiet_day = {'day': '2026-10-21', 'hourly_activity': [0] * 24,
                 'hydration': {'water_ml': 0, 'goal_ml': 2400, 'sips': 0},
                 'breaks': {'count': 0, 'longest_sit_sec': 0},
                 'phone_free': {'counted': False, 'phone_free_sec': 0, 'best_sec': 0},
                 'light': {'sunlight_ts': None}, 'desk_sec': 0, 'danger_count': 0, 'lost_alerts': 0,
                 'objects_seen': []}
    assert recap_facts(quiet_day, objects_listed=8) == (
        'Water: about 0.0 of 2.4 L (0 sips). Breaks taken: 0. Longest stretch sitting: 0 minutes. '
        'Morning sunlight: not logged.')


def test_recap_facts_names_only_the_first_few_objects():
    stats = dict(STATS, objects_seen=['Computer', 'Cup', 'Paper'])
    assert recap_facts(stats, objects_listed=2).endswith('Objects seen today: Computer, Cup.')


# ── the job ──
def test_the_recap_goes_out_at_the_recap_hour_from_an_agent_in_the_chat():
    ai = FakeChatModel('What a day! Well done.')
    recap, notifier = make(ai)
    assert recap.tick(local(21, 30)) == 60
    assert [(m['type'], m['character'], m['text'], m['recap']) for m in notifier.sent] == [
        ('group_message', 'cup', 'What a day! Well done.', True)]
    call = ai.calls[0]
    assert (call['max_tokens'], call['temperature'], call['background']) == (140, 0.7, True)
    assert call['messages'][0]['role'] == 'system' and 'Summarize' in call['messages'][0]['content']
    assert FACTS in call['messages'][1]['content']


def test_no_recap_outside_the_recap_hour():
    recap, notifier = make()
    recap.tick(local(20, 59))
    recap.tick(local(22, 0))
    assert notifier.sent == []


def test_no_recap_when_the_camera_saw_nothing_today():
    recap, notifier = make(stats=dict(STATS, objects_seen=[]))
    recap.tick(local(21, 30))
    assert notifier.sent == []


def test_only_one_recap_a_day_even_after_a_restart():
    store = InMemoryKeyValueStore()
    recap, notifier = make(store=store)
    recap.tick(local(21, 0))
    recap.tick(local(21, 1))
    assert len(notifier.sent) == 1
    restarted, notifier_after_restart = make(store=store)
    restarted.tick(local(21, 45))
    assert notifier_after_restart.sent == []
    next_day, notifier_next_day = make(store=store, stats=dict(STATS, day='2026-10-22'))
    next_day.tick(local(21, 10, day=22))
    assert len(notifier_next_day.sent) == 1


def test_built_in_recap_when_the_ai_cannot_answer():
    recap, notifier = make(FakeChatModel(fail='Gemini is out of quota for today'))
    recap.tick(local(21, 30))
    assert notifier.texts() == [f"Today's desk recap: {FACTS}"]


def test_lens_gives_the_recap_when_nobody_is_in_the_chat():
    recap, notifier = make(members=())
    recap.tick(local(21, 30))
    assert notifier.sent[0]['character'] == 'lens'


class FullDisk(InMemoryKeyValueStore):
    def save(self, key, value):
        raise OSError('No space left on device')


def test_a_recap_that_cannot_be_remembered_on_disk_is_still_sent_only_once(caplog):
    recap, notifier = make(store=FullDisk())
    assert recap.tick(local(21, 0)) == SETTINGS.check_every_sec          # no error: the Supervisor won't retry
    recap.tick(local(21, 1))
    assert len(notifier.sent) == 1
    assert 'No space left on device' in caplog.text
