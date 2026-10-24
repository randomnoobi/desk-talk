"""
Risky pairs and groups of things:

- something in between a danger pair, which could knock one into the other ("chain reaction");
- a crowd of things close together, which can tip over (a crowd of only normal desk things is fine);
- two things moving toward each other over the last few pictures (unless they often sit together,
  like the laptop and the mouse).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from desktalk.config.prompts import DeskCheckWords
from desktalk.core.vision.danger_zones import Pair
from desktalk.core.vision.findings import Findings, Thing
from desktalk.core.vision.geometry import distance, point_segment_distance
from desktalk.domain import DeskRules


def harmless_pair(rules: DeskRules, a: str, b: str) -> bool:
    """Things that often sit together on a desk (the book and the laptop ...)."""
    a, b = (a or '').strip().lower(), (b or '').strip().lower()
    return bool(a and b) and frozenset((a, b)) in rules.harmless_pairs


def _harmless_crowd(rules: DeskRules, labels: Iterable[str]) -> bool:
    return all((label or '').strip().lower() in rules.harmless_crowd for label in labels)


def things_in_between(things: Mapping[str, Thing], pairs: Iterable[Pair], rules: DeskRules, *,
                      words: DeskCheckWords) -> Findings:
    """A thing close to the path between a danger pair (closer than rules.path_ratio of the path's length)."""
    found = Findings()
    seen = set()
    for a, b in pairs:
        start, end = things[a].center, things[b].center
        length = distance(start, end)
        if length < 1:
            continue
        for c in things.values():
            if c.id in (a, b):
                continue
            gap = point_segment_distance(c.center, start, end)
            key = frozenset((a, b, c.id))
            if gap >= length * rules.path_ratio or key in seen:
                continue
            seen.add(key)
            found.add({'type': 'chain_reaction', 'chain': [a, c.id, b], 'mediator': c.id, 'mediator_class': c.label,
                       'endpoints': [things[a].label, things[b].label], 'distance_to_path': round(gap, 1)},
                      {'type': 'chain_reaction', 'severity': 'high', 'chain': [a, c.id, b]},
                      words.in_between.format(middle=c.label, a=things[a].label, b=things[b].label))
    return found


def crowds(things: Mapping[str, Thing], rules: DeskRules, *, words: DeskCheckWords) -> Findings:
    """At least rules.crowd_min_objects things within rules.crowd_radius_px of one of them."""
    found = Findings()
    seen = set()
    for a in things.values():
        near = [b.id for b in things.values() if b.id != a.id and distance(a.center, b.center) < rules.crowd_radius_px]
        if len(near) + 1 < rules.crowd_min_objects:
            continue
        cluster = sorted([a.id, *near])
        if tuple(cluster) in seen:
            continue
        seen.add(tuple(cluster))
        labels = [things[c].label for c in cluster]
        if _harmless_crowd(rules, labels):
            continue
        found.add({'type': 'crowd', 'cluster': cluster, 'count': len(cluster)},
                  {'type': 'crowd', 'severity': 'warning', 'cluster': list(cluster)},
                  words.crowd.format(count=len(cluster), labels=', '.join(labels)))
    return found


def approaching(things: Mapping[str, Thing], before: Mapping[str, Thing], rules: DeskRules, *,
                words: DeskCheckWords) -> Findings:
    """Pairs at least rules.approach_rate closer than in `before` (rules.approach_min_frames pictures ago)."""
    found = Findings()
    ids = list(things)
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            if a not in before or b not in before:
                continue
            was = distance(before[a].center, before[b].center)
            now = distance(things[a].center, things[b].center)
            if was < 1:
                continue
            rate = (was - now) / was
            if rate <= rules.approach_rate or harmless_pair(rules, things[a].label, things[b].label):
                continue
            found.add({'type': 'approaching', 'object_a': a, 'object_b': b, 'old_dist': round(was, 1),
                       'new_dist': round(now, 1), 'rate_pct': round(rate * 100, 1)},
                      {'type': 'approaching', 'severity': 'warning', 'from': a, 'to': b,
                       'rate_pct': round(rate * 100, 1)},
                      words.approaching.format(a=things[a].label, b=things[b].label, percent=int(rate * 100),
                                               pictures=rules.approach_min_frames))
    return found
