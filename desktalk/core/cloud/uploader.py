"""
Sends the habit events that may leave this computer to a linked cloud server, in the background. Until the computer
is linked (scripts/link_cloud.py) it does nothing at all: no call, no thread of its own, no log line.

    uploader = CloudUploader(link_factory, event_log, store, preferences, goals, config.cloud, clock, words)
    supervisor.add(uploader)          # a PeriodicWorker: each round sends what is new, then waits

Each round it:
  1. reads the link from the store (the server's address, this device's id and token, and the place to send from);
  2. reads the events written since that place from the event log, oldest first. The event log is the waiting
     list, so there is no second copy: with no Wi-Fi the place just stops moving and the sender catches up later
     (days older than event_log.keep_days are gone by then, and that is accepted);
  3. runs each through the one filter (desktalk_shared.cloud_api.to_cloud_event), which drops what must stay home;
  4. sends up to cloud.batch_size of them, with `caught_up` on the batch that empties the backlog. Your goals and
     the day's numbers (desk time, water ...) ride along when they are due.
The link is saved as {server, device_id, token, place_ts, start_ts, recent_ids, stopped, last_sent_ts, goals_sent,
totals_sent}. Nothing before `start_ts` (the moment of linking) is ever sent, so old history does not go by surprise.
Each round looks `overlap_sec` back from the place, so an event written a little late still goes; `recent_ids` stops
those looked-at-again events from going twice.

A failure never raises: the place does not move and the next try waits longer (Backoff). The server saying it doesn't
know us (401) stops sending until the computer is linked again. The token is never logged.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable, Mapping
from typing import Any

from desktalk.config import CloudSettings
from desktalk.config.prompts import CloudWords
from desktalk.core.habits.habit_days import day_key
from desktalk.domain import CloudNotLinked, CloudUnavailable, plain_error
from desktalk.ports import Clock, CloudLink, EventStore, KeyValueStore, Preferences
from desktalk_shared.backoff import Backoff
from desktalk_shared.cloud_api import IngestReply, daily_totals_event, goals_event, to_cloud_event

log = logging.getLogger(__name__)

MINUTE, HOUR, DAY = 60, 3600, 86_400

# The numbers of a habit day (data/habits/<day>.json, core/habits/habit_days.py) that go as the day's totals. The
# desk seconds per hour go as hour_00 ... hour_23.
TOTALS = ('desk_sec', 'water_ml', 'sips', 'breaks', 'longest_sit_sec', 'phone_free_sec', 'phone_on_desk_sec',
          'sunlight_minutes', 'nudges_sent')
HOURLY = 'hourly_activity'

LinkFactory = Callable[[str, str], CloudLink]       # (the server's address, the token) → the line to that server


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _number_or(value: Any, fallback: float) -> float:
    return float(value) if _is_number(value) else fallback


def _when(entry: Any, signature: str | None = None) -> float | None:
    """The `ts` in a remembered {sig, ts} (goals_sent, one day of totals_sent), if it is one (with that `sig`, when
    one is given)."""
    if not isinstance(entry, dict) or not _is_number(entry.get('ts')):
        return None
    return float(entry['ts']) if signature is None or entry.get('sig') == signature else None


class _Pending:
    """One event the sender made itself (the goals, a day's numbers), and what to remember once it is sent."""

    def __init__(self, event: dict[str, Any], remember: Callable[[dict[str, Any]], None]):
        self.event = event
        self.remember = remember


class CloudUploader:
    """The CloudSending job (desktalk/ports.py).

    link_factory: makes the line to a server from its address and the token.
    events: the event log. store: where the link is saved (settings → cloud.link_key) and the habit days are read.
    preferences: the wake and bed times. goals: the goal numbers (from config/protocols.yaml).
    words: the log lines (prompts.yaml → cloud)."""

    name = 'cloud uploader'

    def __init__(self, link_factory: LinkFactory, events: EventStore, store: KeyValueStore, preferences: Preferences,
                 goals: Mapping[str, float], settings: CloudSettings, clock: Clock, words: CloudWords):
        self._factory = link_factory
        self._events = events
        self._store = store
        self._prefs = preferences
        self._goals = goals
        self._s = settings
        self._clock = clock
        self._words = words
        self._backoff = Backoff(settings.retry_start_sec, settings.retry_max_sec)
        self._line: tuple[tuple[str, str], CloudLink] | None = None     # the line made for the current link
        self._lock = threading.Lock()                                   # status() is read from the web server's thread
        self._waiting = 0

    # ── the job ──
    def tick(self, now: float) -> float:
        link = self._linked()
        if link is None or link.get('stopped') is True:
            return self._s.idle_check_every_sec
        try:
            self._round(link, now)
        except CloudNotLinked:
            self._stop(link)
            return self._s.idle_check_every_sec
        except CloudUnavailable as exc:
            wait = self._backoff.next()
            log.warning(self._words.log_unreachable.format(error=str(exc) or plain_error(exc), seconds=wait))
            return wait
        self._backoff.reset()
        return self._s.send_every_sec

    def status(self) -> dict[str, Any]:
        link = self._linked()
        if link is None:
            return {'linked': False, 'stopped': False, 'last_sent_ts': None, 'waiting': 0}
        with self._lock:
            waiting = self._waiting
        last = link.get('last_sent_ts')
        return {'linked': True, 'stopped': link.get('stopped') is True,
                'last_sent_ts': last if _is_number(last) else None, 'waiting': waiting}

    # ── the link ──
    def _linked(self) -> dict[str, Any] | None:
        """The saved link, or None when this computer isn't linked (nothing saved, or no address or token)."""
        saved = self._store.load(self._s.link_key)
        if not isinstance(saved, dict):
            return None
        if not all(isinstance(saved.get(k), str) and saved[k] for k in ('server', 'token')):
            return None
        return saved

    def _line_to(self, link: Mapping[str, Any]) -> CloudLink:
        key = (link['server'], link['token'])
        if self._line is None or self._line[0] != key:
            self._line = (key, self._factory(*key))
        return self._line[1]

    def _save(self, used: Mapping[str, Any], changes: Mapping[str, Any]) -> bool:
        """Add `changes` to the saved link, unless the computer was linked again while this round was out (then the
        new link wins and the round's changes are dropped)."""
        current = self._linked()
        if current is None or any(current.get(k) != used.get(k) for k in ('server', 'device_id', 'token')):
            return False
        self._store.save(self._s.link_key, {**current, **changes})
        return True

    def _stop(self, used: Mapping[str, Any]) -> None:
        if self._save(used, {'stopped': True}):
            log.warning(self._words.log_not_linked)

    # ── one round ──
    def _round(self, link: dict[str, Any], now: float) -> None:
        place = _number_or(link.get('place_ts'), now)
        start = _number_or(link.get('start_ts'), place)
        recent = {i for i in link.get('recent_ids') or [] if isinstance(i, str)}
        found = self._read_log(max(place - self._s.overlap_sec, start))
        fresh = [(ts, event_id, event) for ts, event_id, event in found if event_id not in recent]
        sendable = [(ts, event_id, sent) for ts, event_id, event in fresh
                    if (sent := to_cloud_event(event)) is not None]
        batch = sendable[:self._s.batch_size]
        caught_up = len(sendable) <= self._s.batch_size
        extras = self._extras(link, now)

        self._set_waiting(len(sendable))
        if not batch and not extras:
            moved = self._next_place(link, found, [], place, caught_up=True)
            if moved['place_ts'] != place or moved['recent_ids'] != list(link.get('recent_ids') or []):
                self._save(link, moved)
            return
        sent = [p.event for p in extras] + [event for _, _, event in batch]
        reply = self._line_to(link).send(sent, caught_up=caught_up)
        self._set_waiting(len(sendable) - len(batch))
        self._note_refused(reply)

        changes: dict[str, Any] = {'last_sent_ts': now}
        for pending in extras:
            pending.remember(changes)
        changes.update(self._next_place(link, found, batch, place, caught_up=caught_up))
        self._save(link, changes)

    def _note_refused(self, reply: IngestReply) -> None:
        if reply.refused:
            reasons = '; '.join(sorted({r.why for r in reply.refused}))
            log.warning(self._words.log_refused.format(count=len(reply.refused), reasons=reasons))

    def _next_place(self, link: Mapping[str, Any], found: list[tuple[float, str, dict[str, Any]]],
                    batch: list[tuple[float, str, dict[str, Any]]], place: float, *,
                    caught_up: bool) -> dict[str, Any]:
        """The saved changes that say where the next round starts. When it caught up, past everything it read (what
        stays home too); else past the last event sent. `recent_ids` keeps the ids still inside the overlap, so a
        round that looks back does not send them twice. (Refused events count as sent: asking again won't help.)"""
        new_place = max([place, *(ts for ts, _, _ in found)]) if caught_up else max(place, batch[-1][0])
        kept = {i for i in link.get('recent_ids') or [] if isinstance(i, str)} | {i for _, i, _ in batch}
        recent = [event_id for ts, event_id, _ in found if event_id in kept and ts >= new_place - self._s.overlap_sec]
        return {'place_ts': new_place, 'recent_ids': recent}

    def _set_waiting(self, count: int) -> None:
        with self._lock:
            self._waiting = count

    # ── reading the event log ──
    def _read_log(self, since: float) -> list[tuple[float, str, dict[str, Any]]]:
        """(ts, id, event) for every event written at or after `since`, oldest first. It reads the days from one
        day before `since` on, so a place on a day that was deleted just carries on from the oldest day left."""
        first_day = self._clock.local_day(since - DAY)
        found: list[tuple[float, str, dict[str, Any]]] = []
        for day in sorted(d for d in self._events.days() if d >= first_day):
            for event in reversed(self._events.events(day, None, None)):         # the log gives newest first
                ts, event_id = _number_or(event.get('ts'), float('-inf')), event.get('id')
                if ts >= since and isinstance(event_id, str):
                    found.append((ts, event_id, event))
        found.sort(key=lambda item: item[0])
        return found

    # ── the goals and the day's numbers ──
    def _extras(self, link: Mapping[str, Any], now: float) -> list[_Pending]:
        return [*self._goals_due(link, now), *self._totals_due(link, now)]

    def _goals_due(self, link: Mapping[str, Any], now: float) -> list[_Pending]:
        wake, bed = self._prefs.get('wake_time'), self._prefs.get('bed_time')
        goals = dict(self._goals)
        signature = json.dumps([wake, bed, goals], sort_keys=True)
        last = _when(link.get('goals_sent'), signature)
        if last is not None and now - last < self._s.goals_every_hours * HOUR:
            return []
        event = to_cloud_event(goals_event(now, wake=str(wake or ''), bed=str(bed or ''), goals=goals))
        if event is None:
            return []
        return [_Pending(event, lambda changes: changes.update(goals_sent={'sig': signature, 'ts': now}))]

    def _totals_due(self, link: Mapping[str, Any], now: float) -> list[_Pending]:
        saved = link.get('totals_sent')
        sent_before: dict[str, Any] = saved if isinstance(saved, dict) else {}
        days = [self._clock.local_day(now - DAY), self._clock.local_day(now)]
        pending: list[_Pending] = []
        remembered = {day: sent_before[day] for day in days if day in sent_before}

        def remember(changes: dict[str, Any], day: str, signature: str) -> None:
            remembered[day] = {'sig': signature, 'ts': now}
            changes['totals_sent'] = dict(remembered)

        for day in days:
            numbers = self._store.load(day_key(day))
            if not isinstance(numbers, dict) or numbers.get('day') != day:
                continue
            totals = self._totals(numbers)
            signature = json.dumps(totals, sort_keys=True)
            before = sent_before.get(day)
            if _when(before, signature) is not None:
                continue                                    # nothing changed since it was sent
            last = _when(before)
            if last is not None and now - last < self._s.totals_every_min * MINUTE:
                continue                                    # changed, but sent too lately
            event = to_cloud_event(daily_totals_event(now, day, totals))
            if event is not None:
                pending.append(_Pending(event, lambda changes, day=day, signature=signature:
                                        remember(changes, day, signature)))
        return pending

    @staticmethod
    def _totals(saved: Mapping[str, Any]) -> dict[str, float]:
        totals = {name: saved[name] for name in TOTALS if _is_number(saved.get(name))}
        hours = saved.get(HOURLY)
        if isinstance(hours, list):
            totals.update({f'hour_{hour:02d}': value for hour, value in enumerate(hours) if _is_number(value)})
        return totals
