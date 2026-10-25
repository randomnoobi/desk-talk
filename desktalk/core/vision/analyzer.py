"""
The desk analyzer: runs every desk check on the agents' objects in each picture.

    an = DeskAnalyzer(rules, words=config.prompts.desk_checks)
    result = an.analyze(things, (width, height), now)
    result['risk_level']        # 0 (safe) to 4

The checks, in order: desk edges, danger zones, things in between a danger pair, crowds, things moving
toward each other, things that keep going back into a zone. The result has the shape the app and the AI
have always read (SafetyNow.result):

    {"timestamp": ..., "objects": [...], "relationships": [...], "risk_events": [...],
     "explanations": [...], "risk_level": 2}

It remembers the last rules.history_length pictures (to see things move) and the latest result (for the
live view). analyze() is called from one thread; latest and history() may be read from another. The
explanations' wording is in config/prompts.yaml (desk_checks).
"""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Mapping, Sequence
from typing import Any

from desktalk.config.prompts import DeskCheckWords
from desktalk.core.vision.danger_zones import ZoneVisits, repeated_entries, zone_risks
from desktalk.core.vision.desk_edges import Size, edge_risks
from desktalk.core.vision.findings import Findings, Thing
from desktalk.core.vision.risky_pairs import approaching, crowds, things_in_between
from desktalk.domain import DeskRules
from desktalk.ports import DeskRulesView


def risk_level(events: Sequence[Mapping[str, Any]], weights: Mapping[str, int], steps: Sequence[int]) -> int:
    """0 when nothing is wrong; otherwise 1 + how many of `steps` the summed weights are above."""
    score = sum(weights[e['type']] for e in events)
    if score == 0:
        return 0
    return 1 + sum(score > step for step in steps)


class DeskAnalyzer:
    def __init__(self, rules: DeskRulesView, *, words: DeskCheckWords):
        self._rules = rules
        self._words = words
        self._visits = ZoneVisits()
        self._lock = threading.Lock()
        self._history: deque[dict[str, Thing]] = deque(maxlen=rules.current.history_length)
        self._latest: dict[str, Any] | None = None

    @property
    def rules(self) -> DeskRules:
        return self._rules.current

    @property
    def latest(self) -> dict[str, Any] | None:
        """The result of the last analyze(), or None before the first."""
        return self._latest

    def history(self) -> list[dict[str, Thing]]:
        """The things in each of the last pictures analysed, oldest first."""
        with self._lock:
            return list(self._history)

    def analyze(self, things: Mapping[str, Thing], size: Size, now: float) -> dict[str, Any]:
        rules, words = self._rules.current, self._words
        found = Findings()
        found += edge_risks(things, rules, size, words=words)
        found += zone_risks(things, rules, self._visits, now, words=words)
        pairs = dict.fromkeys((e['intruder'], e['target']) for e in found.risk_events if e['type'] == 'zone_intrusion')
        found += things_in_between(things, pairs, rules, words=words)
        found += crowds(things, rules, words=words)
        history = self.history()
        if len(history) >= rules.approach_min_frames:
            found += approaching(things, history[-rules.approach_min_frames], rules, words=words)
        found += repeated_entries(things, rules, self._visits, now, words=words)
        result = {
            'timestamp': now,
            'objects': [{'id': t.id, 'class': t.label, 'center': [round(c, 1) for c in t.center],
                         'bbox': [round(v, 1) for v in t.box]} for t in things.values()],
            'relationships': found.relationships,
            'risk_events': found.risk_events,
            'explanations': found.explanations,
            'risk_level': risk_level(found.risk_events, rules.risk_weights, rules.risk_level_steps),
        }
        with self._lock:
            if self._history.maxlen != rules.history_length:
                self._history = deque(self._history, maxlen=rules.history_length)
            self._history.append(dict(things))
            self._latest = result
        return result
