"""
Runs the habit rules (config/protocols.yaml: rules) once every rule_engine.check_every_sec.

Each round it reads the desk once, works out which nudges are due and keeps them. The reminder loop
asks for them with nudges() and says which one it sent with note_fired(); neither of those reads the
desk or runs a rule. For every nudge that went out, later rounds also check whether it worked (the
rule's `success`) and publish a RuleOutcome.

Fire times are kept in memory, so a restart forgets them. The reminder warm-up (reminders.warmup_min)
covers that.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Sequence
from dataclasses import dataclass

from desktalk.config import RuleConfig, RuleEngineSettings
from desktalk.core.habits.rule_engine import check_success, evaluate, lookback_sec
from desktalk.domain import Nudge, RuleOutcome
from desktalk.domain.rules import RuleHistory
from desktalk.ports import EventBus, ObjectStatesView

log = logging.getLogger(__name__)

HOUR = 3600
KEY_PREFIX = 'rule.'            # the keys of the nudges the rule engine makes


@dataclass(frozen=True)
class _OpenCheck:
    """A nudge that went out and may still work or miss."""

    rule: RuleConfig
    fired_at: float


class RuleRunner:
    """A PeriodicWorker and the RuleNudges the reminders read: one round = check every rule once."""

    name = 'rule engine'

    def __init__(self, rules: Sequence[RuleConfig], states: ObjectStatesView, bus: EventBus,
                 settings: RuleEngineSettings):
        self._rules = list(rules)
        self._by_id = {rule.id: rule for rule in self._rules}
        self._states = states
        self._bus = bus
        self._every = settings.check_every_sec
        self._lookback = lookback_sec(self._rules)
        self._lock = threading.Lock()                       # covers everything below
        self._due: list[Nudge] = []
        self._fired: dict[str, list[float]] = {}            # rule id -> when it nudged, oldest first
        self._open: list[_OpenCheck] = []

    # ── RuleNudges (the reminders thread) ──────────────────────────────────────────────────────────────────────────
    def nudges(self, now: float) -> list[Nudge]:
        with self._lock:
            return list(self._due)

    def note_fired(self, nudge: Nudge, now: float) -> None:
        if not nudge.key.startswith(KEY_PREFIX):
            return
        rule = self._by_id.get(nudge.key.removeprefix(KEY_PREFIX))
        if rule is None:
            log.warning('Rule nudge %s is not one of the rules, ignored', nudge.key)
            return
        keep_from = now - max(HOUR, rule.cooldown)          # the cooldown can be longer than the hour
        with self._lock:
            times = [t for t in self._fired.get(rule.id, []) if t >= keep_from]
            times.append(now)
            self._fired[rule.id] = times
            self._due = [n for n in self._due if n.key != nudge.key]
            if rule.success is not None:
                self._open.append(_OpenCheck(rule, now))

    # ── PeriodicWorker (its own thread) ────────────────────────────────────────────────────────────────────────────
    def tick(self, now: float) -> float:
        with self._lock:
            oldest_open = min((c.fired_at for c in self._open), default=now)
        snapshot = self._states.snapshot()
        changes = self._states.changes(min(now - self._lookback, oldest_open))
        with self._lock:
            fired = {rule_id: tuple(times) for rule_id, times in self._fired.items()}
            self._due = evaluate(self._rules, snapshot, RuleHistory(changes, fired), now)
            outcomes = []
            still_open = []
            for check in self._open:
                result = check_success(check.rule, check.fired_at, snapshot, changes, now)
                if result is None:
                    still_open.append(check)
                else:
                    outcomes.append(RuleOutcome(ts=now, rule=check.rule.id, habit=check.rule.habit, result=result,
                                                after_sec=now - check.fired_at))
            self._open = still_open
        for outcome in outcomes:
            self._bus.publish(outcome)
            log.info('Rule %s %s, %.0f seconds after the nudge', outcome.rule, outcome.result, outcome.after_sec)
        return self._every
