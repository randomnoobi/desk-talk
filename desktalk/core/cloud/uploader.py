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
  4. sends them, with `caught_up` on the batch that empties the backlog. Your goals and each day's numbers (desk
     time, water ...) ride along when they are due;
  5. with nothing to send, sends an empty upload with `caught_up` at most every `cloud.idle_beat_min` (unless a day's
     changed numbers are still waiting their turn), so an empty desk doesn't hold up the server's weekly report or
     leave an old "last seen" on its website.
Everything in one send, the goals and the day's numbers too, is at most cloud.batch_size events (the server refuses
more). The link is saved as {server, device_id, token, place_ts, start_ts, recent_ids, stopped, last_sent_ts,
goals_sent, totals_sent}; a link that is damaged (a bad address or token, odd numbers) counts as not linked.
Nothing before `start_ts` (the moment of linking) is ever sent, so old history does not go by surprise.
Each round looks `overlap_sec` back from the place, so an event written a little late still goes; `recent_ids` stops
those looked-at-again events from going twice.

A failure never raises: the place does not move and the next try waits longer (Backoff, cut by up to cloud.retry_jitter
by chance); a server that answers but says no to the upload is logged apart from one that is off. The server saying it
doesn't know us (401) stops sending until the computer is linked again. The token is never logged. The place never
goes past "now", so one event dated in the future (a clock that was ahead) can't make the sender skip what is written
later.
"""

from __future__ import annotations

import json
import logging
import math
import re
import threading
from collections.abc import Callable, Mapping
from datetime import date, timedelta
from typing import Any
from urllib.parse import urlsplit

from desktalk.config import CloudSettings
from desktalk.config.prompts import CloudWords
from desktalk.core.habits.habit_days import day_key
from desktalk.domain import CloudNotLinked, CloudRefused, CloudUnavailable, is_day, plain_error
from desktalk.ports import Clock, CloudLink, EventStore, KeyValueStore, Preferences
from desktalk_shared.backoff import Backoff, Chance
from desktalk_shared.cloud_api import IngestReply, daily_totals_event, goals_event, to_cloud_event

log = logging.getLogger(__name__)

MINUTE, HOUR, DAY = 60, 3600, 86_400

# The numbers of a habit day (data/habits/<day>.json, core/habits/habit_days.py) that go as the day's totals. The
# desk seconds per hour go as hour_00 ... hour_23. The Demo tab's pretend sips are tallied in the day too
# (demo_sips, demo_water_ml) and taken out of sips and water_ml here.
TOTALS = ('desk_sec', 'water_ml', 'sips', 'breaks', 'longest_sit_sec', 'phone_free_sec', 'phone_on_desk_sec',
          'sunlight_minutes', 'nudges_sent')
HOURLY = 'hourly_activity'
PRETEND = (('sips', 'demo_sips'), ('water_ml', 'demo_water_ml'))
USABLE_TOKEN = re.compile(r'[\x21-\x7e]+')     # printable ASCII, no space or line break: fit for a header

LinkFactory = Callable[[str, str], CloudLink]       # (the server's address, the token) → the line to that server


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_finite(value: Any) -> bool:
    try:
        return _is_number(value) and math.isfinite(value)
    except OverflowError:                               # a whole number too big for float maths
        return False


def _number_or(value: Any, fallback: float) -> float:
    return float(value) if _is_finite(value) else fallback


def _when(entry: Any, signature: str | None = None) -> float | None:
    """The `ts` in a remembered {sig, ts} (goals_sent, one day of totals_sent), if it is one (with that `sig`, when
    one is given)."""
    if not isinstance(entry, dict) or not _is_finite(entry.get('ts')):
        return None
    return float(entry['ts']) if signature is None or entry.get('sig') == signature else None


def _well_formed(saved: Any) -> bool:
    """A saved link this sender can work with: a web address, a token fit for a header, numbers that are real and a
    list of ids. (A link made by scripts/link_cloud.py always is; this is for one that was damaged or edited. The
    clock checks the times too: see CloudUploader._usable.)"""
    if not isinstance(saved, dict):
        return False
    server, token = saved.get('server'), saved.get('token')
    if not isinstance(server, str) or not isinstance(token, str) or not USABLE_TOKEN.fullmatch(token):
        return False
    try:
        parts = urlsplit(server)
        parts.port                                      # noqa: B018  (a bad port raises ValueError here)
    except ValueError:
        return False
    if parts.scheme not in ('http', 'https') or not parts.hostname or server != server.strip():
        return False
    if any(key in saved and not _is_finite(saved[key]) for key in ('place_ts', 'start_ts', 'last_sent_ts')):
        return False
    ids = saved.get('recent_ids', [])
    stopped = saved.get('stopped', False)
    return isinstance(ids, list) and all(isinstance(i, str) for i in ids) and isinstance(stopped, bool)


class _Pending:
    """One event the sender made itself (the goals, a day's numbers), and what to remember once it is sent."""

    def __init__(self, event: dict[str, Any], remember: Callable[[dict[str, Any]], None]):
        self.event = event
        self.remember = remember


def linked_server(store: KeyValueStore, link_key: str) -> tuple[str, str] | None:
    """(the server's address, the token) of the saved link, or None when there's no link fit to use. Read fresh each
    time, so a link made while the app runs counts at once."""
    saved = store.load(link_key)
    return (saved['server'], saved['token']) if _well_formed(saved) else None


class CloudUploader:
    """The CloudSending job (desktalk/ports.py).

    link_factory: makes the line to a server from its address and the token.
    events: the event log. store: where the link is saved (settings → cloud.link_key) and the habit days are read.
    preferences: the wake and bed times. goals: the goal numbers (from config/protocols.yaml).
    words: the log lines (prompts.yaml → cloud). rng: where the jitter's chance comes from (tests pass a fixed one)."""

    name = 'cloud uploader'

    def __init__(self, link_factory: LinkFactory, events: EventStore, store: KeyValueStore, preferences: Preferences,
                 goals: Mapping[str, float], settings: CloudSettings, clock: Clock, words: CloudWords, *,
                 rng: Chance | None = None):
        self._factory = link_factory
        self._events = events
        self._store = store
        self._prefs = preferences
        self._goals = goals
        self._s = settings
        self._clock = clock
        self._words = words
        self._backoff = Backoff(settings.retry_start_sec, settings.retry_max_sec, jitter=settings.retry_jitter, rng=rng)
        self._line: tuple[tuple[str, str], CloudLink] | None = None     # the line made for the current link
        self._lock = threading.Lock()                                   # status() is read from the web server's thread
        self._waiting = 0
        self._said_damaged = False

    # ── the job ──
    def tick(self, now: float) -> float:
        link = self._linked(say=True)
        if link is None or link.get('stopped') is True:
            return self._s.idle_check_every_sec
        try:
            self._round(link, now)
        except CloudNotLinked:
            self._stop(link)
            return self._s.idle_check_every_sec
        except CloudUnavailable as exc:
            wait = self._backoff.next()
            words = self._words.log_rejected if isinstance(exc, CloudRefused) else self._words.log_unreachable
            log.warning(words.format(error=str(exc) or plain_error(exc), seconds=wait))
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
                'last_sent_ts': last if _is_finite(last) else None, 'waiting': waiting}

    # ── the link ──
    def _day_of(self, ts: float) -> str | None:
        """The clock's day for a time, or None when the clock can't turn it into one (a time far too big or small)."""
        try:
            day = self._clock.local_day(ts)
        except (OverflowError, OSError, ValueError, TypeError):
            return None
        return day if is_day(day) else None

    def _usable(self, saved: Any) -> bool:
        return _well_formed(saved) and all(self._day_of(saved[key]) is not None
                                           for key in ('place_ts', 'start_ts', 'last_sent_ts') if key in saved)

    def _linked(self, say: bool = False) -> dict[str, Any] | None:
        """The saved link, or None when this computer isn't linked: nothing saved, or a link that can't be used (said
        once in the log, with `say`)."""
        saved = self._store.load(self._s.link_key)
        if saved is None:
            self._said_damaged = False
            return None
        if not self._usable(saved):
            if say and not self._said_damaged:
                log.warning(self._words.log_bad_link)
            self._said_damaged = self._said_damaged or say
            return None
        self._said_damaged = False
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
        recent = set(link.get('recent_ids', []))
        found = self._read_log(max(place - self._s.overlap_sec, start))
        sendable = [(ts, event_id, sent) for ts, event_id, event in found if event_id not in recent
                    if (sent := to_cloud_event(event)) is not None]
        # Everything in one send (goals and the day's numbers too) is at most batch_size: the server refuses more.
        # Those that don't fit go in later rounds, and events wait a round if they take all the room.
        extras, housekeeping, numbers_waiting = self._extras(link, now, start)
        extras = extras[:self._s.batch_size]
        room = self._s.batch_size - len(extras)
        batch = sendable[:room]
        caught_up = len(sendable) <= room

        self._set_waiting(len(sendable))
        if not batch and not extras:
            moved = self._next_place(link, found, [], place, now, caught_up=True)
            changes = {k: v for k, v in {**moved, **housekeeping}.items() if link.get(k) != v}
            if not numbers_waiting and self._idle_beat_due(link, now):
                self._note_refused(self._line_to(link).send([], caught_up=True))
                changes['last_sent_ts'] = now
            if changes:
                self._save(link, changes)
            return
        sent = [p.event for p in extras] + [event for _, _, event in batch]
        reply = self._line_to(link).send(sent, caught_up=caught_up)
        self._set_waiting(len(sendable) - len(batch))
        self._note_refused(reply)

        changes: dict[str, Any] = {'last_sent_ts': now}
        for pending in extras:
            pending.remember(changes)
        changes.update(housekeeping)
        changes.update(self._next_place(link, found, batch, place, now, caught_up=caught_up))
        self._save(link, changes)

    def _idle_beat_due(self, link: Mapping[str, Any], now: float) -> bool:
        """True when the server hasn't heard from this computer for cloud.idle_beat_min. (A last send dated in the
        future, from a clock that was ahead, doesn't count.)"""
        since_last = now - _number_or(link.get('last_sent_ts'), float('-inf'))
        return not 0 <= since_last < self._s.idle_beat_min * MINUTE

    def _note_refused(self, reply: IngestReply) -> None:
        if reply.refused:
            reasons = '; '.join(sorted({r.why for r in reply.refused}))
            log.warning(self._words.log_refused.format(count=len(reply.refused), reasons=reasons))

    def _next_place(self, link: Mapping[str, Any], found: list[tuple[float, str, dict[str, Any]]],
                    batch: list[tuple[float, str, dict[str, Any]]], place: float, now: float, *,
                    caught_up: bool) -> dict[str, Any]:
        """The saved changes that say where the next round starts. When it caught up, past everything it read (what
        stays home too); else past the last event sent (no move when none was sent). Never past `now`, so one event
        dated in the future can't make later ones be skipped. `recent_ids` keeps the ids still inside the overlap, so
        a round that looks back does not send them twice. (Refused events count as sent: asking again won't help.)"""
        if caught_up:
            new_place = max([place, *(ts for ts, _, _ in found)])
        else:
            new_place = max(place, batch[-1][0]) if batch else place
        new_place = min(new_place, max(now, place))
        kept = set(link.get('recent_ids', [])) | {i for _, i, _ in batch}
        recent = [event_id for ts, event_id, _ in found if event_id in kept and ts >= new_place - self._s.overlap_sec]
        return {'place_ts': new_place, 'recent_ids': recent}

    def _set_waiting(self, count: int) -> None:
        with self._lock:
            self._waiting = count

    # ── reading the event log ──
    def _read_log(self, since: float) -> list[tuple[float, str, dict[str, Any]]]:
        """(ts, id, event) for every event written at or after `since`, oldest first. It reads the days from one
        day before `since` on, so a place on a day that was deleted just carries on from the oldest day left."""
        first_day = self._day_of(since - DAY) or ''           # (a time no clock can use: look at every day)
        found: list[tuple[float, str, dict[str, Any]]] = []
        for day in sorted(d for d in self._events.days() if d >= first_day):
            for event in reversed(self._events.events(day, None, None)):         # the log gives newest first
                ts, event_id = _number_or(event.get('ts'), float('-inf')), event.get('id')
                if ts >= since and isinstance(event_id, str):
                    found.append((ts, event_id, event))
        found.sort(key=lambda item: item[0])
        return found

    # ── the goals and the day's numbers ──
    def _extras(self, link: Mapping[str, Any], now: float,
                start: float) -> tuple[list[_Pending], dict[str, Any], bool]:
        """What is due besides the log's events, goals first and then each day's numbers oldest first; the link's notes
        on the days' numbers (`totals_from`, `totals_sent`) as they should be saved; and whether a day's changed numbers
        are waiting their turn."""
        totals, housekeeping, numbers_waiting = self._totals_due(link, now, start)
        return [*self._goals_due(link, now), *totals], housekeeping, numbers_waiting

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

    def _days_to_look_at(self, link: Mapping[str, Any], now: float, start: float) -> list[str]:
        """Every day from `totals_from` (the first day whose numbers might still need sending; the day of linking at
        first, and never before it) to today: after an outage of days, each day's numbers still go."""
        today = self._day_of(now)
        first = self._day_of(start)
        saved = link.get('totals_from')
        if isinstance(saved, str) and is_day(saved) and first is not None:
            first = max(first, saved)
        if today is None or first is None:
            return [today] if today is not None else []
        try:
            day = date.fromisoformat(first)
        except ValueError:
            return [today]
        days = []
        while day.isoformat() < today:
            days.append(day.isoformat())
            day += timedelta(days=1)
        return [*days, today]

    def _totals_due(self, link: Mapping[str, Any], now: float,
                    start: float) -> tuple[list[_Pending], dict[str, Any], bool]:
        saved = link.get('totals_sent')
        sent_before: dict[str, Any] = saved if isinstance(saved, dict) else {}
        days = self._days_to_look_at(link, now, start)
        today = days[-1] if days else ''
        pending: list[_Pending] = []
        memory: dict[str, Any] = {}
        floor: str | None = None          # the first day not left behind: its numbers may still need sending
        numbers_waiting = False

        def remember(changes: dict[str, Any], day: str, signature: str) -> None:
            memory[day] = {'sig': signature, 'ts': now}
            changes['totals_sent'] = memory

        for day in days:
            numbers = self._store.load(day_key(day))
            before = sent_before.get(day)
            if not isinstance(numbers, dict) or numbers.get('day') != day:
                if day == today and floor is None:
                    floor = day
                continue                                    # no numbers for that day: nothing to send
            totals = self._totals(numbers)
            signature = json.dumps(totals, sort_keys=True)
            unchanged = _when(before, signature)
            last = _when(before)
            # A day is left behind once it is over and its numbers have matched what was sent for a while (so a late
            # save of its last numbers is still caught).
            over = day < today and unchanged is not None and now - unchanged >= self._s.totals_every_min * MINUTE
            if not over and floor is None:
                floor = day
            if before is not None:
                memory[day] = before
            if unchanged is not None:
                continue                                    # nothing changed since it was sent
            if last is not None and now - last < self._s.totals_every_min * MINUTE:
                numbers_waiting = True
                continue                                    # changed, but sent too lately
            event = to_cloud_event(daily_totals_event(now, day, totals))
            if event is not None:
                pending.append(_Pending(event, lambda changes, day=day, signature=signature:
                                        remember(changes, day, signature)))
        floor = floor or today
        for day in [d for d in memory if d < floor]:     # only the days before it are forgotten
            del memory[day]
        return pending, {'totals_from': floor, 'totals_sent': memory}, numbers_waiting

    @staticmethod
    def _totals(saved: Mapping[str, Any]) -> dict[str, float]:
        totals = {name: saved[name] for name in TOTALS if _is_finite(saved.get(name))}
        for name, pretend in PRETEND:                       # the Demo tab's pretend sips aren't the person's
            if name in totals and _is_finite(saved.get(pretend)):
                totals[name] = max(totals[name] - saved[pretend], 0)
        hours = saved.get(HOURLY)
        if isinstance(hours, list):
            totals.update({f'hour_{hour:02d}': value for hour, value in enumerate(hours) if _is_finite(value)})
        return totals
