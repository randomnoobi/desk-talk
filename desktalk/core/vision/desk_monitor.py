"""
The desk monitor: safety alerts from each analysed picture.

It listens for DetectionEvents. For everything on the desk (DeskScene.on_desk, kept by the roster), in the chat
or not, it runs the desk analyzer (danger zones, desk edges, things in between, crowds, things moving closer) and
publishes an Alert for each danger, with a proof photo. Only an agent in the chat (DeskScene.members) speaks an
alert; when none of the ones it is about is in the chat, Lens says it. It also notices a pet near the desk, a
cluttered desk and a plant that may need water. A pet only counts when the detector was sure about it in enough
of the last few pictures, because an arm close to the camera can look like a cat for a moment. Timings are in
settings → alerts, the wording in config/prompts.yaml → desk_alerts.

What the app and the AI read about the desk's safety goes to the SafetyBoard: each picture's report (SAFE or
DANGEROUS, the dangers and the analyzer's full result) and the alerts that went out.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from desktalk.config import AlertsSettings
from desktalk.config.prompts import DeskAlertWords
from desktalk.core.vision.findings import Thing
from desktalk.core.vision.geometry import Box
from desktalk.domain import LENS, Alert, Danger, Detection, DetectionEvent, Event
from desktalk.ports import DeskScene, DeskStatus, EventBus, HabitsView, PhotoTaker, Profiles, SafetyBoard

SAFE, DANGEROUS = 'SAFE', 'DANGEROUS'


class MonitorDesk(DeskScene, SafetyBoard, Protocol):
    """The parts of the desk the monitor uses: who is on it (read) and its safety (written)."""


class DeskChecks(Protocol):
    """The desk analyzer (DeskAnalyzer): every desk check on the agents' objects in one picture."""

    def analyze(self, things: Mapping[str, Thing], size: tuple[int, int], now: float) -> dict[str, Any]:
        ...


class DeskMonitor:
    def __init__(self, bus: EventBus, desk: MonitorDesk, analyzer: DeskChecks, profiles: Profiles,
                 habits: HabitsView, status: DeskStatus, photos: PhotoTaker, alerts: AlertsSettings, *,
                 words: DeskAlertWords):
        self._bus = bus
        self._words = words
        self._desk = desk
        self._analyzer = analyzer
        self._profiles = profiles
        self._habits = habits
        self._status = status
        self._photos = photos
        self._settings = alerts
        # When each kind of alert last went out (0 = never) and how often in a row
        self._last_alert: dict[str, float] = {}
        self._alerts_in_a_row: dict[str, int] = {}
        self._pet_alerted: dict[str, float] = {}
        # For each pet, whether it was clearly seen in each of the last few pictures
        self._pet_seen: dict[str, deque[bool]] = {pet: deque(maxlen=alerts.pet_window_pictures)
                                                  for pet in alerts.pet_labels}
        self._last_picture_ts: float | None = None
        self._last_clutter = 0.0
        self._last_plant = 0.0
        bus.subscribe('desk-monitor', self._on_event, types=[DetectionEvent])

    def _on_event(self, event: Event) -> None:
        if isinstance(event, DetectionEvent):
            self.on_detection(event)

    def on_detection(self, event: DetectionEvent) -> None:
        now = event.ts
        things = {agent: Thing.from_seen(agent, obj) for agent, obj in self._desk.on_desk().items()}
        chat = set(self._desk.members())
        result = self._analyzer.analyze(things, event.size, now)
        dangers = self._safety_alerts(result, things, chat, now)
        self._keep_safety_state(result, dangers, now)
        labels = {d.label for d in event.detections}
        warm = self._status.warm(now)
        self._pets(event.detections, things, chat, now)
        self._clutter(labels, chat, now, warm)
        self._plants(labels, chat, now, warm)

    # ── danger zones and desk edges ──
    def _safety_alerts(self, result: Mapping[str, Any], things: Mapping[str, Thing], chat: set[str],
                       now: float) -> list[Danger]:
        """One alert per danger (backing off while it stays); returns the dangers for the app and the AI.
        `chat` = the agents in the chat: only they (or Lens) speak."""
        dangers: list[Danger] = []
        checked: set[frozenset[str]] = set()
        for ev in result['risk_events']:
            if ev['type'] == 'zone_intrusion':
                intruder, target = ev['intruder'], ev['target']
                if frozenset((intruder, target)) in checked:
                    continue
                checked.add(frozenset((intruder, target)))
                speaker, other = self._zone_speaker(intruder, target, chat)
                self._proximity_alert(speaker, [things[intruder].box, things[target].box], now, near=other,
                                      severity='high', rule_label=ev['zone_label'], depth_px=ev['depth_px'])
                dangers.append(Danger(pair=(ev['intruder_class'], ev['target_class']), label=ev['zone_label'],
                                      kind='zone_intrusion', depth_px=ev['depth_px']))
            elif ev['type'] == 'edge_danger':
                agent = ev['object']
                reason = self._words.edge_reason.format(edge=ev['edge'], distance=int(ev['distance_px']))
                self._proximity_alert(agent if agent in chat else LENS, [things[agent].box], now, severity='medium',
                                      rule_label=reason)
                dangers.append(Danger(pair=(ev['class'], 'desk_edge'),
                                      label=self._words.edge_danger.format(object=ev['class'], edge=ev['edge']),
                                      kind='edge_danger'))
        return dangers

    def _zone_speaker(self, intruder: str, target: str, chat: set[str]) -> tuple[str, str]:
        """Who speaks about something in another thing's danger zone, and who it is near: the fragile target if
        it's in the chat, else the intruder, else the target, else Lens (about the target)."""
        owner = self._profiles.get(target)
        if owner and owner.fragile and target in chat:
            return target, intruder
        if intruder in chat:
            return intruder, target
        if target in chat:
            return target, intruder
        return LENS, target

    def _proximity_alert(self, speaker: str, crop_boxes: Sequence[Box], now: float, **details: Any) -> bool:
        """An alert from `speaker`, unless the same one went out lately. The wait starts at alerts.cooldown_sec
        and doubles with each repeat (up to alerts.cooldown_max_times that); a quiet spell of
        alerts.reset_after_sec starts it over."""
        s = self._settings
        key = f'proximity_{speaker}_{details.get("near", "")}'
        last = self._last_alert.get(key, 0)
        in_a_row = self._alerts_in_a_row.get(key, 0)
        if now - last > s.reset_after_sec:
            in_a_row = 0
        wait = s.cooldown_sec * min(2 ** in_a_row, s.cooldown_max_times)
        if last and now - last < wait:
            return False
        self._last_alert[key] = now
        self._alerts_in_a_row[key] = in_a_row + 1
        self._desk.note_alert({'time': now, 'type': 'proximity', 'character': speaker, 'near': details.get('near', ''),
                               'severity': details.get('severity', ''), 'rule_label': details.get('rule_label', '')},
                              keep=s.recent_kept)
        self._habits.bump('danger_count')
        photo = self._photos.save(self._words.photo_label, crop_boxes)
        self._bus.publish(Alert(ts=now, kind='proximity', agent=speaker,
                                extra={'snapshot': photo, 'follow_up': in_a_row, **details}))
        return True

    def _keep_safety_state(self, result: dict[str, Any], dangers: list[Danger], now: float) -> None:
        state = DANGEROUS if result['risk_level'] >= self._settings.dangerous_from_risk else SAFE
        self._desk.report(state, dangers, result, now)

    # ── pets, clutter, plants ──
    def _scenario_alert(self, now: float, scenario: str, feature: str, agent: str, text: str) -> None:
        self._bus.publish(Alert(ts=now, kind='scenario', scenario=scenario, feature=feature, agent=agent, text=text))

    def _pets(self, detections: Sequence[Detection], things: Mapping[str, Thing], chat: set[str],
              now: float) -> None:
        """A pet counts once it was seen at alerts.pet_min_confidence or more in alerts.pet_min_pictures of the
        last alerts.pet_window_pictures pictures. Then an alert goes out, at most every alerts.pet_cooldown_sec
        for each pet. After more than alerts.pet_forget_after_sec without an analysed picture every pet's memory
        starts over, so a cat that left before the gap isn't counted again."""
        s = self._settings
        if self._last_picture_ts is not None and now - self._last_picture_ts > s.pet_forget_after_sec:
            for seen in self._pet_seen.values():
                seen.clear()
        self._last_picture_ts = now
        sure = {d.label for d in detections if d.confidence >= s.pet_min_confidence}
        for pet in s.pet_labels:
            seen = self._pet_seen[pet]
            seen.append(pet in sure)
            if sum(seen) < s.pet_min_pictures:
                continue
            due = now - self._pet_alerted.get(pet, 0) > s.pet_cooldown_sec
            if not due:
                continue
            self._pet_alerted[pet] = now
            can_spill = self._profiles.ids_for_habit('hydration') + self._profiles.ids_for_habit('food')
            names = ', '.join(p.name for p in (self._profiles.get(a) for a in things if a in can_spill) if p)
            fragile = next((a for a in things if a in chat and (p := self._profiles.get(a)) and p.fragile), LENS)
            knock = self._words.pet_knock.format(names=names) if names else ''
            self._scenario_alert(now, 'risk_detection', 'pet_risk', fragile,
                                 self._words.pet.format(pet=pet.title(), knock=knock))

    def _clutter(self, labels: set[str], chat: set[str], now: float, warm: bool) -> None:
        countable = {label for label in labels if not self._profiles.is_ignored(label)}
        due = now - self._last_clutter > self._settings.clutter_cooldown_sec
        if len(countable) < self._settings.clutter_threshold or not due or not warm:
            return
        self._last_clutter = now
        speaker = next(iter(self._profiles.ids_for_habit('tasks')), LENS)
        self._scenario_alert(now, 'reminders', 'organization', speaker if speaker in chat else LENS,
                             self._words.clutter.format(count=len(countable)))

    def _plants(self, labels: set[str], chat: set[str], now: float, warm: bool) -> None:
        plants = set(self._settings.plant_labels)
        if not plants & labels or not warm or now - self._last_plant <= self._settings.plant_water_every_sec:
            return
        self._last_plant = now
        plant = next((a for a in sorted(chat) if (p := self._profiles.get(a)) and set(p.labels) & plants), LENS)
        self._scenario_alert(now, 'reminders', 'water_plants', plant, self._words.plant)
