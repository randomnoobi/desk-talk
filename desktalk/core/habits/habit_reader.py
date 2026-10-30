"""
HabitReader: reads the habits from the camera's DetectionEvents and writes them into today's HabitDay.

    hydration   sips (a drink is lifted, or goes missing and comes back moved a little, with someone at the desk)
                → estimated water
    breaks      away from the desk break_min+ minutes (or the camera off that long); how long you've been sitting
    phone-free  desk time with no phone on the desk, and the current streak
    light       how bright the room is
    food        what was on the desk (healthy if it is in protocols.yaml → food.healthy)

It also keeps what is true right now (someone at the desk, a drink or the phone on the desk, since when ...),
which the habit rules look at. HabitTracker (tracker.py) owns it and holds the lock around every call.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence, Set
from dataclasses import dataclass
from typing import Any

from desktalk.config import (
    AgentsSettings,
    HabitTrackerSettings,
    HydrationRule,
    PresenceSettings,
    Protocols,
    SensitivityStep,
)
from desktalk.config.prompts import HabitNudges
from desktalk.core.agents.presence import PresenceTracker
from desktalk.core.agents.sensitivity import Slider, confidence_needed
from desktalk.core.habits import habit_rules as P
from desktalk.core.habits.habit_days import HabitDay, Said
from desktalk.core.vision.geometry import same_box_share
from desktalk.domain import BBox, Detection, DetectionEvent
from desktalk.ports import Clock, DeskScene, Preferences, Profiles
from desktalk.util import a_or_an

MIN = 60


class HabitReader:
    def __init__(self, clock: Clock, profiles: Profiles, scene: DeskScene, protocols: Protocols,
                 settings: HabitTrackerSettings, presence: PresenceSettings, agents: AgentsSettings,
                 prefs: Preferences, words: HabitNudges, *, phone_counts: bool):
        self._clock = clock
        self._profiles = profiles
        self._scene = scene
        self._rules = protocols
        self._s = settings
        self._words = words
        self._slider = Slider(agents, prefs)       # how sure a box must be follows the Settings page's slider
        self._new_things = agents.new_things        # ...and new things need a surer box
        self._user_window = presence.user_active_window_sec      # not the reminders' switch (the person's object state)
        self._phone_counts = phone_counts                          # off when the phone itself is the camera
        self._healthy = frozenset(protocols.food.healthy)
        self._cats = {
            'drink': profiles.labels_for_habit('hydration'),
            'food': profiles.labels_for_habit('food'),
            'phone': profiles.labels_for_habit('phone_free'),
            'screen': profiles.labels_for_habit('screens'),
        }
        self._presence = PresenceTracker(presence.appear_after_sec, presence.disappear_after_sec)
        self._last_event_ts: float | None = None
        self._user_last_seen: float | None = None
        self._away_since: float | None = None
        self.sit_since: float | None = None
        self._sip_watch = SipWatch(protocols.hydration)
        self.food_since: float | None = None
        self.food_name = ''
        self.meal_done_ts: float | None = None
        self.meal_name = ''
        self.phone_on_desk_since: float | None = None
        self.phone_free_streak = 0.0
        self._celebrated = 0
        self.bright_since: float | None = None
        self.dim_since: float | None = None
        self.light: dict[str, float | None] = {}
        self.screen_on_desk = False
        self.drink_on_desk = False

    # ── what is true right now ──
    def at_desk(self, now: float) -> bool:
        return (self._away_since is None and self._user_last_seen is not None
                and now - self._user_last_seen <= self._user_window)

    def phone_on_desk(self) -> bool:
        return self._presence.is_present('phone')

    def new_day(self) -> None:
        """Midnight: the phone-free streak starts again."""
        self._celebrated = 0
        self.phone_free_streak = 0.0

    def restart_sitting(self, now: float) -> None:
        """A break logged by hand: the sitting clock starts again."""
        self.sit_since = now

    # ── reading one picture ──
    def read(self, event: DetectionEvent, day: HabitDay) -> list[Said]:
        """Write what this picture shows into `day`. Returns what happened, for the chat."""
        now = event.ts
        said: list[Said] = []
        break_sec = self._rules.breaks.break_min * MIN
        gap = (now - self._last_event_ts) if self._last_event_ts else 0.0
        if self._last_event_ts and gap >= break_sec:
            # The camera was off this long: that time away counts as a break.
            said.append(self._log_break(day, now, gap, 'camera off'))
            self.sit_since = now
            self._away_since = None
        dt = min(gap, self._s.gap_counted_max_sec)
        self._last_event_ts = now

        # Someone at the desk? (a person, or something moving in the picture)
        if event.person or event.moved:
            if self._away_since is not None and now - self._away_since >= break_sec:
                said.append(self._log_break(day, now, now - self._away_since, 'left the desk'))
                self.sit_since = now
            elif self.sit_since is None:
                self.sit_since = now
            self._away_since = None
            self._user_last_seen = now
        elif (self._user_last_seen is not None and self._away_since is None
              and now - self._user_last_seen > self._user_window):
            self._away_since = self._user_last_seen
        at_desk = self.at_desk(now)
        if at_desk:
            day.desk_sec += dt
            day.hourly_activity[self._clock.local_hour(now)] += dt
            if self.sit_since:
                day.longest_sit_sec = max(day.longest_sit_sec, round(now - self.sit_since))

        # Things on the desk, by habit. Sips are watched one drink at a time (SipWatch), so picking up the mug
        # counts even if a bottle stays, or a second cup.
        step = self._slider.step()
        best = self._best_per_label(event, step)
        labels = {label: det.confidence for label, det in best.items()}
        seen = {cat for cat, words in self._cats.items() if words & set(labels)}
        appeared, gone = self._presence.update(seen, now)
        self.drink_on_desk = self._presence.is_present('drink')
        self.screen_on_desk = self._presence.is_present('screen')
        drinks: dict[str, list[BBox]] = {}
        for det in event.detections:
            if det.label in self._cats['drink'] and self._sure(det, step):
                drinks.setdefault(det.label, []).append(det.bbox)
        for _ in range(self._sip_watch.update(set(labels), drinks, event.size, now, person=event.person)):
            said.append(log_sip(day, now, self._rules.hydration.ml_per_sip, 'camera', self._s, self._words))
        said += self._food(day, labels, appeared, gone, now)
        if self._phone_counts:
            said += self._phone(day, appeared, gone, at_desk, dt, now)

        # Light
        lux, brightness = event.light.lux, event.light.brightness
        self.light = {'lux': lux, 'brightness': brightness}
        light_rule = self._rules.light
        self.bright_since = (self.bright_since or now) if P.is_bright(lux, brightness, light_rule) else None
        self.dim_since = (self.dim_since or now) if P.is_dim(lux, brightness, light_rule) else None

        # The agents in the chat right now (the roster)
        for profile in (self._profiles.get(agent_id) for agent_id in self._scene.members()):
            if profile and profile.name not in day.objects_seen:
                day.objects_seen.append(profile.name)
        return said

    def _best_per_label(self, event: DetectionEvent, step: SensitivityStep) -> dict[str, Detection]:
        """Detector word → its surest box, for the words sure enough to count (by the slider's step, or the
        agent's own stricter min_confidence)."""
        best: dict[str, Detection] = {}
        for det in event.detections:
            if self._sure(det, step) and (det.label not in best or det.confidence > best[det.label].confidence):
                best[det.label] = det
        return best

    def _sure(self, det: Detection, step: SensitivityStep) -> bool:
        return det.confidence >= confidence_needed(step, self._profiles.for_label(det.label), self._new_things)

    def _food(self, day: HabitDay, labels: dict[str, float], appeared: list[str], gone: list[str],
              now: float) -> list[Said]:
        out: list[Said] = []
        if 'food' in appeared:
            foods = [(label, conf) for label, conf in labels.items() if label in self._cats['food']]
            name = max(foods, key=lambda x: x[1], default=(self._words.unknown_snack, 0))[0]
            self.food_since, self.food_name = now, name
            if not any(f.get('label') == name for f in day.food):
                healthy = name in self._healthy
                day.add_food(name, healthy, now, 'camera')
                said = self._words.logged.food_seen_healthy if healthy else self._words.logged.food_seen_treat
                out.append(('food', 'logged', said.format(a=a_or_an(name), food=name),
                            {'label': name, 'healthy': healthy}))
        if 'food' in gone and self.food_since:
            stayed = (self._presence.last_seen('food') or now) - self.food_since
            if stayed >= self._rules.breaks.meal_min_on_desk_min * MIN:
                self.meal_done_ts, self.meal_name = now, self.food_name
            self.food_since = None
        return out

    def _phone(self, day: HabitDay, appeared: list[str], gone: list[str], at_desk: bool, dt: float,
               now: float) -> list[Said]:
        if 'phone' in appeared:
            self.phone_on_desk_since = now
            self.phone_free_streak = 0.0
            self._celebrated = 0
        if 'phone' in gone:
            self.phone_on_desk_since = None
        if not at_desk:
            return []
        if self._presence.is_present('phone'):
            day.phone_on_desk_sec += dt
            return []
        day.phone_free_sec += dt
        self.phone_free_streak += dt
        day.best_phone_free_sec = max(day.best_phone_free_sec, round(self.phone_free_streak))
        every = self._rules.phone_free.celebrate_every_min * MIN
        if every > 0 and self.phone_free_streak >= every * (self._celebrated + 1):
            self._celebrated += 1
            mins = int(self.phone_free_streak // MIN)
            return [('phone_free', 'streak', self._words.logged.phone_streak.format(minutes=mins), {'minutes': mins})]
        return []

    def _log_break(self, day: HabitDay, now: float, seconds: float, how: str) -> Said:
        mins = max(1, int(seconds // MIN))
        day.add_break(now, mins, how, self._s.breaks_kept)
        return ('breaks', 'break', self._words.logged.break_seen.format(minutes=mins),
                {'minutes': mins, 'how': how, 'count': day.breaks})


@dataclass
class _Drink:
    """One drink on the desk, as SipWatch sees it."""
    rest: BBox                      # its box where it rests on the desk
    rested_at: float                # the last time it was seen resting there
    left_at: float | None = None    # once seen lifted: when it left its resting spot (None = resting)
    covered: bool = False           # the camera was covered since it last rested: this trip can't be a sip
    covered_run: int = 0            # pictures in a row, while it's missing, that looked covered
    seen_with: Set[str] = frozenset()   # everything in the last picture that showed it
    missing: int = 0                # pictures since it last rested that didn't show it
    person_seen: bool = False       # a person was in a picture since it last rested

    def settle(self, box: BBox, now: float) -> None:
        """It rests at `box` from now on, a fresh start."""
        self.rest, self.rested_at, self.left_at, self.covered, self.missing = box, now, None, False, 0
        self.person_seen = False


class SipWatch:
    """Spots sips from where each drink's box is (protocols.yaml → hydration).

    A drink rests somewhere. It is lifted when the middle of its box rises more than `sip_lift_share` of the
    picture's height above where it rests. A sip is counted when the drink is next seen settled again (not
    lifted, and within `sip_back_share` of the picture's diagonal of its resting spot), between
    `sip_gone_min_sec` and `sip_gone_max_sec` after it was last seen resting, and it was either
      - lifted first (the camera may lose it while it's up in the hand), or
      - missing from the pictures in between (a camera looking down sees a bottle at the picture's edge go
        straight out of the picture, never rising), and it comes back moved: its box's middle is more than
        `sip_moved_share` of the picture's diagonal off its resting box's middle. A drink the detector only
        missed for a while, or a hand only covered, comes back where it was, at most with its box drawn a little
        bigger or smaller around the same middle.
    With `sip_needs_person` on, a sip also needs a person in at least one picture from the one after it last
    rested to the one where it rests again: with nobody at the desk, nobody drank.
    A picture where a box of another drink word is nearly the box where it rested (`sip_same_drink_overlap`)
    doesn't count as missing: looking down, the detector often switches the same bottle between "bottle" and
    "cup". A box that only covers its spot is another drink nearby (a mug in front of the bottle).
    If the camera looks covered while a drink is missing (nothing in the picture, or `sip_cover_share` or more of
    the other things that were there in the last picture that showed it are missing too) in `sip_cover_pictures`
    pictures in a row, that trip can't be a sip. Nor can it when no picture came for longer than `sip_stall_sec`
    while the drink was up or missing.

    Every box of a drink word is a drink: each goes to the nearest resting spot of that word, so with two cups on
    the desk, one leaving doesn't hand its spot to the other. The resting spot only moves down (the camera's few
    pixels of wobble settle on the lowest), to wherever the drink was slid when it is seen far from it without
    being lifted (so a slow lift can't drag it along), or to wherever it came back after being missing. A drink
    missing for longer than `sip_gone_max_sec` is forgotten."""

    def __init__(self, rule: HydrationRule):
        self._rule = rule
        self._stall_after = rule.sip_stall_sec
        self._drinks: dict[str, list[_Drink]] = {}
        self._last_picture: float | None = None

    def update(self, labels: Set[str], drinks: Mapping[str, Sequence[BBox]], size: tuple[int, int],
               now: float, *, person: bool) -> int:
        """One picture: every word sure enough in it, every box of each drink word, and whether it shows a
        person. Returns how many sips it ends."""
        width, height = size
        diagonal = math.hypot(width, height)
        lift_px = self._rule.sip_lift_share * height
        back_px = self._rule.sip_back_share * diagonal
        moved_px = self._rule.sip_moved_share * diagonal
        stalled = self._last_picture is not None and now - self._last_picture > self._stall_after
        self._last_picture = now
        sips = 0
        for label in sorted(self._drinks.keys() | drinks.keys()):
            boxes = drinks.get(label, ())
            others = [box for other, theirs in drinks.items() if other != label for box in theirs]
            kept = self._drinks.get(label, [])
            paired = _pair([drink.rest for drink in kept], boxes)
            for i, drink in enumerate(kept):
                box = boxes[paired[i]] if i in paired else None
                drink.person_seen = drink.person_seen or person
                if stalled and (drink.left_at is not None or drink.missing or box is None):
                    drink.covered = True               # the camera stalled while it was up or missing
                if box is None:
                    self._missing(drink, label, labels, others, now)
                    continue
                drink.covered_run = 0
                sips += self._seen(drink, box, lift_px, back_px, moved_px, now)
                drink.seen_with = frozenset(labels)
            used = set(paired.values())
            kept = [drink for drink in kept if not self._forgotten(drink, now)]
            kept += [_Drink(box, now, seen_with=frozenset(labels)) for j, box in enumerate(boxes) if j not in used]
            self._drinks[label] = kept
        return sips

    def _missing(self, drink: _Drink, label: str, labels: Set[str], others: Sequence[BBox], now: float) -> None:
        """This picture doesn't show `drink`."""
        if drink.left_at is None and any(self._same_drink(drink.rest, box) for box in others):
            drink.settle(drink.rest, now)              # still there, under another drink word
            drink.covered_run = 0
            return
        drink.missing += 1
        if self._looks_covered(label, labels, drink.seen_with):
            drink.covered_run += 1
            if drink.covered_run >= self._rule.sip_cover_pictures:
                drink.covered = True
        else:
            drink.covered_run = 0

    def _same_drink(self, rest: BBox, other: BBox) -> bool:
        """Is another drink word's box nearly the box where this drink rested (the same drink renamed), not just
        another drink nearby whose box covers its spot?"""
        return same_box_share(_box(rest), _box(other)) >= self._rule.sip_same_drink_overlap

    def _forgotten(self, drink: _Drink, now: float) -> bool:
        """Missing for longer than any sip takes: it was taken away."""
        since = drink.left_at if drink.left_at is not None else drink.rested_at
        return drink.missing > 0 and now - since > self._rule.sip_gone_max_sec

    def _trip_can_be_sip(self, drink: _Drink, now: float) -> bool:
        """Settled again after a trip that can be a sip: not covered, away a sip's time since it last rested, and
        someone was at the desk meanwhile (when `sip_needs_person` is on)."""
        took = now - (drink.left_at if drink.left_at is not None else drink.rested_at)
        someone = drink.person_seen or not self._rule.sip_needs_person
        return not drink.covered and someone and self._rule.sip_gone_min_sec <= took <= self._rule.sip_gone_max_sec

    def _looks_covered(self, label: str, labels: Set[str], seen_with: Set[str]) -> bool:
        """`label` is missing from this picture: does the camera look covered?"""
        if not labels:
            return True
        others = seen_with - {label}
        return bool(others) and len(others - labels) / len(others) >= self._rule.sip_cover_share

    def _seen(self, drink: _Drink, box: BBox, lift_px: float, back_px: float, moved_px: float, now: float) -> bool:
        x, y = box.center
        rest_x, rest_y = drink.rest.center
        lifted = rest_y - y > lift_px                  # up in the picture is a smaller y
        off = math.hypot(x - rest_x, y - rest_y)      # how far its middle is from where it rests
        near = off <= back_px
        if drink.left_at is None:                      # resting
            if lifted:
                drink.left_at = drink.rested_at
                return False
            back = drink.missing > 0
            sip = back and near and off > moved_px and self._trip_can_be_sip(drink, now)
            if y >= rest_y or not near or back:        # settled lower, slid somewhere else, or put down again
                drink.rest = box
            drink.rested_at = now
            drink.covered = False
            drink.missing = 0
            drink.person_seen = False
            return sip
        if not lifted and near:                        # put back
            sip = self._trip_can_be_sip(drink, now)
            drink.settle(box, now)
            return sip
        if now - drink.left_at > self._rule.sip_gone_max_sec:    # away too long: it rests wherever it is now
            drink.settle(box, now)
        return False


def _pair(rests: Sequence[BBox], boxes: Sequence[BBox]) -> dict[int, int]:
    """Which box (by place in `boxes`) goes to which resting spot (by place in `rests`): the closest pairs first,
    each box and each spot used once. A spot left over is missing; a box left over is a new drink."""
    pairs = sorted((math.dist(rest.center, box.center), i, j)
                   for i, rest in enumerate(rests) for j, box in enumerate(boxes))
    paired: dict[int, int] = {}
    used: set[int] = set()
    for _, i, j in pairs:
        if i not in paired and j not in used:
            paired[i] = j
            used.add(j)
    return paired


def _box(box: BBox) -> tuple[float, float, float, float]:
    return box.x1, box.y1, box.x2, box.y2


def log_sip(day: HabitDay, now: float, ml: float, how: str, settings: HabitTrackerSettings,
            words: HabitNudges) -> Said:
    """A drink, seen by the camera (how='camera') or logged by a button or the chat. ValueError for a bad ml."""
    day.add_sip(now, ml, how, settings.drinks_kept)
    liters = f'{day.water_ml / 1000:.1f}'
    text = (words.logged.sip_seen.format(sips=day.sips, liters=liters) if how == 'camera'
            else words.logged.sip_button.format(liters=liters))
    extra: dict[str, Any] = {'ml': int(ml), 'total_ml': day.water_ml, 'how': how}
    return 'hydration', 'sip', text, extra
