"""
Scoring the desk test: for each picture and each of Sam's kinds, did the model find it?

A kind's score = found rate × (1 − false alarms). The desk score is the average over
the kinds, each counting the same. A kind with no pictures where it's there is left out
for every model alike; a kind the model has no name for scores 0. See
benchmarks/desk-test-design.md.
"""

from __future__ import annotations

from pathlib import Path

import yaml


def app_thresholds(settings_path: Path, agents_path: Path, kinds: dict) -> dict[str, float]:
    """How sure a model must be for the app to count each kind as seen (never reads settings.local.yaml): the agent's
    own min_confidence, else the sensitivity step the app uses until its slider is moved."""
    app = yaml.safe_load(settings_path.read_text())['agents']
    default = float(app['sensitivity_steps'][app['sensitivity_default']]['min_confidence'])
    agents = yaml.safe_load(agents_path.read_text())['agents']
    out = {}
    for name, kind in kinds.items():
        names = kind.get('app_agent') or []
        names = [names] if isinstance(names, str) else names        # a kind can be covered by more than one agent
        own = [agents.get(a, {}).get('min_confidence') for a in names]
        own = [c for c in own if c is not None]
        out[name] = float(own[0]) if own else default
    return out


def name_map(kinds: dict, names: str) -> dict[str, str]:
    """A model's own names (lowercase) → Sam's kind, for names = coco, objects365 or words."""
    return {n.lower(): kind for kind, k in kinds.items() for n in k.get(names) or []}


def missing_names(kinds: dict, names: str, model_names: list[str]) -> list[str]:
    """Configured names (lowercase) that the model itself doesn't use, so they could never be found."""
    have = {n.lower() for n in model_names}
    return [n for n in name_map(kinds, names) if n not in have]


def found_kinds(detections: list[tuple[str, float]], names: dict[str, str],
                threshold: dict[str, float] | float) -> set[str]:
    found = set()
    for name, conf in detections:
        kind = names.get(name.lower())
        if kind and conf >= (threshold[kind] if isinstance(threshold, dict) else threshold):
            found.add(kind)
    return found


def _share(hits: int, total: int) -> float | None:
    return hits / total if total else None


def score(pictures: list[dict], found: dict, kinds: list[str], nameable: set[str]) -> dict:
    out = {}
    for kind in kinds:
        there = [p for p in pictures if p['answers'].get(kind) == 'there']
        absent = [p for p in pictures if p['answers'].get(kind) == 'not there']
        found_rate = _share(sum(kind in found.get(p['id'], ()) for p in there), len(there))
        false_alarms = _share(sum(kind in found.get(p['id'], ()) for p in absent), len(absent))
        if not there:
            s = None
        elif kind not in nameable:
            s = 0.0
        elif false_alarms is None:
            s = found_rate
        else:
            s = found_rate * (1 - false_alarms)
        out[kind] = {'there': len(there), 'not_there': len(absent), 'found_rate': found_rate,
                     'false_alarms': false_alarms, 'score': s}
    scores = [k['score'] for k in out.values() if k['score'] is not None]
    return {'kinds': out, 'desk_score': sum(scores) / len(scores) if scores else None}


def scene_scores(pictures: list[dict], found: dict, kinds: list[str], nameable: set[str],
                 min_photos: int) -> dict[str, float | None]:
    """Each scene's desk score, over the kinds that are there in at least min_photos of its pictures."""
    out = {}
    for scene in dict.fromkeys(s for p in pictures for s in p['scenes']):
        s = score([p for p in pictures if scene in p['scenes']], found, kinds, nameable)
        ok = [k['score'] for k in s['kinds'].values() if k['score'] is not None and k['there'] >= min_photos]
        out[scene] = sum(ok) / len(ok) if ok else None
    return out
