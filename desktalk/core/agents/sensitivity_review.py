"""
The sensitivity slider moving by itself. Every settings → sensitivity_review.check_every_min it looks back over what
the camera saw (the event log's intervals, "the cup was in view from 14:02 to 14:31") and what those sightings
turned into (the object-state history), and moves the Settings page's slider (Preferences 'sensitivity') one step
when it's clear:

    too jumpy        things counted as put down and taken away again within blink_sec (not counting the wait
                     before "gone" counts) → one step pickier (a cup lifted for a sip is gone for a moment, not
                     here for a moment: that's real, not a blink)
    missing things   an object with a character of its own in view for missed_min_sec, sure enough for the next
                     easier step but not for this one, that didn't join the chat (and wasn't in it already), and
                     wasn't off the marked desk → one step easier

Both at once, or neither, and it leaves the slider alone. No AI: plain counting, on the Pi.

It starts fresh: the first look judges only what comes after it. It looks back at most look_back_hours, and never
past the last change: after you move the slider it waits wait_after_you_change_hours, and after it moves the slider
itself it needs min_watch_hours of watching again, and won't move it back the other way for wait_before_undo_hours.
The Settings page's "Adjust by itself" switch (Preferences 'auto_sensitivity') turns it off. What it remembers
(the step it last saw, where its stretch starts, the moves it made) is saved under 'sensitivity_review'.
"""

from __future__ import annotations

import dataclasses
import logging
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from desktalk.config import AgentsSettings, ObjectStatesSettings, SensitivityReviewSettings, SensitivityStep
from desktalk.core.agents.roster import OnDesk
from desktalk.core.agents.sensitivity import confidence_needed
from desktalk.domain import AgentJoined, AgentLeft, SensitivityVerdict
from desktalk.domain.object_states import StateRecord
from desktalk.ports import (
    Clock,
    EventStore,
    KeyValueStore,
    ObjectStateStore,
    Preferences,
    PresenceHistory,
    Profiles,
)

log = logging.getLogger(__name__)

MINUTE = 60
HOUR = 3600
HALF_DAY = 12 * HOUR                    # a local day is never shorter than this, even when the clocks change
MEMORY_KEY = 'sensitivity_review'       # where it keeps what it remembers (KeyValueStore)

# (detector word, step index) → how sure a box of it must be at that step; None for a word that doesn't count
Bar = Callable[[str, int], float | None]
# (when, detector word, True = it joined the chat / False = it left), from the event log
Join = tuple[float, str, bool]
CHAT_EVENTS = {AgentJoined.TYPE, AgentLeft.TYPE}


# ── The judgement: plain counting, nothing saved ──────────────────────────────────────────────────────────────

def review(*, step: int, steps: Sequence[SensitivityStep], settings: SensitivityReviewSettings,
           changes: Sequence[StateRecord], intervals: Sequence[Mapping[str, Any]], joins: Sequence[Join],
           gone_sec: float, on_desk: OnDesk | None, bar: Bar, person_label: str, start: float,
           now: float) -> SensitivityVerdict:
    """Judge the stretch from `start` to `now` at slider step `step` (an index into `steps`).

    `changes`: object-state rows (ObjectStateStore.changes), all at this step. `gone_sec`: how long something is
    missing at this step before it counts as removed (a removed row's dwell_sec includes it). `intervals`: what the
    camera saw (PresenceHistory.presence), cut to the stretch here. `joins`: who joined and left the chat, from
    the event log, oldest first or not. `on_desk(x, y, width, height)`: the marked desk's answer for a point (None:
    no desk marked); None here = don't check. `bar(label, step)`: how sure a box of that word must be at that step,
    None for words that don't count (no character of their own, or a new thing)."""
    spans = [s for s in (_span(i, start, now) for i in intervals) if s is not None]
    hours = _union_sec([(s.start, s.end) for s in spans]) / HOUR
    blinks = sum(1 for r in changes if start < r.ts <= now and r.type == 'removed' and r.label != person_label
                 and r.dwell_sec is not None and r.dwell_sec - gone_sec < settings.blink_sec)
    chat = _ChatLog(joins)
    easier = step + 1
    misses = 0
    if easier < len(steps):
        for s in spans:
            if s.label == person_label or s.conf is None or s.end - s.start < settings.missed_min_sec:
                continue
            here, there = bar(s.label, step), bar(s.label, easier)
            if here is None or there is None or not there <= s.conf < here:
                continue
            if chat.in_chat(s.label, s.start, s.end) or _off_desk(s, on_desk):
                continue
            misses += 1

    def verdict(why: str, to: int = step) -> SensitivityVerdict:
        return SensitivityVerdict(ts=now, from_step=step, to_step=to, why=why, watched_hours=hours, blinks=blinks,
                                  misses=misses)

    if hours <= 0 or hours < settings.min_watch_hours:
        return verdict('not_enough_watching')
    jumpy = blinks / hours >= settings.jumpy_per_hour
    missing = misses >= settings.missed_at_least
    if jumpy and missing:
        return verdict('mixed')
    if not (jumpy or missing):
        return verdict('steady')
    to = step - 1 if jumpy else step + 1
    if not 0 <= to < len(steps):
        return verdict('at_the_end')
    return verdict('jumpy' if jumpy else 'missing', to)


@dataclass(frozen=True)
class _Span:
    label: str
    start: float
    end: float
    conf: float | None                      # the average confidence; None when the interval has none
    center: tuple[float, float] | None = None
    size: tuple[int, int] | None = None     # the picture's width, height; None for intervals saved before it


def _number(value: Any) -> float | None:
    return None if isinstance(value, bool) or not isinstance(value, int | float) else float(value)


def _pair(value: Any) -> tuple[float, float] | None:
    if not isinstance(value, list | tuple) or len(value) != 2:
        return None
    a, b = _number(value[0]), _number(value[1])
    return None if a is None or b is None else (a, b)


def _span(interval: Mapping[str, Any], start: float, now: float) -> _Span | None:
    """One interval cut to [start, now]; None when nothing of it is left, or it isn't one."""
    try:
        first, last = (float(t) for t in interval['interval'])
        label = str(interval['label'])
    except (KeyError, TypeError, ValueError):
        return None
    first, last = max(first, start), min(last, now)
    if last <= first:
        return None
    size = _pair(interval.get('size'))
    return _Span(label, first, last, _number(interval.get('avg_conf')), center=_pair(interval.get('center')),
                 size=None if size is None else (int(size[0]), int(size[1])))


def _off_desk(span: _Span, on_desk: OnDesk | None) -> bool:
    """Its center is off the marked desk. False when there's no desk to check, or the interval can't say."""
    if on_desk is None or span.center is None or span.size is None:
        return False
    return on_desk(span.center[0], span.center[1], span.size[0], span.size[1]) is False


class _ChatLog:
    """Who was in the chat when, from the joins and leaves the event log saw."""

    def __init__(self, joins: Sequence[Join]):
        self._by_label: dict[str, list[tuple[float, bool]]] = {}
        for ts, label, joined in sorted(joins):
            self._by_label.setdefault(label, []).append((ts, joined))

    def in_chat(self, label: str, start: float, end: float) -> bool:
        """`label` was in the chat when `start` came, or joined it by `end`."""
        events = self._by_label.get(label, [])
        if any(joined and start < ts <= end for ts, joined in events):
            return True
        before = [joined for ts, joined in events if ts <= start]
        if before:
            return before[-1]
        after = [joined for ts, joined in events if ts > start]
        return bool(after) and not after[0]         # the first thing seen is it leaving: it was in before


def _union_sec(spans: Sequence[tuple[float, float]]) -> float:
    """How long at least one of the spans covers (overlaps count once)."""
    total, reach = 0.0, float('-inf')
    for first, last in sorted(spans):
        if last > reach:
            total += last - max(first, reach)
            reach = last
    return total


def days_covered(start: float, now: float, local_day: Callable[[float], str]) -> list[str]:
    """Every local day (Clock.local_day) from `start` to `now`, oldest first. It steps half a day at a time: a whole
    day's step could jump over a 23-hour day when the clocks go forward."""
    moments, t = [], start
    while t <= now:
        moments.append(t)
        t += HALF_DAY
    return list(dict.fromkeys(local_day(m) for m in [*moments, now]))


# ── The background job ────────────────────────────────────────────────────────────────────────────────────────

@dataclass
class _Memory:
    step: int                                   # the slider step it last saw
    since: float                                # it judges only what came after this
    hold_until: float | None = None             # after you move the slider, it leaves it alone until then
    last: SensitivityVerdict | None = None
    moves: list[SensitivityVerdict] = field(default_factory=list)   # newest first
    moved_way: int | None = None                # its own last move: -1 pickier, +1 easier (None after yours)
    undo_after: float | None = None             # it won't move the slider back the other way before then

    def to_dict(self) -> dict[str, Any]:
        return {'step': self.step, 'since': self.since, 'hold_until': self.hold_until,
                'last': self.last.to_dict() if self.last else None, 'moves': [m.to_dict() for m in self.moves],
                'moved_way': self.moved_way, 'undo_after': self.undo_after}

    @classmethod
    def from_dict(cls, data: Any) -> _Memory:
        """Raises TypeError, KeyError or ValueError for something that isn't one (a broken saved value)."""
        if not isinstance(data, dict):
            raise TypeError(f'not a dict but {type(data).__name__}')
        step, hold, last, moves = data['step'], data['hold_until'], data['last'], data['moves']
        if not isinstance(step, int) or isinstance(step, bool):
            raise TypeError(f'step is not a whole number: {step!r}')
        if not isinstance(moves, list):
            raise TypeError(f'moves is not a list: {moves!r}')
        way, undo = data.get('moved_way'), data.get('undo_after')       # not in what was saved before these
        if way not in (None, -1, 1) or isinstance(way, bool):
            raise ValueError(f'moved_way is not -1, 1 or None: {way!r}')
        return cls(step=step, since=float(data['since']), hold_until=None if hold is None else float(hold),
                   last=None if last is None else SensitivityVerdict.from_dict(last),
                   moves=[SensitivityVerdict.from_dict(m) for m in moves], moved_way=way,
                   undo_after=None if undo is None or way is None else float(undo))


class SensitivityReviewer:
    """Implements SensitivityReview (desktalk/ports.py)."""

    name = 'sensitivity review'

    def __init__(self, settings: SensitivityReviewSettings, agents: AgentsSettings,
                 object_states: ObjectStatesSettings, prefs: Preferences, history: ObjectStateStore,
                 presence: PresenceHistory, chat_log: EventStore, store: KeyValueStore, profiles: Profiles,
                 clock: Clock, *, person_label: str, on_desk: OnDesk | None = None):
        """`chat_log`: where the joins and leaves of the chat are (the event log). `on_desk`: the roster's desk
        check (desktalk/bootstrap.py); None = never check where things were."""
        self._settings = settings
        self._agents = agents
        self._object_states = object_states
        self._prefs = prefs
        self._history = history
        self._presence = presence
        self._chat_log = chat_log
        self._on_desk = on_desk
        self._store = store
        self._profiles = profiles
        self._clock = clock
        self._person = person_label
        self._lock = threading.Lock()           # the web routes read last() / moves() while tick() runs
        self._memory = self._load()

    # what the Settings page reads

    def last(self) -> SensitivityVerdict | None:
        with self._lock:
            return self._memory.last if self._memory else None

    def moves(self) -> list[SensitivityVerdict]:
        with self._lock:
            return list(self._memory.moves) if self._memory else []

    # the job

    def tick(self, now: float) -> float:
        s = self._settings
        wait_after_you = s.wait_after_you_change_hours * HOUR
        wait_before_undo = s.wait_before_undo_hours * HOUR
        step = self._current_step()
        with self._lock:
            memory = dataclasses.replace(self._memory) if self._memory else None
        if memory is None:                                          # the first look: it starts fresh
            memory = _Memory(step=step, since=now)
        elif step != memory.step:                                   # you moved the slider
            log.info('The sensitivity slider was moved to %s: it stays there for at least %g hours',
                     self._name(step), s.wait_after_you_change_hours)
            memory.step, memory.since, memory.hold_until = step, now, now + wait_after_you
            memory.moved_way = memory.undo_after = None
        if now < memory.since:                                      # the clock went back
            memory.since = now
        if memory.hold_until is not None:                           # ...and never waits longer than it should
            memory.hold_until = min(memory.hold_until, now + wait_after_you)
        if memory.undo_after is not None:
            memory.undo_after = min(memory.undo_after, now + wait_before_undo)

        if not self._prefs.get('auto_sensitivity'):
            verdict = self._still(step, 'off', now)
        elif memory.hold_until is not None and now < memory.hold_until:
            verdict = self._still(step, 'waiting', now)
        else:
            verdict = self._judge(step, max(memory.since, now - s.look_back_hours * HOUR), now)
            way = verdict.to_step - verdict.from_step
            if (way and memory.moved_way == -way and memory.undo_after is not None
                    and now < memory.undo_after):                   # it would undo its own move too soon
                verdict = dataclasses.replace(verdict, to_step=step, why='too_soon_to_undo')

        if not verdict.moved:
            memory.last = verdict
        elif self._move(verdict):
            memory.step, memory.since, memory.hold_until = verdict.to_step, now, None
            memory.moved_way = 1 if verdict.to_step > verdict.from_step else -1
            memory.undo_after = now + wait_before_undo
            memory.last = verdict
            memory.moves = [verdict, *memory.moves][:s.changes_kept]
        # (a move that couldn't be saved, or that you beat it to, isn't remembered: the next look sees why)
        with self._lock:
            self._memory = memory
        self._store.save(MEMORY_KEY, memory.to_dict())
        return s.check_every_min * MINUTE

    def _judge(self, step: int, start: float, now: float) -> SensitivityVerdict:
        days = days_covered(start, now, self._clock.local_day)
        intervals = [i for day in days for i in self._presence.presence(day)]
        joins = [j for day in days for j in self._joins(day)]
        _, gone_sec, _ = self._object_states.steady_rules(self._agents.sensitivity_steps[step].steady)
        return review(step=step, steps=self._agents.sensitivity_steps, settings=self._settings,
                      changes=self._history.changes(start), intervals=intervals, joins=joins, gone_sec=gone_sec,
                      on_desk=self._on_desk if self._agents.only_on_desk else None, bar=self._bar,
                      person_label=self._person, start=start, now=now)

    def _joins(self, day: str) -> list[Join]:
        """The chat's joins and leaves on `day`, from the event log (lines that aren't one are skipped)."""
        out: list[Join] = []
        for e in self._chat_log.events(day, CHAT_EVENTS, None):
            try:
                out.append((float(e['ts']), str(e['label']), e['type'] == AgentJoined.TYPE))
            except (KeyError, TypeError, ValueError):
                continue
        return out

    def _bar(self, label: str, step: int) -> float | None:
        """How sure a box of `label` must be at `step`; None for a word with no character of its own, or a new
        thing (the detector is wrong about those too often to go by)."""
        profile = self._profiles.for_label(label)
        if profile is None or profile.auto:
            return None
        return confidence_needed(self._agents.sensitivity_steps[step], profile, self._agents.new_things)

    def _move(self, verdict: SensitivityVerdict) -> bool:
        now_at = self._current_step()
        if now_at != verdict.from_step:             # you moved it while it looked: yours stays
            log.info('The sensitivity slider was moved to %s while it looked, so it leaves it there',
                     self._name(now_at))
            return False
        try:
            self._prefs.update({'sensitivity': verdict.to_step})
        except Exception:
            log.exception('Could not move the sensitivity slider to %s', self._name(verdict.to_step))
            return False
        if verdict.why == 'jumpy':
            what = f'{verdict.blinks} things flashed on and off in {verdict.watched_hours:.1f} hours'
        else:
            what = f'{verdict.misses} things stayed in view without being sure enough'
        log.info('Moved the sensitivity slider from %s to %s by itself: %s', self._name(verdict.from_step),
                 self._name(verdict.to_step), what)
        return True

    def _current_step(self) -> int:
        value = self._prefs.get('sensitivity')
        return value if self._agents.step_at(value) is not None else self._agents.sensitivity_default

    def _name(self, step: int) -> str:
        return self._agents.sensitivity_steps[step].name

    @staticmethod
    def _still(step: int, why: str, now: float) -> SensitivityVerdict:
        """It didn't look: the slider stays, and there's nothing counted."""
        return SensitivityVerdict(ts=now, from_step=step, to_step=step, why=why, watched_hours=0, blinks=0, misses=0)

    def _load(self) -> _Memory | None:
        """What it saved last time; None (logged) when there's nothing, or it's broken."""
        saved = self._store.load(MEMORY_KEY)
        if saved is None:
            return None
        try:
            memory = _Memory.from_dict(saved)
            if self._agents.step_at(memory.step) is None:
                raise ValueError(f'step {memory.step} is not one of the {len(self._agents.sensitivity_steps)} steps')
        except (TypeError, KeyError, ValueError) as e:
            log.warning('What the sensitivity review saved under %r is broken, so it starts over: %s', MEMORY_KEY, e)
            return None
        return memory
