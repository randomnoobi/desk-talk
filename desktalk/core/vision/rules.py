"""
The danger rules in use.

The built-in rules are config/settings.yaml → safety. What the Settings page saved (danger zones, edge
distances, the desk corners you marked ...) is kept in a KeyValueStore and laid on top, the same way
config/safety_rules.json always was:

    rules = SafetyRules(store, config.safety, liquids=profiles.labels_for_habit('hydration'),
                        foods=profiles.labels_for_habit('food'))
    rules.current.zones['laptop']          # Zone(expand=134, label='Spill / pressure zone')
    rules.save({'desk_outline': [[0.1, 0.2], [0.9, 0.2], [0.9, 0.95]]})

`current` is a read-only DeskRules; a save swaps in a new one, so readers never see half a change.
"""

from __future__ import annotations

import logging
import math
import threading
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from desktalk.config import SafetySettings
from desktalk.core.vision.geometry import Point
from desktalk.ports import KeyValueStore

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Zone:
    """A danger zone around an object: `expand` pixels wider on every side."""

    expand: float
    label: str


@dataclass(frozen=True, kw_only=True)
class DeskRules:
    outline: tuple[Point, ...]          # desk corners, 0-1 of the picture size; empty = not marked
    reference_gravity: tuple[float, ...] | None   # how the phone was tilted when the desk was marked
    edge_danger_px: float
    edge_warn_px: float
    edge_objects: frozenset[str]
    zones: Mapping[str, Zone]
    zone_intruders: Mapping[str, frozenset[str]]  # zone → the words that are a danger inside it
    harmless_pairs: frozenset[frozenset[str]]
    path_ratio: float
    crowd_radius_px: float
    crowd_min_objects: int
    harmless_crowd: frozenset[str]
    history_length: int
    approach_min_frames: int
    approach_rate: float
    repeated_times: int
    repeated_window_sec: float
    risk_weights: Mapping[str, int]
    risk_level_steps: tuple[int, ...]
    desk_border_share: float
    footprint_share: float
    footprint_min_px: float


def _lower(words: Iterable[Any]) -> frozenset[str]:
    return frozenset(str(w).lower() for w in words)


def _section(saved: Mapping, name: str) -> Mapping:
    value = saved.get(name)
    return value if isinstance(value, Mapping) else {}


def _number(section: Mapping, name: str, key: str, built_in, kind=float):
    """The saved value of one setting, or the built-in one if it wasn't saved or is out of range: not a
    number, nan or infinity, below 0, or (for a count, kind=int) below 1. Those are logged."""
    if key not in section:
        return built_in
    raw = section[key]
    try:
        value = kind(raw)
        ok = math.isfinite(value) and value >= (1 if kind is int else 0)
    except (TypeError, ValueError, OverflowError):
        ok = False
    if ok:
        return value
    should_be = 'a whole number, 1 or more' if kind is int else 'a number, 0 or more'
    log.warning('Saved safety rules: %s.%s should be %s (got %r), using %s', name, key, should_be, raw, built_in)
    return built_in


def _zones(safety: SafetySettings, saved: Mapping) -> dict[str, Zone]:
    zones = {name: Zone(z.expand, z.label) for name, z in safety.danger_zones.items()}
    for name, z in _section(saved, 'danger_zones').items():
        try:
            zones[name] = Zone(float(z['expand']), str(z['label']))
        except (KeyError, TypeError, ValueError):
            log.warning('Saved safety rules: the %s danger zone needs an expand number and a label (got %r), '
                        'so it is skipped', name, z)
    return zones


def _zone_intruders(safety: SafetySettings, saved: Mapping, liquids, foods) -> dict[str, frozenset[str]]:
    kinds = {'liquids': _lower(safety.liquids) | _lower(liquids),
             'foods': _lower(safety.foods) | _lower(foods),
             'sharp': _lower(safety.sharp)}
    out = {zone: frozenset().union(*(kinds[k] for k in names)) for zone, names in safety.zone_dangers.items()}
    for zone, words in _section(saved, 'zone_danger_intruders').items():
        if isinstance(words, (list, tuple)):
            out[zone] = _lower(words)
    return out


def _outline(saved: Mapping) -> tuple[Point, ...]:
    corners = saved.get('desk_outline') or []
    try:
        points = tuple((float(x), float(y)) for x, y in corners)
    except (TypeError, ValueError):
        log.warning('Saved safety rules: desk_outline should be a list of [x, y] points (got %r)', corners)
        return ()
    return points if len(points) >= 3 else ()


def make_rules(safety: SafetySettings, saved: Mapping | None = None, liquids: Iterable[str] = (),
               foods: Iterable[str] = ()) -> DeskRules:
    """The rules in use: `safety` with `saved` (what the Settings page saved) on top.

    `liquids` and `foods` are extra drink and food words (the labels of the agents that speak for
    hydration and food), added to safety.liquids and safety.foods."""
    saved = saved or {}
    edge, chain, temporal = (_section(saved, n) for n in ('edge_proximity', 'chain_reaction', 'temporal'))
    gravity = (saved.get('camera_reference') or {}).get('gravity')
    saved_objects = edge.get('objects')
    return DeskRules(
        outline=_outline(saved),
        reference_gravity=tuple(gravity) if gravity and len(gravity) == 3 and saved.get('desk_outline') else None,
        edge_danger_px=_number(edge, 'edge_proximity', 'danger_px', safety.edge_proximity.danger_px),
        edge_warn_px=_number(edge, 'edge_proximity', 'warn_px', safety.edge_proximity.warn_px),
        edge_objects=frozenset(safety.edge_proximity.objects)
        | frozenset(saved_objects if isinstance(saved_objects, (list, tuple)) else ()),
        zones=_zones(safety, saved),
        zone_intruders=_zone_intruders(safety, saved, liquids, foods),
        harmless_pairs=frozenset(_lower(pair) for pair in safety.harmless_pairs),
        path_ratio=_number(chain, 'chain_reaction', 'path_ratio', safety.chain_reaction.path_ratio),
        crowd_radius_px=_number(chain, 'chain_reaction', 'crowd_radius_px', safety.chain_reaction.crowd_radius_px),
        crowd_min_objects=_number(chain, 'chain_reaction', 'crowd_min_objects',
                                  safety.chain_reaction.crowd_min_objects, int),
        harmless_crowd=_lower(safety.chain_reaction.harmless_crowd),
        history_length=_number(temporal, 'temporal', 'history_length', safety.temporal.history_length, int),
        approach_min_frames=_number(temporal, 'temporal', 'approach_min_frames', safety.temporal.approach_min_frames,
                                    int),
        approach_rate=_number(temporal, 'temporal', 'approach_rate', safety.temporal.approach_rate),
        repeated_times=_number(temporal, 'temporal', 'repeated_intrusion_threshold',
                               safety.temporal.repeated_intrusion_threshold, int),
        repeated_window_sec=_number(temporal, 'temporal', 'repeated_window_sec', safety.temporal.repeated_window_sec),
        risk_weights=safety.risk_weights.model_dump(),
        risk_level_steps=tuple(safety.risk_level_steps),
        desk_border_share=safety.desk_border_share,
        footprint_share=safety.footprint_share,
        footprint_min_px=safety.footprint_min_px,
    )


class SafetyRules:
    """The rules in use, kept up to date when the Settings page saves new ones."""

    def __init__(self, store: KeyValueStore, safety: SafetySettings, *, liquids: Iterable[str] = (),
                 foods: Iterable[str] = (), key: str = 'safety_rules'):
        self._store = store
        self._safety = safety
        self._liquids = frozenset(liquids)
        self._foods = frozenset(foods)
        self._key = key
        self._lock = threading.Lock()
        self._reload()

    def _reload(self) -> None:
        saved = self._store.load(self._key)
        self._saved = saved if isinstance(saved, dict) else None
        self._current = make_rules(self._safety, self._saved, self._liquids, self._foods)

    @property
    def current(self) -> DeskRules:
        return self._current

    def has_outline(self) -> bool:
        """The desk corners are marked (Lens's DeskLayout)."""
        return bool(self._current.outline)

    def reference_gravity(self) -> tuple[float, ...] | None:
        """How the phone was tilted when the desk corners were marked (Lens's DeskLayout)."""
        return self._current.reference_gravity

    @property
    def loaded(self) -> bool:
        """Rules were saved from the Settings page (otherwise only the built-in ones are used)."""
        return self._saved is not None

    def save(self, changes: Mapping[str, Any]) -> None:
        """Replace these parts of the saved rules (danger_zones, edge_proximity, desk_outline ...); the rest
        stays as it was. Takes effect at once."""
        with self._lock:
            saved = self._store.load(self._key)
            saved = dict(saved) if isinstance(saved, dict) else {}
            saved.update(changes)
            self._store.save(self._key, saved)
            self._reload()

    def clean_outline(self, points: Any) -> list[list[float]]:
        """Desk corners sent by the Settings page, each cut to 0-1 of the picture, at most
        safety.max_desk_corners of them. Raises ValueError (plain words) for something that isn't corners."""
        try:
            corners = [[round(min(1.0, max(0.0, float(x))), 4), round(min(1.0, max(0.0, float(y))), 4)]
                       for x, y in (points or [])]
        except (TypeError, ValueError):
            raise ValueError('desk_outline must be a list of [x, y] points') from None
        if corners and len(corners) < 3:
            raise ValueError('Mark at least 3 corners of the desk')
        return corners[:self._safety.max_desk_corners]

    def for_settings_page(self) -> dict[str, Any]:
        """What the Settings page shows: the saved rules, or the built-in ones where nothing was saved."""
        saved, rules = self._saved or {}, self._current
        return {
            'loaded': self.loaded,
            'desk_outline': saved.get('desk_outline', []),
            'danger_zones': saved.get('danger_zones', {name: {'expand': z.expand, 'label': z.label}
                                                       for name, z in rules.zones.items()}),
            'edge_proximity': saved.get('edge_proximity', {'danger_px': rules.edge_danger_px,
                                                           'warn_px': rules.edge_warn_px,
                                                           'objects': sorted(rules.edge_objects)}),
            'chain_reaction': saved.get('chain_reaction', {}),
            'temporal': saved.get('temporal', {}),
        }
