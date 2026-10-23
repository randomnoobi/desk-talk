"""
The habit rules (config/protocols.yaml: rules) checked against the desk.

Pure functions: they get the rules, where each object on the desk is, what changed lately and what
already nudged, and say which nudges are due. They never read the clock (the caller passes `now`) and
never touch the camera, the chat or the disk.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from desktalk.config import (
    AllCond,
    AnyCond,
    Condition,
    EventCond,
    NoEventCond,
    NotCond,
    QuietWindow,
    RuleConfig,
    StateCond,
    rule_timezone,
)
from desktalk.domain import Nudge
from desktalk.domain.rules import DeskSnapshot, ObjectSnapshot, RuleHistory, StateChange
from desktalk.util import in_hour_window, parse_hhmm

MIN = 60
HOUR = 3600

ABSENT = 'ABSENT'

# How each kind of change reads after "hasn't" in the evidence.
_HAS_NOT = {'appeared': 'appeared', 'moved': 'been moved', 'removed': 'been removed'}


def evaluate(rules: Sequence[RuleConfig], snapshot: DeskSnapshot, history: RuleHistory, now: float) -> list[Nudge]:
    """The nudges that are due right now, in the order the rules are written."""
    lookback = lookback_sec(rules)          # the history handed in goes back at least this far
    nudges = []
    for rule in rules:
        nudge = _nudge_if_due(rule, snapshot, history, now, lookback)
        if nudge:
            nudges.append(nudge)
    return nudges


def _nudge_if_due(rule: RuleConfig, snapshot: DeskSnapshot, history: RuleHistory, now: float,
                  lookback: float) -> Nudge | None:
    """The cheap checks first, and the first "no" stops it."""
    if not rule.enabled:
        return None
    if rule.quiet_hours and in_quiet_hours(rule.quiet_hours, rule.timezone, now):
        return None
    fired = history.fired.get(rule.id, ())
    if fired and now - max(fired) < rule.cooldown:
        return None
    if sum(1 for t in fired if now - t < HOUR) >= rule.max_nudges_per_hour:
        return None
    if not holds(rule.gate, snapshot, history.changes, now):
        return None
    if not holds(rule.when, snapshot, history.changes, now):
        return None
    return Nudge(
        habit=rule.habit,
        key=f'rule.{rule.id}',
        evidence='; '.join(_evidence(rule.when, snapshot, history.changes, now, lookback)),
        text=rule.message,
        repeat_after_sec=rule.cooldown,
    )


def holds(cond: Condition, snapshot: DeskSnapshot, changes: Sequence[StateChange], now: float,
          events_after: float | None = None) -> bool:
    """Is this condition true? `events_after` is the time an `event` condition counts from (success checks only)."""
    if isinstance(cond, StateCond):
        check = cond.state
        found = _state_of(check.object, snapshot)
        if found is None or found.state.upper() != check.is_.upper():
            return False
        return check.for_ is None or _lasted(found.since, now, check.for_)
    if isinstance(cond, NoEventCond):
        check = cond.no_event
        if not _lasted(snapshot.watched_since, now, check.for_):
            return False
        return not any(_matches(c, check.object, check.events) and now - c.ts < check.for_ for c in changes)
    if isinstance(cond, EventCond):
        if events_after is None:
            return False
        return any(_matches(c, cond.event.object, cond.event.events) and c.ts > events_after for c in changes)
    if isinstance(cond, AllCond):
        return all(holds(inner, snapshot, changes, now, events_after) for inner in cond.all)
    if isinstance(cond, AnyCond):
        return any(holds(inner, snapshot, changes, now, events_after) for inner in cond.any)
    if isinstance(cond, NotCond):
        return not holds(cond.not_, snapshot, changes, now, events_after)
    return False


def check_success(rule: RuleConfig, fired_at: float, snapshot: DeskSnapshot, changes: Sequence[StateChange],
                  now: float, grace_sec: float = 0.0) -> str | None:
    """'worked', 'missed', or None (still waiting) for a nudge this rule sent at `fired_at`.

    The runner wakes up on the deadline, so `now` is normally no later than it (or only `grace_sec` later,
    as the wait is never exact). A check that comes later still can't tell how the desk looked at the
    deadline: then only the changes seen by the deadline count (`event`), and `state` and `no_event` count
    for nothing.
    """
    if rule.success is None:
        return None
    deadline = fired_at + rule.success.within
    in_time = [c for c in changes if c.ts <= deadline]         # a change after the deadline is too late
    if now > deadline + grace_sec:
        return 'worked' if _told_by_changes(rule.success.when, in_time, fired_at) is True else 'missed'
    if holds(rule.success.when, snapshot, in_time, now, events_after=fired_at):
        return 'worked'
    return 'missed' if now >= deadline else None


def in_quiet_hours(window: QuietWindow, timezone: str, now: float) -> bool:
    """Is `now` inside the window, by the wall clock of `timezone` (start counts, end doesn't, wraps midnight)?"""
    local = datetime.fromtimestamp(now, rule_timezone(timezone))
    return in_hour_window(local.hour * MIN + local.minute, _clock_minutes(window.start), _clock_minutes(window.end))


def lookback_sec(rules: Sequence[RuleConfig]) -> float:
    """The longest `for` or `within` in any rule: how much history the runner has to hand over."""
    longest = 0.0
    for rule in rules:
        longest = max(longest, _longest_for(rule.gate), _longest_for(rule.when))
        if rule.success:
            longest = max(longest, rule.success.within, _longest_for(rule.success.when))
    return longest


# ── helpers ─────────────────────────────────────────────────────────────────────────────────────────────────────────
def _clock_minutes(text: str) -> int:
    hours, minutes = parse_hhmm(text)
    return hours * MIN + minutes


def _told_by_changes(cond: Condition, changes: Sequence[StateChange], after: float) -> bool | None:
    """Is this condition true, going by the changes alone? None when that can't be told (`state`, `no_event`)."""
    if isinstance(cond, EventCond):
        return any(_matches(c, cond.event.object, cond.event.events) and c.ts > after for c in changes)
    if isinstance(cond, AllCond):
        found = [_told_by_changes(inner, changes, after) for inner in cond.all]
        if any(f is False for f in found):
            return False
        return None if None in found else True
    if isinstance(cond, AnyCond):
        found = [_told_by_changes(inner, changes, after) for inner in cond.any]
        if any(f is True for f in found):
            return True
        return None if None in found else False
    if isinstance(cond, NotCond):
        found = _told_by_changes(cond.not_, changes, after)
        return None if found is None else not found
    return None


def _state_of(label: str, snapshot: DeskSnapshot) -> ObjectSnapshot | None:
    """An object the camera has never seen is absent since it started watching (None while nothing is known)."""
    found = snapshot.objects.get(label)
    if found is not None:
        return found
    return ObjectSnapshot(label, ABSENT, snapshot.watched_since)


def _lasted(since: float | None, now: float, at_least: float) -> bool:
    return since is not None and now - since >= at_least


def _matches(change: StateChange, label: str, kinds: Sequence[str]) -> bool:
    return change.label == label and change.change in kinds


def _longest_for(cond: Condition) -> float:
    if isinstance(cond, StateCond):
        return cond.state.for_ or 0.0
    if isinstance(cond, NoEventCond):
        return cond.no_event.for_
    if isinstance(cond, AllCond):
        return max(map(_longest_for, cond.all))
    if isinstance(cond, AnyCond):
        return max(map(_longest_for, cond.any))
    if isinstance(cond, NotCond):
        return _longest_for(cond.not_)
    return 0.0


def _evidence(cond: Condition, snapshot: DeskSnapshot, changes: Sequence[StateChange], now: float,
              lookback: float) -> list[str]:
    """What the camera saw, for the conditions that held. Anything under a `not` isn't described.

    `changes` go back `lookback` seconds. With no matching change in them and a camera that has watched
    for longer than that, the last change is somewhere before them, so the time isn't known exactly.
    """
    if isinstance(cond, StateCond):
        check = cond.state
        if check.for_ is None:
            return [f'the {check.object} is {check.is_}']
        found = _state_of(check.object, snapshot)
        return [f'the {check.object} has been {check.is_} for {_minutes(now - (found.since or now))}']
    if isinstance(cond, NoEventCond):
        check = cond.no_event
        said = f"the {check.object} hasn't {_has_not(check.events)} in"
        last = max((c.ts for c in changes if _matches(c, check.object, check.events)), default=None)
        if last is None:
            if snapshot.watched_since is None or now - snapshot.watched_since > lookback:
                return [f'{said} over {_minutes(check.for_)}']
            last = snapshot.watched_since
        return [f'{said} {_minutes(now - last)}']
    if isinstance(cond, AllCond):
        return [line for inner in cond.all for line in _evidence(inner, snapshot, changes, now, lookback)]
    if isinstance(cond, AnyCond):
        return [line for inner in cond.any if holds(inner, snapshot, changes, now)
                for line in _evidence(inner, snapshot, changes, now, lookback)]
    return []


def _has_not(kinds: Sequence[str]) -> str:
    """'appeared', 'been moved or removed', 'appeared or been removed' ("been" is said once in a row)."""
    words, after_been = [], False
    for kind in kinds:
        phrase = _HAS_NOT[kind]
        passive = phrase.startswith('been ')
        words.append(phrase.removeprefix('been ') if passive and after_been else phrase)
        after_been = passive
    return ' or '.join(words)


def _minutes(seconds: float) -> str:
    """'1 minute', '25 minutes' (whole minutes, rounded down)."""
    count = int(seconds // MIN)
    return f'{count} minute' if count == 1 else f'{count} minutes'
