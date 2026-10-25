"""DangerBoard: the dangers on the desk right now (the desk monitor writes them), and the latest alerts."""

from __future__ import annotations

import threading
from collections.abc import Mapping, Sequence
from typing import Any

from desktalk.domain import Danger, SafetyNow


class DangerBoard:
    """Implements SafetyBoard."""

    def __init__(self):
        self._lock = threading.Lock()
        self._now = SafetyNow()
        self._since: dict[frozenset[str], float] = {}     # the two words of an active danger → when it started
        self._alerts: list[dict[str, Any]] = []           # the latest alerts, oldest first, for the AI's context

    def report(self, state: str, dangers: Sequence[Danger], result: Mapping[str, Any] | None, now: float) -> None:
        active = {frozenset(d.pair) for d in dangers}
        with self._lock:
            self._now = SafetyNow(state=state, dangers=tuple(dangers),
                                  result=dict(result) if result is not None else None)
            self._since = {pair: self._since.get(pair, now) for pair in active}

    def now(self) -> SafetyNow:
        with self._lock:
            return self._now

    def danger_since(self, pair: frozenset[str]) -> float | None:
        with self._lock:
            return self._since.get(pair)

    def note_alert(self, event: Mapping[str, Any], keep: int) -> None:
        with self._lock:
            self._alerts = [*self._alerts, dict(event)][-keep:] if keep > 0 else []

    def recent_alerts(self, limit: int) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(e) for e in self._alerts[-limit:]] if limit > 0 else []
