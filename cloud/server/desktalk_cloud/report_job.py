"""
The weekly report job. Every few minutes it looks at each linked Pi: when a week's report has come due (in the Pi's
own time zone) it starts one, waits for a Pi that has not finished sending, and then has an AI write it from the Pi's
data. A report that fails is tried again a few times, each wait cut by up to reports.retry_jitter of it by chance, then
marked failed (the admin page can run it again).

One Pi's trouble never stops another's report. A report is never written twice (the store keeps one row per Pi and
week), and after a long sleep only the newest week is started. A row left pending by a run that stopped is finished by
the next run, and one whose Pi was removed is marked failed without asking the AI. A stop is looked for before each
report, so a stop can't outlast the grace period by starting another. Settings: cloud/settings.yaml → reports. Words:
cloud/prompts.yaml → report.
"""

from __future__ import annotations

import logging
import random
from collections.abc import Callable, Mapping
from datetime import date, datetime, timedelta
from typing import Any

from desktalk_cloud.config import ReportSettings
from desktalk_cloud.domain import DONE, FAILED, NO_DATA, Device, Report, is_clean_text
from desktalk_cloud.ports import Clock, ReportFailed, ReportWriter, Store
from desktalk_cloud.report_data import ReportWords, build_week
from desktalk_cloud.report_errors import ReportWillNotWork
from desktalk_cloud.report_times import DAYS_IN_WEEK, due_for_week, latest_due, week_of, zone_of
from desktalk_shared.backoff import Chance, jittered

__all__ = ['ReportJob']

log = logging.getLogger(__name__)

SECONDS_IN_MINUTE, SECONDS_IN_DAY = 60, 86_400
MISSED_ROUNDS = 2             # this many rounds with no tick at all means the server was asleep or stopped, not slow
GOALS, CAMERA = 'goals', 'camera'


def _has_numbers(event: Mapping[str, Any]) -> bool:
    totals = event.get('totals')
    return isinstance(totals, Mapping) and any(isinstance(v, (int, float)) and v != 0 for v in totals.values())


def _counts_for_the_week(event: Mapping[str, Any], first_day: str, end_day: str, end: float) -> bool:
    """Whether a day's numbers show this week had data. It goes by the day, because `ts` is when they were sent: a day
    wholly inside the week counts whenever it came. The first evening is mostly last week's, so it doesn't. The day the
    week ends on counts only if its numbers were sent by the end, since later ones could be all from after it."""
    day = str(event.get('day'))
    return day >= first_day and (day != end_day or event.get('ts', 0) <= end)


class ReportJob:
    """A PeriodicWorker (desktalk-style): tick() does one round and says how many seconds until the next."""

    name = 'weekly report'

    def __init__(self, store: Store, writer: ReportWriter, clock: Clock, settings: ReportSettings,
                 prompts: ReportWords, should_stop: Callable[[], bool] = lambda: False, *,
                 rng: Chance | None = None) -> None:
        self._store, self._writer, self._clock, self._settings, self._words = store, writer, clock, settings, prompts
        self._rng = rng or random.Random()              # for the jitter (tests pass one that gives set numbers)
        self._should_stop = should_stop
        self._awake_since: float | None = None       # when this server last started or woke up (kept in memory only)
        self._last_round_end: float | None = None

    def _note_if_woken(self, now: float) -> None:
        """Remember when the server started, or woke up after missing rounds. A Pi that could not send while the server
        slept gets reports.wake_grace_min from then to send before the server gives up waiting for it."""
        every = self._settings.check_every_min * SECONDS_IN_MINUTE
        if self._last_round_end is None or now - self._last_round_end > MISSED_ROUNDS * every:
            self._awake_since = now

    def tick(self, now: float) -> float:
        self._note_if_woken(now)
        try:
            return self._round(now)
        finally:
            self._last_round_end = max(now, self._clock.now())

    def _round(self, now: float) -> float:
        devices = {d.id: d for d in self._store.list_all_devices()}
        for device in devices.values():
            if device.removed_at is None:
                self._guarded(device.id, self._start_week, device, now)
        for report in self._store.reports_to_write(now):
            if self._should_stop():
                break                                # the next run finishes the rest
            device = devices.get(report.device_id)
            if device is None:
                continue
            step = self._give_up_on if device.removed_at is not None else self._write
            self._guarded(device.id, step, report, device, now)
        return self._settings.check_every_min * SECONDS_IN_MINUTE

    def _guarded(self, device_id: str, step, *args) -> None:
        try:
            step(*args)
        except Exception:
            log.exception('The weekly report for Pi %s hit a problem; the other Pis go on', device_id)

    def _week(self, due: float, zone) -> tuple[float, float, str]:
        return week_of(due, zone, self._settings.weekday, self._settings.hour)

    # ── starting a week ──
    def _start_week(self, device: Device, now: float) -> None:
        """Make this week's pending report once it has come due. A Pi linked after the due moment waits for the next."""
        zone = zone_of(device.time_zone)
        due = latest_due(now, zone, self._settings.weekday, self._settings.hour)
        if device.created_at <= due:
            self._store.create_report(device.id, self._week(due, zone)[2], now)

    def _give_up_on(self, report: Report, _device: Device, _now: float) -> None:
        """A removed Pi's report: nobody is waiting for it, and linking the Pi again must not get it written and paid
        for."""
        self._store.finish_report(report.id, FAILED, None, None, self._words.errors['pi_removed'], self._clock.now())

    # ── writing one ──
    def _ready(self, device: Device, report: Report, end: float, now: float) -> bool:
        """The Pi has sent everything up to the end of the week, or the wait for it has run out. The wait counts from
        the end of the week or from when the server started this report, whichever is later (a re-run by the admin keeps
        the first time, so it is written at once). And the server must have been awake for reports.wake_grace_min: one
        that slept past the end of the wait gives the Pi time to send once it can hear it."""
        if device.caught_up_at is not None and device.caught_up_at >= end:
            return True
        waited = now >= max(end, report.created_at) + self._settings.wait_for_pi_days * SECONDS_IN_DAY
        grace = self._settings.wake_grace_min * SECONDS_IN_MINUTE
        awake = self._awake_since is not None and now >= self._awake_since + grace
        return waited and awake

    def _write(self, report: Report, device: Device, now: float) -> None:
        zone = zone_of(device.time_zone)
        due = due_for_week(report.week_start, zone, self._settings.weekday, self._settings.hour)
        week = self._week(due, zone)
        if not self._ready(device, report, week[1], now):
            return
        try:
            data = self._week_data(device, week, now)
            if data is None:
                self._store.finish_report(report.id, NO_DATA, None, None, None, self._clock.now())
                return
            markdown = self._writer.write(self._words.prompt, data)
            if not isinstance(markdown, str) or not is_clean_text(markdown):
                raise ReportFailed(self._words.errors['bad_reply'])         # text the store couldn't keep
            self._store.finish_report(report.id, DONE, markdown, self._settings.model, None, self._clock.now())
        except ReportWillNotWork as error:
            log.warning('The weekly report for Pi %s will not work: %s', device.id, error)
            self._store.finish_report(report.id, FAILED, None, None, str(error), self._clock.now())
        except ReportFailed as error:
            self._tried_and_failed(report, device, str(error))
        except Exception:
            log.exception('The weekly report for Pi %s hit a problem', device.id)
            self._tried_and_failed(report, device, self._words.errors['unexpected'])

    def _week_data(self, device: Device, week: tuple[float, float, str], now: float) -> str | None:
        """The data to write from, or None when the Pi sent nothing this week."""
        start, end, first_day = week
        zone = zone_of(device.time_zone)
        first = date.fromisoformat(first_day)
        last_day = (first + timedelta(days=DAYS_IN_WEEK - 1)).isoformat()
        start_day = datetime.fromtimestamp(start, zone).date().isoformat()      # the evening before the first whole day
        end_day = datetime.fromtimestamp(end, zone).date().isoformat()          # (ending at midnight: the next day)
        events = self._store.events_between(device.id, start, end)
        totals = self._store.totals_between(device.id, start_day, last_day)
        if not any(e.get('type') != GOALS for e in events) and not any(
                _has_numbers(t) and _counts_for_the_week(t, first_day, end_day, end) for t in totals):
            return None
        return build_week(device, events, totals, self._store.latest_event(device.id, GOALS),
                          self._store.object_states(device.id), self._store.latest_event(device.id, CAMERA), now, week,
                          self._words.data, self._settings)

    def _tried_and_failed(self, report: Report, device: Device, error: str) -> None:
        """Count a try that failed: wait longer and try again, or give up after reports.max_tries."""
        settings, now = self._settings, self._clock.now()
        attempt = report.tries + 1
        log.warning('The weekly report for Pi %s failed (try %d of %d): %s', device.id, attempt, settings.max_tries,
                    error)
        if attempt >= settings.max_tries:
            self._store.finish_report(report.id, FAILED, None, None, error, now)
        else:
            wait = jittered(settings.retry_first_wait_min * SECONDS_IN_MINUTE * settings.retry_growth ** report.tries,
                            settings.retry_jitter, self._rng)
            self._store.retry_report(report.id, error, now + wait)
