"""The weekly report job: who is due, when to wait for a Pi, retrying, and one Pi never stopping another."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from desktalk_cloud.domain import DONE, FAILED, NO_DATA, PENDING, Report
from desktalk_cloud.ports import ReportFailed
from desktalk_cloud.report_data import load_report_words
from desktalk_cloud.report_errors import ReportWillNotWork
from desktalk_cloud.report_job import ReportJob
from desktalk_cloud.store_memory import MemoryStore
from tests.fakes import FakeClock
from tests.fakes.cloud import FakeReportWriter, cloud_config, link_device, make_parts

DAY = 86400
DUE = datetime(2026, 11, 1, 20, tzinfo=UTC).timestamp()          # Sunday 1 Nov, 20:00 UTC
WEEK = '2026-10-26'                                              # the Monday that week's days start on
CONFIG = cloud_config()
WORDS = load_report_words(CONFIG.prompts, CONFIG.messages)
SETTINGS = CONFIG.reports


def at(day: int, hour: int, month: int = 10) -> float:
    return datetime(2026, month, day, hour, tzinfo=UTC).timestamp()


class Setup:
    """A server whose clock starts on Tuesday 20 Oct 12:00, with a job, a fake writer and one linked Pi (ann)."""

    def __init__(self, store: MemoryStore | None = None, writer: Any = None):
        self.clock = FakeClock(start=at(20, 12))
        self.parts = make_parts(clock=self.clock, store=store)
        self.store = self.parts.store
        self.writer = writer or FakeReportWriter('# The week')
        self.job = ReportJob(self.store, self.writer, self.clock, SETTINGS, WORDS)
        self.ann = link_device(self.parts, 'ann').device

    def go_to(self, ts: float | None) -> None:
        assert ts is not None
        self.clock.advance(ts - self.clock.now())

    def send(self, device=None, *, at_ts: float, events=(), caught_up=True) -> None:
        self.store.save_batch((device or self.ann).id, list(events), [], [], caught_up, at_ts)

    def tick(self) -> float:
        return self.job.tick(self.clock.now())

    def row(self, device=None, week: str = WEEK) -> Report | None:
        return self.store.get_report((device or self.ann).id, week)

    def report(self, device=None, week: str = WEEK) -> Report:
        found = self.row(device, week)
        assert found is not None
        return found


def sip(event_id: str, ts: float) -> dict[str, Any]:
    return {'id': event_id, 'ts': ts, 'type': 'habit', 'habit': 'hydration', 'action': 'sip', 'how': 'camera', 'ml': 80}


# ── when a report is written ─────────────────────────────────────────────────────────────────────────────────────
def test_writes_when_due_and_caught_up():
    s = Setup()
    s.send(events=[sip('s1', at(28, 9))], at_ts=DUE + 300)
    s.go_to(DUE - 10)
    s.tick()
    assert s.row() is None and s.writer.calls == []                     # not due yet: not even a row
    s.go_to(DUE + 600)
    assert s.tick() == SETTINGS.check_every_min * 60
    report = s.report()
    assert report.status == DONE and report.markdown == '# The week' and report.model == SETTINGS.model
    assert len(s.writer.calls) == 1
    prompt, data = s.writer.calls[0]
    assert prompt == WORDS.prompt and data.startswith('# Desk Talk data: Mon Oct 26 to Sun Nov 01, 2026')
    assert 'habit hydration/sip' in data
    s.tick()
    assert len(s.writer.calls) == 1                                        # a week is never written twice


def test_waits_for_catch_up_then_writes_after_wait_days():
    s = Setup()
    s.send(events=[sip('s1', at(28, 9))], at_ts=DUE - 100, caught_up=False)     # still sending when the week ended
    s.go_to(DUE + 600)
    s.tick()
    assert s.report().status == PENDING and s.writer.calls == []
    started = s.clock.now()                                                # the report is started on this first round
    s.go_to(started + SETTINGS.wait_for_pi_days * DAY - 1)
    s.tick()
    assert s.report().status == PENDING and s.writer.calls == []
    s.go_to(started + SETTINGS.wait_for_pi_days * DAY)
    s.tick()
    assert s.report().status == DONE
    data = s.writer.calls[0][1]
    assert 'had not finished sending' in data and 'Sun Nov 01 19:58' in data       # when the Pi was last heard from


def test_writes_as_soon_as_the_pi_catches_up_during_the_wait():
    s = Setup()
    s.send(events=[sip('s1', at(28, 9))], at_ts=DUE - 100, caught_up=False)
    s.go_to(DUE + 600)
    s.tick()
    s.send(at_ts=DUE + 3 * 3600)                                           # now it has caught up
    s.go_to(DUE + 3 * 3600 + 60)
    s.tick()
    assert s.report().status == DONE and 'had not finished sending' not in s.writer.calls[0][1]


def test_no_data_after_wait_no_llm_call():
    s = Setup()
    s.send(at_ts=DUE + 300)                                                # caught up, but nothing in the week
    s.go_to(DUE + 600)
    s.tick()
    report = s.report()
    assert report.status == NO_DATA and report.markdown is None and s.writer.calls == []


def test_no_data_waits_for_a_pi_that_has_not_caught_up():
    s = Setup()
    s.send(at_ts=DUE - 100, caught_up=False)
    s.go_to(DUE + 600)
    s.tick()
    assert s.report().status == PENDING
    s.go_to(DUE + 600 + SETTINGS.wait_for_pi_days * DAY)
    s.tick()
    assert s.report().status == NO_DATA and s.writer.calls == []


def test_a_pi_never_heard_from_gets_no_data_after_the_wait():
    s = Setup()
    s.go_to(DUE + 600)
    s.tick()
    assert s.report().status == PENDING
    s.go_to(DUE + 600 + SETTINGS.wait_for_pi_days * DAY)
    s.tick()
    assert s.report().status == NO_DATA and s.writer.calls == []


def test_written_late_after_sleep():
    s = Setup()
    s.send(events=[sip('s1', at(28, 9))], at_ts=DUE + 300)
    s.go_to(DUE + 2 * DAY + 5 * 3600)                                      # the computer was asleep until Tuesday 1 am
    s.tick()
    assert s.report().status == DONE and len(s.writer.calls) == 1


def test_only_latest_week_written_after_long_sleep():
    s = Setup()
    later_due = DUE + 14 * DAY
    s.send(events=[sip('s1', later_due - DAY)], at_ts=later_due + 100)
    s.go_to(later_due + 3600)                                              # asleep through two Sundays
    s.tick()
    weeks = [r.week_start for r in s.store.list_reports(s.ann.id)]
    assert weeks == ['2026-11-09'] and len(s.writer.calls) == 1


def test_device_linked_after_due_gets_no_report_yet():
    s = Setup()
    s.go_to(DUE + 3600)
    late = link_device(s.parts, 'bob').device                              # linked an hour after the Sunday
    s.send(late, at_ts=DUE + 3700)
    s.send(at_ts=DUE + 3700)
    s.tick()
    assert s.store.list_reports(late.id) == []
    assert [r.week_start for r in s.store.list_reports(s.ann.id)] == [WEEK]
    s.go_to(DUE + 7 * DAY + 3600)
    s.send(late, events=[sip('s2', DUE + 3 * DAY)], at_ts=DUE + 7 * DAY + 3000)
    s.send(at_ts=DUE + 7 * DAY + 3000)
    s.tick()
    assert [r.week_start for r in s.store.list_reports(late.id)] == ['2026-11-02']


def test_a_removed_pi_gets_no_report():
    s = Setup()
    s.send(events=[sip('s1', at(28, 9))], at_ts=DUE + 300)
    s.store.remove_device(s.ann.id, DUE + 400)
    s.go_to(DUE + 600)
    s.tick()
    assert s.store.list_reports(s.ann.id) == [] and s.writer.calls == []


def test_each_pi_is_due_in_its_own_time_zone():
    s = Setup()
    tokyo = link_device(s.parts, 'kei', time_zone='Asia/Tokyo').device         # Sunday 20:00 in Tokyo is 11:00 UTC
    for pi in (s.ann, tokyo):
        s.send(pi, events=[sip(f's-{pi.id}', at(28, 9))], at_ts=DUE + 9 * 3600)
    s.go_to(DUE - 8 * 3600)                                                # Sunday 12:00 UTC: Tokyo's Sunday is over
    s.tick()
    assert s.row(s.ann) is None
    assert s.report(tokyo).status == DONE


# ── when it goes wrong ───────────────────────────────────────────────────────────────────────────────────────────
def test_failure_retries_then_failed():
    s = Setup()
    s.writer.fail_with = 'The AI company is busy.'
    s.send(events=[sip('s1', at(28, 9))], at_ts=DUE + 300)
    s.go_to(DUE + 600)
    s.tick()
    first = s.report()
    assert (first.status, first.tries, first.error) == (PENDING, 1, 'The AI company is busy.')
    assert first.next_try_at == DUE + 600 + SETTINGS.retry_first_wait_min * 60
    s.tick()
    assert len(s.writer.calls) == 1                                        # too soon: it waits
    s.go_to(first.next_try_at)
    s.tick()
    second = s.report()
    assert (second.status, second.tries) == (PENDING, 2)
    assert first.next_try_at is not None
    assert second.next_try_at == first.next_try_at + SETTINGS.retry_first_wait_min * 60 * SETTINGS.retry_growth
    s.go_to(second.next_try_at)
    s.tick()
    last = s.report()
    assert last.status == FAILED and last.error == 'The AI company is busy.' and last.markdown is None
    assert len(s.writer.calls) == SETTINGS.max_tries
    s.go_to(DUE + 3 * DAY)
    s.tick()
    assert len(s.writer.calls) == SETTINGS.max_tries                       # a failed week waits for the admin


def test_a_retry_that_works_finishes_the_report():
    s = Setup()
    s.writer.fail_with = 'The AI company is busy.'
    s.send(events=[sip('s1', at(28, 9))], at_ts=DUE + 300)
    s.go_to(DUE + 600)
    s.tick()
    s.writer.fail_with = None
    s.go_to(s.report().next_try_at)
    s.tick()
    assert s.report().status == DONE and s.report().markdown == '# The week'


def test_no_key_marks_failed_with_plain_message():
    message = CONFIG.messages.report_no_key

    class NoKey:
        calls = 0

        def write(self, prompt: str, data: str) -> str:
            NoKey.calls += 1
            raise ReportWillNotWork(message)

    s = Setup(writer=NoKey())
    s.send(events=[sip('s1', at(28, 9))], at_ts=DUE + 300)
    s.go_to(DUE + 600)
    s.tick()
    report = s.report()
    assert report.status == FAILED and report.error == message and report.tries == 0
    s.go_to(DUE + DAY)
    s.tick()
    assert NoKey.calls == 1                                                # no point trying again


def test_a_bug_in_the_writer_counts_as_a_try_and_keeps_its_words_out_of_the_report(caplog):
    class Buggy:
        def write(self, prompt: str, data: str) -> str:
            raise ValueError('secret-internal-detail')

    s = Setup(writer=Buggy())
    s.send(events=[sip('s1', at(28, 9))], at_ts=DUE + 300)
    s.go_to(DUE + 600)
    with caplog.at_level(logging.WARNING):
        s.tick()
    report = s.report()
    assert (report.status, report.tries) == (PENDING, 1)
    assert report.error == WORDS.errors['unexpected']


def test_pending_row_finished_after_restart():
    s = Setup()
    s.send(events=[sip('s1', at(28, 9))], at_ts=DUE + 300)
    s.go_to(DUE + 600)
    assert s.store.create_report(s.ann.id, WEEK, DUE + 500) is not None        # a row left behind by a run that stopped
    fresh = ReportJob(s.store, s.writer, s.clock, SETTINGS, WORDS)
    fresh.tick(s.clock.now())
    assert s.report().status == DONE and len(s.writer.calls) == 1


def test_a_row_for_an_older_week_is_finished_for_that_week():
    s = Setup()
    old_due = DUE - 7 * DAY
    s.send(events=[sip('old', old_due - DAY)], at_ts=old_due + 300)
    s.go_to(DUE + 600)
    s.store.create_report(s.ann.id, '2026-10-19', old_due + 100)
    s.tick()
    assert s.report(week='2026-10-19').status == DONE
    assert 'Mon Oct 19 to Sun Oct 25, 2026' in s.writer.calls[0][1]


class FlakyStore(MemoryStore):
    """A store that fails when asked for one Pi's events."""

    def __init__(self) -> None:
        super().__init__()
        self.broken: set[str] = set()

    def events_between(self, device_id, start, end):
        if device_id in self.broken:
            raise RuntimeError('this one Pi is broken')
        return super().events_between(device_id, start, end)


def test_one_bad_pi_does_not_stop_others(caplog):
    store = FlakyStore()
    s = Setup(store=store)
    bob = link_device(s.parts, 'bob').device
    odd = link_device(s.parts, 'cy', time_zone='Nowhere/Land').device            # a time zone the server doesn't know
    store.broken.add(s.ann.id)
    for pi in (s.ann, bob, odd):
        s.send(pi, events=[sip(f's-{pi.id}', at(28, 9))], at_ts=DUE + 300)
    s.go_to(DUE + 600)
    with caplog.at_level(logging.WARNING):
        s.tick()
    assert s.report(bob).status == DONE
    assert s.report(odd).status == DONE                                        # worked out in UTC
    assert s.report().status == PENDING and s.report().tries == 1
    assert 'Nowhere/Land' in caplog.text
    store.broken.clear()
    s.go_to(s.report().next_try_at)
    s.tick()
    assert s.report().status == DONE
    assert len(s.writer.calls) == 3


def test_a_problem_with_the_store_for_one_pi_does_not_stop_the_round(caplog):
    class Down(MemoryStore):
        def create_report(self, device_id, week_start, now):
            if device_id == self.down:
                raise RuntimeError('database went away')
            return super().create_report(device_id, week_start, now)

        down = ''

    store = Down()
    s = Setup(store=store)
    bob = link_device(s.parts, 'bob').device
    store.down = s.ann.id
    for pi in (s.ann, bob):
        s.send(pi, events=[sip(f's-{pi.id}', at(28, 9))], at_ts=DUE + 300)
    s.go_to(DUE + 600)
    with caplog.at_level(logging.WARNING):
        s.tick()
    assert s.report(bob).status == DONE and s.row() is None


def test_a_report_that_will_not_work_is_still_a_report_failed():
    assert isinstance(ReportWillNotWork('x'), ReportFailed)


def daily(day: str, **totals) -> dict[str, Any]:
    return {'id': f'daily-{day}', 'ts': DUE, 'type': 'daily_totals', 'day': day, 'totals': totals}


def test_a_week_with_only_the_days_numbers_is_written():
    s = Setup()
    s.store.save_batch(s.ann.id, [], [daily('2026-10-28', desk_sec=3600, water_ml=800)], [], True, DUE + 300)
    s.go_to(DUE + 600)
    s.tick()
    assert s.report().status == DONE
    assert '| Wed 10-28 | 60 |' in s.writer.calls[0][1]


def test_a_week_of_all_zero_numbers_and_nothing_else_is_no_data():
    s = Setup()
    s.store.save_batch(s.ann.id, [], [daily('2026-10-28', desk_sec=0, water_ml=0)], [], True, DUE + 300)
    s.go_to(DUE + 600)
    s.tick()
    assert s.report().status == NO_DATA and s.writer.calls == []


# ── fix round 1 ──────────────────────────────────────────────────────────────────────────────────────────────────
def test_a_server_that_slept_waits_for_a_pi_that_has_not_sent_yet():
    s = Setup()
    s.send(at_ts=at(21, 12))                                               # it sent on Wednesday, then held the rest
    s.go_to(DUE + 3 * DAY)                                                 # the server wakes on Wednesday evening
    s.tick()
    assert s.report().status == PENDING and s.writer.calls == []           # not "no data": the Pi has not had its say
    s.send(events=[sip('late', at(30, 9))], at_ts=DUE + 3 * DAY + 3600)
    s.go_to(DUE + 3 * DAY + 3700)
    s.tick()
    assert s.report().status == DONE and 'habit hydration/sip' in s.writer.calls[0][1]


def test_the_wait_counts_from_when_the_server_started_the_report_if_that_is_later():
    s = Setup()
    s.go_to(DUE + 3 * DAY)
    s.tick()
    s.go_to(DUE + 3 * DAY + SETTINGS.wait_for_pi_days * DAY - 1)
    s.tick()
    assert s.report().status == PENDING
    s.go_to(DUE + 3 * DAY + SETTINGS.wait_for_pi_days * DAY)
    s.tick()
    assert s.report().status == NO_DATA


def test_the_first_evening_of_the_week_is_in_the_data():
    s = Setup()
    sunday = {'id': 'daily-2026-10-25', 'ts': at(26, 1), 'type': 'daily_totals', 'day': '2026-10-25',
              'totals': {'desk_sec': 7200, 'hour_21': 3600}}
    s.store.save_batch(s.ann.id, [sip('s1', at(28, 9))], [sunday], [], True, DUE + 300)
    s.go_to(DUE + 600)
    s.tick()
    assert 'this week starts at 20:00' in s.writer.calls[0][1] and '| 120 | 21h |' in s.writer.calls[0][1]


def test_a_week_with_only_the_first_evenings_numbers_has_no_data():
    s = Setup()
    sunday = {'id': 'daily-2026-10-25', 'ts': at(26, 1), 'type': 'daily_totals', 'day': '2026-10-25',
              'totals': {'desk_sec': 7200}}
    s.store.save_batch(s.ann.id, [], [sunday], [], True, DUE + 300)        # a day mostly in last week
    s.go_to(DUE + 600)
    s.tick()
    assert s.report().status == NO_DATA and s.writer.calls == []


class _Reply:
    def __init__(self, text: str) -> None:
        self.text, self.calls = text, 0

    def write(self, prompt: str, data: str) -> str:
        self.calls += 1
        return self.text


def test_a_reply_a_store_cannot_keep_is_a_failed_try():
    writer = _Reply('# Week\x00 with a NUL')
    s = Setup(writer=writer)
    s.send(events=[sip('s1', at(28, 9))], at_ts=DUE + 300)
    s.go_to(DUE + 600)
    s.tick()
    report = s.report()
    assert (report.status, report.tries, report.markdown) == (PENDING, 1, None)
    assert report.error == WORDS.errors['bad_reply']


def test_a_store_that_fails_when_saving_the_report_counts_as_a_try():
    class CannotSave(MemoryStore):
        def finish_report(self, report_id, status, markdown, model, error, now):
            if status == DONE:
                raise RuntimeError('database went away')
            return super().finish_report(report_id, status, markdown, model, error, now)

    s = Setup(store=CannotSave())
    s.send(events=[sip('s1', at(28, 9))], at_ts=DUE + 300)
    s.go_to(DUE + 600)
    s.tick()
    assert (s.report().status, s.report().tries) == (PENDING, 1)
    s.tick()
    assert len(s.writer.calls) == 1                                        # it waits before paying for another
