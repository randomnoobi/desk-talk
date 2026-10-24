"""The danger rules in use (desktalk/core/vision/rules.py builds them from the settings and what the
Settings page saved)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from desktalk.domain.pictures import Point


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
