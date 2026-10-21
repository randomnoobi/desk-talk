"""
The new desk rules, Lens checks and desk monitor give exactly what the old code gives.

The same scripted scenes go through the old classes (desktalk/vision/) and the new ones
(desktalk/core/vision/), and every result is compared. Phase 4 deletes the old code, and these tests
with it.

The old modules keep their settings in module globals, which other tests change (building the old app
loads config/safety_rules.json into them). So each test here loads its own fresh copy of the old file.
"""

from __future__ import annotations

import importlib.util
import json
import random
from types import SimpleNamespace

import pytest

from desktalk.core.vision.analyzer import DeskAnalyzer
from desktalk.core.vision.findings import Thing
from desktalk.core.vision.rules import SafetyRules, make_rules
from tests.fakes import InMemoryKeyValueStore
from tests.unit.test_core_vision_kit import ROOT, app_config, desk_object

SAVED = json.loads((ROOT / 'config' / 'safety_rules.json').read_text())
OUTLINE = [[0.05, 0.3], [0.97, 0.25], [1.0, 1.0], [0.0, 1.0]]
SIZE = (640, 480)
HYDRATION = ['cup', 'bottle', 'wine glass', 'mug', 'water bottle']
FOOD = ['banana', 'apple', 'sandwich', 'orange', 'broccoli', 'carrot', 'hot dog', 'pizza', 'donut', 'cake', 'cookies']


class _OrderedSet(dict):
    """A set that keeps the order things were added. The old code walks a set() of danger pairs, whose order
    changes from run to run; with this the old code walks them in the order found, as the new code does."""

    def __init__(self, items=()):
        super().__init__((x, None) for x in items)

    def add(self, x):
        self[x] = None

    def __and__(self, other):
        return _OrderedSet(x for x in self if x in other)


def fresh_old(name):
    """A private copy of an old module from desktalk/vision/, with its built-in settings."""
    spec = importlib.util.spec_from_file_location(f'old_{name}_copy', ROOT / 'desktalk' / 'vision' / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def old_relationships(tmp_path, saved):
    """The old relationships module set up the way the old app did it (set_categories, then load_config)."""
    rel = fresh_old('relationships')
    rel.set_categories(HYDRATION, FOOD, {'scissors', 'knife'})
    path = tmp_path / 'safety_rules.json'
    path.write_text(json.dumps(saved))
    rel.load_config(str(path))
    return rel


def new_rules(saved):
    return SafetyRules(InMemoryKeyValueStore({'safety_rules': saved}), app_config().safety,
                       liquids=HYDRATION, foods=FOOD)


# ── the rules in use ──
@pytest.mark.parametrize('saved', [SAVED, {**SAVED, 'desk_outline': OUTLINE},
                                   {**SAVED, 'zone_danger_intruders': {'book': ['cup', 'Pizza']}}, {}])
def test_rules_in_use_are_the_same(tmp_path, saved):
    rel = old_relationships(tmp_path, saved)
    new = make_rules(app_config().safety, saved, HYDRATION, FOOD)
    assert new.outline == tuple(tuple(p) for p in rel.DESK_OUTLINE)
    assert (new.edge_danger_px, new.edge_warn_px, new.edge_objects) == (rel.EDGE_DANGER_PX, rel.EDGE_WARN_PX,
                                                                        rel.EDGE_OBJECTS)
    assert {k: (z.expand, z.label) for k, z in new.zones.items()} == {
        k: (z['expand'], z['label']) for k, z in rel.DANGER_ZONES.items()}
    assert new.zone_intruders == {k: {w.lower() for w in v} for k, v in rel.ZONE_DANGER_INTRUDERS.items()}
    assert (new.crowd_radius_px, new.crowd_min_objects, new.path_ratio) == (rel.CROWD_RADIUS_PX,
                                                                            rel.CROWD_MIN_OBJECTS,
                                                                            rel.CHAIN_PATH_RATIO)
    assert (new.approach_min_frames, new.approach_rate, new.repeated_times) == (rel.APPROACH_MIN_FRAMES,
                                                                                rel.APPROACH_RATE,
                                                                                rel.REPEATED_INTRUSION_N)
    assert new.history_length == rel.HISTORY_LENGTH and new.risk_weights == rel.RISK_WEIGHTS
    assert new.harmless_crowd == rel.BENIGN_CROWD_CLASSES and new.desk_border_share == rel.BORDER_FRACTION


# ── scripted scenes ──
LABELS = {'computer': 'laptop', 'keys': 'keyboard', 'cup': 'cup', 'drink': 'bottle', 'snack': 'apple',
          'scissors': 'scissors', 'phone': 'cell phone', 'paper': 'book', 'mouse': 'mouse', 'remote': 'remote',
          'glass': 'wine glass', 'banana': 'banana'}


def scenes(seed, steps=400, size=SIZE):
    """(time, desk objects) for each picture: things wander about, come and go, and bump into each other."""
    rnd = random.Random(seed)
    w, h = size
    place = {}
    t = 1_760_000_000.0
    for _ in range(steps):
        t += rnd.choice([1 / 3, 1 / 3, 1 / 3, 1.0, 5.0])
        for agent in LABELS:
            if agent in place and rnd.random() < 0.02:
                del place[agent]                                    # taken away
            elif agent not in place and rnd.random() < 0.05:
                bw, bh = rnd.uniform(30, 160), rnd.uniform(30, 140)
                x, y = rnd.uniform(-20, w - bw + 20), rnd.uniform(-20, h - bh + 20)
                place[agent] = [x, y, bw, bh]                       # put down somewhere
            elif agent in place:
                p = place[agent]
                p[0] += rnd.gauss(0, 12)
                p[1] += rnd.gauss(0, 12)
        objects = {}
        for agent, (x, y, bw, bh) in place.items():
            box = (max(0.0, x), max(0.0, y), min(float(w), x + bw), min(float(h), y + bh))
            if box[2] > box[0] and box[3] > box[1]:
                objects[agent] = desk_object(LABELS[agent], box)
        yield t, objects


def run_old(rel, steps):
    clock = [0.0]
    rel.time = SimpleNamespace(time=lambda: clock[0])
    rel.set = _OrderedSet
    an = rel.RelationshipAnalyzer(*SIZE)
    out = []
    for t, objects in steps:
        clock[0] = t
        out.append(an.analyze(objects))
    return out


def run_new(rules, steps):
    an = DeskAnalyzer(rules)
    return [an.analyze({a: Thing.from_desk(a, o) for a, o in objects.items()}, SIZE, t) for t, objects in steps]


@pytest.mark.parametrize('seed', range(6))
@pytest.mark.parametrize('saved', [SAVED, {**SAVED, 'desk_outline': OUTLINE}], ids=['no outline', 'outline'])
def test_desk_analysis_is_the_same(tmp_path, seed, saved):
    steps = list(scenes(seed))
    old = run_old(old_relationships(tmp_path, saved), steps)
    new = run_new(new_rules(saved), steps)
    kinds = {e['type'] for r in new for e in r['risk_events']}
    assert {'zone_intrusion', 'crowd', 'approaching'} <= kinds          # the scenes do exercise the rules
    for i, (a, b) in enumerate(zip(old, new, strict=True)):
        assert a == b, f'picture {i}'


def test_scenes_reach_every_kind_of_risk(tmp_path):
    saved = {**SAVED, 'desk_outline': OUTLINE}
    kinds = set()
    for seed in range(6):
        kinds |= {e['type'] for r in run_new(new_rules(saved), list(scenes(seed))) for e in r['risk_events']}
    assert kinds == {'edge_danger', 'edge_warning', 'zone_intrusion', 'chain_reaction', 'crowd', 'approaching',
                     'repeated_intrusion'}


def test_changed_rules_give_the_same_analysis(tmp_path):
    saved = {**SAVED, 'desk_outline': OUTLINE, 'edge_proximity': {'danger_px': 50, 'warn_px': 120},
             'chain_reaction': {'crowd_radius_px': 90, 'crowd_min_objects': 2},
             'temporal': {'approach_min_frames': 4, 'approach_rate': 0.05, 'repeated_intrusion_threshold': 2},
             'danger_zones': {**SAVED['danger_zones'], 'mouse': {'expand': 40, 'label': 'Mouse zone'}},
             'zone_danger_intruders': {'mouse': ['cup', 'banana']}}
    steps = list(scenes(42))
    assert run_old(old_relationships(tmp_path, saved), steps) == run_new(new_rules(saved), steps)

