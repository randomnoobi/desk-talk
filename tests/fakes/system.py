"""Stand-ins for the Pi tab's parts (desktalk/ports.py: SystemStatsReader, SystemStats, PiStatsLink). They pass the
same contracts as the real ones (tests/contracts/system_stats_contract.py), but read no file and call no Pi."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import Any

from desktalk.domain import RawReading, StatsSample, StatsUnavailable

EMPTY_REPLY = {'collecting': True, 'every_sec': 2.0, 'keep_min': 30.0, 'info': {}, 'samples': []}


class FakeStatsReader:
    """Gives `readings` one by one (each with at= the time asked), then the last one again and again. With none,
    an empty reading. `asked` records each time read() was called with."""

    def __init__(self, readings: Sequence[RawReading] = ()):
        self._readings = list(readings)
        self._next = 0
        self.asked: list[float] = []

    def read(self, now: float) -> RawReading:
        self.asked.append(now)
        if not self._readings:
            return RawReading(at=now)
        reading = self._readings[min(self._next, len(self._readings) - 1)]
        self._next += 1
        return replace(reading, at=now)


class FakeStats:
    """A SystemStats whose every tick adds an empty sample at that moment, `every_sec` apart."""

    name = 'system stats'

    def __init__(self, every_sec: float = 2.0, collecting: bool = True):
        self.every_sec = every_sec
        self.collecting = collecting
        self.samples: list[StatsSample] = []

    def tick(self, now: float) -> float:
        self.samples.append(StatsSample(at=now))
        return self.every_sec

    def history(self, since: float | None) -> dict[str, Any]:
        newer_than_all = bool(self.samples) and since is not None and since > self.samples[-1].at
        kept = [s.to_dict() for s in self.samples if since is None or newer_than_all or s.at > since]
        return {**EMPTY_REPLY, 'collecting': self.collecting, 'every_sec': self.every_sec, 'samples': kept}


class FakePiStatsLink:
    """Answers with `reply` (one empty sample at 10.0 when not given), or raises StatsUnavailable(problem) when a
    problem is given. `asked` records each `since`."""

    def __init__(self, reply: dict[str, Any] | None = None, problem: str = ''):
        self.reply = reply if reply is not None else {**EMPTY_REPLY, 'samples': [StatsSample(at=10.0).to_dict()]}
        self.problem = problem
        self.asked: list[float | None] = []

    def fetch(self, since: float | None) -> dict[str, Any]:
        self.asked.append(since)
        if self.problem:
            raise StatsUnavailable(self.problem)
        return self.reply
