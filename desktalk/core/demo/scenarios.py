"""
The developer page's Demo tab: pretend warnings that fire on purpose once a countdown is over, for testing and
for recording a demo video.

The page starts one with a countdown in seconds; this background job checks the countdowns every
dev.scenarios.check_every_sec and hands each one that is over to the ScenarioFirer, which sends it the way the
real warning goes out. Each fires once: one that fails is logged and dropped, not tried again.

    runner = ScenarioRunner(config.dev.scenarios, firer, clock, words=config.prompts.dev)
    supervisor.add(runner)
"""

from __future__ import annotations

import itertools
import logging
import math
import threading
from dataclasses import dataclass

from desktalk.config import DemoSettings
from desktalk.config.prompts import DevWords
from desktalk.domain import Scenario, WaitingScenario
from desktalk.ports import Clock, ScenarioFirer

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Countdown:
    id: str
    scenario: Scenario
    fires_at: float


class ScenarioRunner:
    """The DemoScenarios (desktalk/ports.py), and a PeriodicWorker that fires the ones whose countdown is over."""

    name = 'demo scenarios'

    def __init__(self, settings: DemoSettings, firer: ScenarioFirer, clock: Clock, *, words: DevWords):
        self._settings = settings
        self._firer = firer
        self._clock = clock
        self._words = words
        self._scenarios = {item.name: Scenario(item.name, item.label, item.fires) for item in settings.items}
        self._ids = itertools.count(1)
        self._lock = threading.Lock()           # the web routes and the job's own thread both come here
        self._waiting: dict[str, _Countdown] = {}

    # ── DemoScenarios (the web routes) ──
    def scenarios(self) -> list[Scenario]:
        return list(self._scenarios.values())

    def start(self, name: str, after_sec: float) -> WaitingScenario:
        scenario = self._scenarios.get(name)
        if scenario is None:
            raise ValueError(self._words.unknown_scenario.format(name=name))
        problem = self._firer.problem(name)
        if problem is not None:
            raise ValueError(problem)
        most = self._settings.countdown_max_sec
        if not (math.isfinite(after_sec) and 0 <= after_sec <= most):     # (a NaN fails every comparison)
            raise ValueError(self._words.countdown_out_of_range.format(most=most))
        now = self._clock.now()
        with self._lock:
            if len(self._waiting) >= self._settings.waiting_max:
                raise ValueError(self._words.too_many_waiting.format(count=len(self._waiting)))
            countdown = _Countdown(f'demo-{next(self._ids)}', scenario, now + after_sec)
            self._waiting[countdown.id] = countdown
        log.info('Demo: %s goes off in %g seconds', name, after_sec)
        return self._shown(countdown, now)

    def cancel(self, scenario_id: str) -> None:
        with self._lock:
            if self._waiting.pop(scenario_id, None) is None:
                raise KeyError(self._words.unknown_waiting.format(scenario_id=scenario_id))

    def waiting(self) -> list[WaitingScenario]:
        now = self._clock.now()
        with self._lock:
            waiting = sorted(self._waiting.values(), key=lambda c: c.fires_at)
        return [self._shown(countdown, now) for countdown in waiting]

    @staticmethod
    def _shown(countdown: _Countdown, now: float) -> WaitingScenario:
        s = countdown.scenario
        return WaitingScenario(countdown.id, s.name, s.label, countdown.fires_at, max(0.0, countdown.fires_at - now))

    # ── PeriodicWorker ──
    def tick(self, now: float) -> float:
        with self._lock:
            due = sorted((c for c in self._waiting.values() if c.fires_at <= now), key=lambda c: c.fires_at)
            for countdown in due:
                del self._waiting[countdown.id]
        for countdown in due:
            try:
                self._firer.fire(countdown.scenario.name, countdown.fires_at)
            except Exception:
                log.exception('Demo: %s could not go off', countdown.scenario.name)
            else:
                log.info('Demo: %s went off', countdown.scenario.name)
        return self._settings.check_every_sec
