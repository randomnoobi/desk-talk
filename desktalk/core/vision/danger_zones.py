"""
Danger zones: an invisible bubble around fragile things (the laptop, the keyboard ...).

The camera looks at the desk at an angle, so boxes are judged by where things stand: a zone is grown
around the part of the fragile thing that sits on the desk (the bottom `base_share` of its box, not a
laptop's open screen), and a drink counts when the bottom strip of its box is inside.

Overlap alone isn't a danger: only things that can really do harm there count (a drink or food near
the laptop, something sharp near the phone), not a book resting next to it. Which words count for
which zone is in the rules (settings → safety.zone_dangers, plus the agents' drink and food words).
A thing that keeps going back into a zone is noticed too.
"""

from __future__ import annotations

from collections.abc import Mapping

from desktalk.config.prompts import DeskCheckWords
from desktalk.core.vision.findings import Findings, Thing
from desktalk.core.vision.geometry import Box, box_gap, distance, footprint, grow, overlap
from desktalk.core.vision.rules import standing_strip
from desktalk.domain import DeskRules, Zone

Pair = tuple[str, str]             # (the thing that went in, the thing whose zone it is)


def base(box: Box, zone: Zone, rules: DeskRules) -> Box:
    """The part of a fragile thing that sits on the desk: the bottom zone.base_share of its box."""
    return footprint(box, zone.base_share, rules.footprint_min_px)


def zone_area(box: Box, zone: Zone, rules: DeskRules) -> Box:
    """The danger zone around a fragile thing: its base, zone.expand pixels wider on every side."""
    return grow(base(box, zone, rules), zone.expand)


def counts_as_danger(rules: DeskRules, zone_label: str, intruder_label: str) -> bool:
    """A thing with this word is a danger inside this zone (a cup near the laptop, not a book)."""
    if zone_label not in rules.zones or not intruder_label:
        return False
    return intruder_label.strip().lower() in rules.zone_intruders.get(zone_label, frozenset())


class ZoneVisits:
    """When things went into danger zones, to notice one that keeps coming back."""

    def __init__(self):
        self._inside: dict[Pair, bool] = {}
        self._went_in: dict[Pair, list[float]] = {}

    def note(self, pair: Pair, inside: bool, now: float) -> None:
        if inside and not self._inside.get(pair, False):
            self._went_in.setdefault(pair, []).append(now)
        self._inside[pair] = inside

    def times_within(self, now: float, window: float) -> list[tuple[Pair, int]]:
        """How often each pair went in during the last `window` seconds (older visits are forgotten)."""
        out = []
        for pair, times in self._went_in.items():
            recent = [t for t in times if now - t < window]
            self._went_in[pair] = recent
            out.append((pair, len(recent)))
        return out


def zone_risks(things: Mapping[str, Thing], rules: DeskRules, visits: ZoneVisits, now: float, *,
               words: DeskCheckWords) -> Findings:
    found = Findings()
    for target in things.values():
        zone = rules.zones.get(target.label)
        if zone is None:
            continue
        target_base = base(target.box, zone, rules)
        area = grow(target_base, zone.expand)       # as zone_area(); the base is needed again below
        for intruder in things.values():
            if intruder.id == target.id:
                continue
            strip = standing_strip(intruder.box, rules)
            danger = overlap(strip, area) is not None and counts_as_danger(rules, target.label, intruder.label)
            visits.note((intruder.id, target.id), danger, now)
            if not danger:
                continue
            depth = zone.expand - box_gap(strip, target_base)     # how far inside: all of expand when touching
            found.add({'type': 'zone_intrusion', 'intruder': intruder.id, 'intruder_class': intruder.label,
                       'target': target.id, 'target_class': target.label, 'zone_label': zone.label,
                       'depth_px': round(depth, 1),
                       'center_dist_px': round(distance(intruder.center, target.center), 1),
                       'zone_expand': zone.expand},
                      {'type': 'danger_zone', 'severity': 'danger', 'intruder': intruder.id, 'target': target.id,
                       'depth_px': round(depth, 1), 'zone_label': zone.label},
                      words.zone.format(intruder=intruder.label, target=target.label, zone=zone.label,
                                        depth=int(depth)))
    return found


def repeated_entries(things: Mapping[str, Thing], rules: DeskRules, visits: ZoneVisits, now: float, *,
                     words: DeskCheckWords) -> Findings:
    """Things that went into the same zone at least rules.repeated_times times lately."""
    found = Findings()
    for (intruder, target), count in visits.times_within(now, rules.repeated_window_sec):
        if count < rules.repeated_times or intruder not in things or target not in things:
            continue
        found.add({'type': 'repeated_intrusion', 'intruder': intruder, 'target': target, 'count': count},
                  {'type': 'repeated_intrusion', 'severity': 'high', 'intruder': intruder, 'target': target,
                   'count': count},
                  words.repeated.format(intruder=things[intruder].label, target=things[target].label, count=count,
                                        window=rules.repeated_window_sec))
    return found
