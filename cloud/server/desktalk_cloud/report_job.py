"""
The weekly report job. Every few minutes it looks at each linked Pi: when a week's report has come due (in the Pi's
own time zone) it starts one, waits for a Pi that has not finished sending, and then has an AI write it from the Pi's
data. A report that fails is tried again a few times, then marked failed (the admin page can run it again).

One Pi's trouble never stops another's report. A report is never written twice (the store keeps one row per Pi and
week), and after a long sleep only the newest week is started. A row left pending by a run that stopped is finished by
the next run. Settings: cloud/settings.yaml → reports. Words: cloud/prompts.yaml → report.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from typing import Any

from desktalk_cloud.config import ReportSettings
from desktalk_cloud.domain import DONE, FAILED, NO_DATA, Device, Report, is_clean_text
from desktalk_cloud.ports import Clock, ReportFailed, ReportWriter, Store
from desktalk_cloud.report_data import ReportWords, build_week
from desktalk_cloud.report_errors import ReportWillNotWork
from desktalk_cloud.report_times import DAYS_IN_WEEK, due_for_week, latest_due, week_of, zone_of

__all__ = ['ReportJob']

log = logging.getLogger(__name__)

SECONDS_IN_MINUTE, SECONDS_IN_DAY = 60, 86_400
GOALS, CAMERA = 'goals', 'camera'


def _has_numbers(event: Mapping[str, Any]) -> bool:
    totals = event.get('totals')
    return isinstance(totals, Mapping) and any(isinstance(v, (int, float)) and v != 0 for v in totals.values())


class ReportJob:
    """A PeriodicWorker (desktalk-style): tick() does one round and says how many seconds until the next."""

    name = 'weekly report'

    def __init__(self, store: Store, writer: ReportWriter, clock: Clock, settings: ReportSettings,
                 prompts: ReportWords) -> None:
        self._store, self._writer, self._clock, self._settings, self._words = store, writer, clock, settings, prompts

    def tick(self, now: float) -> float:
        devices = {d.id: d for d in self._store.list_all_devices() if d.removed_at is None}
        for device in devices.values():
            self._guarded(device.id, self._start_week, device, now)
        for report in self._store.reports_to_write(now):
            device = devices.get(report.device_id)
            if device is not None:
                self._guarded(device.id, self._write, report, device, now)
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

    # ── writing one ──
    def _ready(self, device: Device, report: Report, end: float, now: float) -> bool:
        """The Pi has sent everything up to the end of the week, or the wait for it has run out. The wait counts from
        the end of the week or from when the server started this report, whichever is later: a server that slept past
        the end of the week gives the Pi its days to catch up from the moment the server is awake to hear it."""
        if device.caught_up_at is not None and device.caught_up_at >= end:
            return True
        return now >= max(end, report.created_at) + self._settings.wait_for_pi_days * SECONDS_IN_DAY

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
        events = self._store.events_between(device.id, start, end)
        totals = self._store.totals_between(device.id, start_day, last_day)
        # (the first evening's day is mostly last week's, so its numbers don't count as this week having data)
        week_totals = [t for t in totals if str(t.get('day')) >= first_day]
        if not any(e.get('type') != GOALS for e in events) and not any(_has_numbers(t) for t in week_totals):
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
            wait = settings.retry_first_wait_min * SECONDS_IN_MINUTE * settings.retry_growth ** report.tries
            self._store.retry_report(report.id, error, now + wait)
