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

import cv2
import numpy as np
import pytest
import yaml

from desktalk.core.vision.analyzer import DeskAnalyzer
from desktalk.core.vision.camera_health import CameraHealth
from desktalk.core.vision.desk_monitor import DeskMonitor
from desktalk.core.vision.findings import Thing
from desktalk.core.vision.live_view import DeskPainter
from desktalk.core.vision.proof_photos import ProofPhotos
from desktalk.core.vision.rules import SafetyRules, make_rules
from desktalk.desk_state import DeskState
from desktalk.domain import AgentProfile, Alert, BBox, Detection, DetectionEvent, Light
from tests.fakes import (
    FakeClock,
    FakeDeskStatus,
    FakeHabits,
    FakeProfiles,
    InMemoryKeyValueStore,
    SyncEventBus,
)
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



# ── Lens's camera checks ──
def old_camera_health():
    ch = fresh_old('camera_health')
    raw = yaml.safe_load((ROOT / 'config' / 'settings.yaml').read_text())
    ch.configure(raw['lens'])
    return ch


def test_lens_numbers_left_in_the_old_code_match_the_settings():
    ch, lens = old_camera_health(), app_config().lens
    in_settings = (lens.measure_width, lens.edge_touch_px, lens.fixed_after_sec, lens.edge_tip_after_sec)
    assert in_settings == (ch.SAMPLE_WIDTH, ch.EDGE_TOUCH_PX, ch.CLEAR_HOLD_SEC, ch.EDGE_TIP_AFTER_SEC)
    waits = lens.wait_before_saying_sec.model_dump()
    waits.pop('pi')                  # new: the Pi's health, which the old code never checked
    assert {key: wait for key, (wait, _, _) in ch.ISSUES.items()} == waits
    assert app_config().camera.moved_within_sec == 1.5          # the old producer's "moved" window


def _picture(kind, size=(480, 360)):        # twice the measuring width: quick to shrink
    w, h = size
    if kind == 'black':
        return np.zeros((h, w, 3), np.uint8)
    if kind == 'white':
        return np.full((h, w, 3), 255, np.uint8)
    if kind == 'flat':
        return np.full((h, w, 3), 140, np.uint8)
    f = np.full((h, w, 3), 140, np.uint8)
    f[::8] = 30
    return (f * 0.15).astype(np.uint8) if kind == 'dark' else f


PICTURES = {k: _picture(k) for k in ('bright', 'dark', 'black', 'white', 'flat')}
BOXES = {
    'cup': [('cup', [100, 100, 200, 200])],
    'cup cut left': [('cup', [0, 200, 80, 300]), ('laptop', [200, 100, 400, 300])],
    'two cut': [('cup', [0, 200, 80, 300]), ('book', [380, 100, 480, 200])],
    'book cut right': [('book', [380, 100, 480, 200])],
    'chair cut': [('chair', [0, 200, 80, 300]), ('cup', [100, 100, 200, 200])],
    'person only': [('person', [300, 0, 480, 300])],
    'nothing': [],
    'fills': [('laptop', [0, 10, 450, 300])],
}


def lens_timeline(seed, seconds=3000):
    """(time, what the camera shows, Lens's check arguments) for each second, in random stretches."""
    rnd = random.Random(seed)
    t, out = 1_760_000_000.0, []
    while len(out) < seconds:
        stretch = rnd.choice([1, 3, 6, 10, 20, 50, 90, 200])
        picture = rnd.choice(['bright'] * 4 + ['dark', 'black', 'white', 'flat'])
        boxes = BOXES[rnd.choice(list(BOXES))]
        connected = rnd.random() > 0.12
        check = {'connected': connected, 'fps': rnd.choice([3.0, 2.9, 1.0, 0.0, None]),
                 'has_outline': rnd.random() > 0.3, 'stopped': (not connected) and rnd.random() < 0.3}
        if rnd.random() < 0.25:
            check['gravity'], check['reference_gravity'] = (0, 9.8, rnd.choice([0, 1, 4])), (0, 9.8, 0)
        for _ in range(stretch):
            out.append((t, picture if connected else None, boxes, check))
            t += 1
    return out[:seconds]


def as_detections(boxes):
    return [Detection(label, 0.9, BBox(*box)) for label, box in boxes]


@pytest.mark.parametrize('seed, source', [(0, 'pi'), (1, 'phone'), (2, 'webcam')])
def test_lens_says_the_same_things_at_the_same_times(seed, source):
    ch = old_camera_health()
    lens = app_config().lens
    ignore = app_config().agents_file.ignore
    old = ch.CameraHealth(ignore_classes=ignore, source_kind=source)
    new = CameraHealth(lens, source_kind=source, moved_within_sec=1.5, ignore_labels=ignore)
    said = []
    for t, picture, boxes, check in lens_timeline(seed):
        if picture is not None:
            for dt in (0, 1 / 3, 2 / 3):                       # three pictures a second, as the camera sends
                old.sample(PICTURES[picture], boxes, now=t + dt)
                new.sample(PICTURES[picture], as_detections(boxes), t + dt)
        mts = old.motion_ts
        assert new.moved_recently(t + 0.9) == bool(mts and 0 <= t + 0.9 - mts < 1.5), t
        a, b = old.check(t, **check), new.check(t, **check)
        assert a == b, t
        assert old.issues() == new.issues(), t
        assert old.phone_tip() == new.phone_tip(), t
        said += [m['key'] for m in b]
    assert {'lost', 'covered', 'dark', 'glare', 'blurry', 'cutoff', 'slow', 'bumped'} <= set(said)


def test_lens_forget_is_the_same():
    ch = old_camera_health()
    old = ch.CameraHealth(source_kind='phone')
    new = CameraHealth(app_config().lens, source_kind='phone', moved_within_sec=1.5)
    tilted = {'connected': True, 'fps': 3.0, 'gravity': (0, 9.8, 0), 'reference_gravity': (0, 9.8, 4)}
    for t in range(1000, 1010):
        old.sample(PICTURES['bright'], BOXES['cup'], now=t)
        new.sample(PICTURES['bright'], as_detections(BOXES['cup']), t)
        assert old.check(t, **tilted) == new.check(t, **tilted)
    old.forget('bumped')
    new.forget('bumped')
    for t in range(1010, 1030):
        assert old.check(t, connected=True, fps=3.0) == new.check(t, connected=True, fps=3.0) == []


# ── the desk monitor ──
PROFILES = [
    AgentProfile('computer', 'Computer', labels=['laptop', 'keyboard'], fragile=True),
    AgentProfile('cup', 'Cup', labels=['cup'], habits=['hydration']),
    AgentProfile('drink', 'Drink', labels=['bottle'], habits=['hydration']),
    AgentProfile('snack', 'Snack', labels=['apple'], habits=['food']),
    AgentProfile('banana', 'Banana', labels=['banana'], habits=['food']),
    AgentProfile('paper', 'Paper', labels=['book'], habits=['tasks'], fragile=True),
    AgentProfile('phone', 'Phone', labels=['cell phone'], fragile=True),
    AgentProfile('plant', 'Plant', labels=['potted plant', 'flower']),
]
EXTRAS = ['person', 'chair', 'pen', 'clock', 'vase', 'potted plant', 'flower', 'key', 'spoon', 'fork']


def monitor_steps(seed):
    """(time, agents' objects, every word in the picture, warm, risk detection on) for each picture."""
    rnd = random.Random(seed)
    warm, risk_on = True, True
    for t, objects in scenes(seed, steps=1500):
        if rnd.random() < 0.01:
            warm = not warm
        if rnd.random() < 0.01:
            risk_on = not risk_on
        words = [o['class_name'] for o in objects.values()] + rnd.sample(EXTRAS, rnd.randint(0, 6))
        if rnd.random() < 0.03:
            words.append(rnd.choice(['cat', 'dog']))           # one pet at a time (the old code's order is random)
        yield t, objects, words, warm, risk_on


class _OldApp:
    """Just enough of the old DeskTalkApp for the old DeskMonitor."""

    def __init__(self, rel, desk, habits):
        raw = yaml.safe_load((ROOT / 'config' / 'settings.yaml').read_text())
        self.settings = SimpleNamespace(alerts=raw['alerts'])
        self.desk, self.habits = desk, habits
        self.analyzer = rel.RelationshipAnalyzer(*SIZE)
        self.profiles = FakeProfiles(PROFILES, ignored=app_config().agents_file.ignore)
        self.published = []
        self.stream = SimpleNamespace(publish=lambda event_type, ts, **data: self.published.append({'ts': ts, **data}))
        self.is_warm = True

    def warm(self, now):
        return self.is_warm


def _live_view_jpeg():
    picture = np.random.default_rng(7).integers(0, 255, (SIZE[1], SIZE[0], 3), dtype=np.uint8)
    return cv2.imencode('.jpg', picture)[1].tobytes()


def _alert_as_old(alert):
    """A new Alert as the old code published it: no id or type, and no empty text / scenario / feature."""
    d = alert.to_dict()
    for key in ('id', 'type'):
        d.pop(key)
    for key in ('text', 'scenario', 'feature'):
        if not d[key]:
            d.pop(key)
    return d


def _safety(desk):
    with desk.lock:
        return (desk.safety_state, desk.safety_dangers, desk.rel_result, desk.danger_active_since,
                desk.recent_events)


@pytest.mark.parametrize('seed', range(4))
def test_desk_monitor_alerts_are_the_same(tmp_path, seed):
    from desktalk.vision.desk_monitor import DeskMonitor as OldDeskMonitor

    rel = old_relationships(tmp_path, {**SAVED, 'desk_outline': OUTLINE})
    clock = [0.0]
    rel.time = SimpleNamespace(time=lambda: clock[0])
    rel.set = _OrderedSet
    old_desk, new_desk = DeskState(), DeskState()
    old_desk.frames[0] = new_desk.frames[0] = _live_view_jpeg()
    old_habits, new_habits = FakeHabits(), FakeHabits()
    app = _OldApp(rel, old_desk, old_habits)
    old = OldDeskMonitor(app)
    bus, status = SyncEventBus(), FakeDeskStatus()
    rules = new_rules({**SAVED, 'desk_outline': OUTLINE})
    DeskMonitor(bus, new_desk, DeskAnalyzer(rules), app.profiles, new_habits, status,
                ProofPhotos(new_desk, FakeClock(), app_config().snapshots), app_config().alerts)
    seen = set()
    for t, objects, words, warm, risk_on in monitor_steps(seed):
        clock[0] = t
        app.is_warm = status.is_warm = warm
        for desk in (old_desk, new_desk):
            desk.objects = dict(objects)
            desk.frame_w, desk.frame_h = SIZE
            desk.active_scenarios['risk_detection'] = risk_on
        found = tuple(Detection(w, 0.9, BBox(0, 0, 10, 10)) for w in words)
        before_old, before_new = len(app.published), len(bus.published)
        old.on_detection({'ts': t, 'detected_obj': [d.to_dict() for d in found]})
        bus.publish(DetectionEvent(ts=t, frame_id='x', source='pi', size=SIZE, detections=found, person=False,
                                   light=Light(brightness=100)))
        olds = app.published[before_old:]
        news = [e for e in bus.published[before_new:] if isinstance(e, Alert)]
        assert len(olds) == len(news), t
        for a, b in zip(olds, news, strict=True):
            b = _alert_as_old(b)
            if 'snapshot' in a:            # the same proof photo, with a different id
                assert old_desk.snapshots[a.pop('snapshot')] == new_desk.snapshots[b.pop('snapshot')]
            assert a == b, t
            seen.add(b.get('feature') or b['kind'])
        assert _safety(old_desk) == _safety(new_desk), t
    assert old_habits.bumps == new_habits.bumps
    assert {'proximity', 'pet_risk', 'organization', 'water_plants'} <= seen


# ── the live view ──
@pytest.mark.parametrize('seed', range(3))
def test_live_view_is_drawn_the_same(tmp_path, seed):
    from desktalk.vision.detector import draw_boxes as old_draw_boxes

    rel = old_relationships(tmp_path, {**SAVED, 'desk_outline': OUTLINE})
    clock = [0.0]
    rel.time = SimpleNamespace(time=lambda: clock[0])
    rel.set = _OrderedSet
    old = rel.RelationshipAnalyzer(*SIZE)
    profiles = FakeProfiles(PROFILES)
    desk = DeskState()
    analyzer = DeskAnalyzer(new_rules({**SAVED, 'desk_outline': OUTLINE}))
    painter = DeskPainter(desk, profiles, analyzer)
    background = np.random.default_rng(seed).integers(0, 255, (SIZE[1], SIZE[0], 3), dtype=np.uint8)
    drawn = 0
    for t, objects in scenes(seed, steps=250):
        clock[0] = t
        desk.objects = dict(objects)
        old.analyze(objects)
        analyzer.analyze({a: Thing.from_desk(a, o) for a, o in objects.items()}, SIZE, t)
        found = [Detection(o['class_name'], o['conf'], BBox(*o['bbox'])) for o in objects.values()]
        found.append(Detection('pen', 0.42, BBox(5, 5, 60, 40)))       # seen, but not an agent
        # the old producer's drawing
        want = background.copy()
        old_draw_boxes(want, [d.to_dict() | {'bbox': [d.bbox.x1, d.bbox.y1, d.bbox.x2, d.bbox.y2]} for d in found],
                       highlight={o['class_name'] for o in objects.values()} or None)
        if objects:
            names = {a: p.name for a in objects if (p := profiles.get(a))}
            old.update_frame_size(*SIZE)
            old.draw_overlays(want, objects, names)
        got = painter.paint(background, found)
        assert np.array_equal(want, got), t
        drawn += bool(old.latest_result['relationships'])
    assert drawn > 50                     # most pictures had overlays to draw
