"""
The desk monitor: safety alerts from each analysed picture.

It listens for DetectionEvents. For the agents in the chat (DeskState.objects, kept by the roster) it runs
the desk analyzer (danger zones, desk edges, things in between, crowds, things moving closer) and publishes
an Alert for each danger, with a proof photo. It also notices a pet near the desk, a cluttered desk and a
plant that may need water. A pet only counts when the detector was sure about it in enough of the last few
pictures, because an arm close to the camera can look like a cat for a moment. Timings are in settings → alerts.

What the app and the AI read about the desk's safety is kept in DeskState: safety_state, safety_dangers,
rel_result, danger_active_since and recent_events.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping, Sequence
from typing import Any

from desktalk.config import AlertsSettings
from desktalk.core.agents.speakers import LENS
from desktalk.core.vision.analyzer import DeskAnalyzer
from desktalk.core.vision.findings import Thing
from desktalk.core.vision.geometry import Box
from desktalk.core.vision.proof_photos import ProofPhotos
from desktalk.desk_state import DeskState
from desktalk.domain import Alert, Detection, DetectionEvent
from desktalk.ports import DeskStatus, EventBus, HabitsView, Profiles

PHOTO_LABEL = 'Proximity alert'


class DeskMonitor:
    def __init__(self, bus: EventBus, desk: DeskState, analyzer: DeskAnalyzer, profiles: Profiles,
                 habits: HabitsView, status: DeskStatus, photos: ProofPhotos, alerts: AlertsSettings):
        self._bus = bus
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
        self._last_clutter = 0.0
        self._last_plant = 0.0
        bus.subscribe('desk-monitor', self.on_detection, types=[DetectionEvent])

    def on_detection(self, event: DetectionEvent) -> None:
        now = event.ts
        with self._desk.lock:
            objects = dict(self._desk.objects)
            risk_on = self._desk.active_scenarios.get('risk_detection', True)
        things = {agent: Thing.from_desk(agent, obj) for agent, obj in objects.items()}
        result = self._analyzer.analyze(things, event.size, now)
        dangers = self._safety_alerts(result, things, now) if risk_on else []
        self._keep_safety_state(result, dangers, now)
        labels = {d.label for d in event.detections}
        warm = self._status.warm(now)
        self._pets(event.detections, things, now, risk_on)
        self._clutter(labels, things, now, warm)
        self._plants(labels, things, now, warm)

    # ── danger zones and desk edges ──
    def _safety_alerts(self, result: Mapping[str, Any], things: Mapping[str, Thing], now: float) -> list[dict]:
        """One alert per danger (backing off while it stays); returns the dangers for the app and the AI."""
        dangers, checked = [], set()
        for ev in result['risk_events']:
            if ev['type'] == 'zone_intrusion':
                intruder, target = ev['intruder'], ev['target']
                if frozenset((intruder, target)) in checked:
                    continue
                checked.add(frozenset((intruder, target)))
                owner = self._profiles.get(target)
                speaker = target if owner and owner.fragile else intruder      # the fragile one complains
                other = target if speaker == intruder else intruder
                self._proximity_alert(speaker, [things[intruder].box, things[target].box], now, near=other,
                                      severity='high', rule_label=ev['zone_label'], depth_px=ev['depth_px'])
                dangers.append({'pair': [ev['intruder_class'], ev['target_class']], 'label': ev['zone_label'],
                                'depth_px': ev['depth_px'], 'type': 'zone_intrusion'})
            elif ev['type'] == 'edge_danger':
                agent = ev['object']
                self._proximity_alert(agent, [things[agent].box], now, severity='medium',
                                      rule_label=f"Near {ev['edge']} edge ({int(ev['distance_px'])}px)")
                dangers.append({'pair': [ev['class'], 'desk_edge'], 'label': f"{ev['class']} near {ev['edge']} edge",
                                'type': 'edge_danger'})
        return dangers

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
        with self._desk.lock:
            self._desk.recent_events.append({'time': now, 'type': 'proximity', 'character': speaker,
                                             'near': details.get('near', ''), 'severity': details.get('severity', ''),
                                             'rule_label': details.get('rule_label', '')})
            self._desk.recent_events = self._desk.recent_events[-s.recent_kept:]
        self._habits.bump('danger_count')
        photo = self._photos.save(PHOTO_LABEL, crop_boxes)
        self._bus.publish(Alert(ts=now, kind='proximity', agent=speaker,
                                extra={'snapshot': photo, 'follow_up': in_a_row, **details}))
        return True

    def _keep_safety_state(self, result: dict[str, Any], dangers: list[dict], now: float) -> None:
        desk = self._desk
        with desk.lock:
            desk.safety_state = 'DANGEROUS' if result['risk_level'] >= self._settings.dangerous_from_risk else 'SAFE'
            desk.safety_dangers = dangers
            desk.rel_result = result
            active = {frozenset(d['pair']) for d in dangers}
            for pair in active:
                desk.danger_active_since.setdefault(pair, now)
            for pair in list(desk.danger_active_since):
                if pair not in active:
                    del desk.danger_active_since[pair]

    # ── pets, clutter, plants ──
    def _scenario_alert(self, now: float, scenario: str, feature: str, agent: str, text: str) -> None:
        self._bus.publish(Alert(ts=now, kind='scenario', scenario=scenario, feature=feature, agent=agent, text=text))

    def _pets(self, detections: Sequence[Detection], things: Mapping[str, Thing], now: float, risk_on: bool) -> None:
        """A pet counts once it was seen at alerts.pet_min_confidence or more in alerts.pet_min_pictures of the
        last alerts.pet_window_pictures pictures. Then an alert goes out, at most every alerts.pet_cooldown_sec
        for each pet; while risk_detection is off the alert is skipped but the wait still starts."""
        s = self._settings
        sure = {d.label for d in detections if d.confidence >= s.pet_min_confidence}
        for pet in s.pet_labels:
            seen = self._pet_seen[pet]
            seen.append(pet in sure)
            if sum(seen) < s.pet_min_pictures:
                continue
            due = now - self._pet_alerted.get(pet, 0) > s.pet_cooldown_sec
            if due:
                self._pet_alerted[pet] = now
            if not (due and risk_on):
                continue
            can_spill = self._profiles.ids_for_habit('hydration') + self._profiles.ids_for_habit('food')
            names = ', '.join(p.name for p in (self._profiles.get(a) for a in things if a in can_spill) if p)
            fragile = next((a for a in things if (p := self._profiles.get(a)) and p.fragile), LENS)
            self._scenario_alert(now, 'risk_detection', 'pet_risk', fragile,
                                 f'{pet.title()} detected near the desk!'
                                 + (f' {names} could get knocked over!' if names else '') + ' Careful!')

    def _clutter(self, labels: set[str], things: Mapping[str, Thing], now: float, warm: bool) -> None:
        countable = {label for label in labels if not self._profiles.is_ignored(label)}
        due = now - self._last_clutter > self._settings.clutter_cooldown_sec
        if len(countable) < self._settings.clutter_threshold or not due or not warm:
            return
        self._last_clutter = now
        speaker = next(iter(self._profiles.ids_for_habit('tasks')), LENS)
        self._scenario_alert(now, 'reminders', 'organization', speaker if speaker in things else LENS,
                             f'I count {len(countable)} things on the desk right now. Maybe tidy up a bit?')

    def _plants(self, labels: set[str], things: Mapping[str, Thing], now: float, warm: bool) -> None:
        plants = set(self._settings.plant_labels)
        if not plants & labels or not warm or now - self._last_plant <= self._settings.plant_water_every_sec:
            return
        self._last_plant = now
        plant = next((a for a in things if (p := self._profiles.get(a)) and set(p.labels) & plants), LENS)
        self._scenario_alert(now, 'reminders', 'water_plants', plant,
                             'I can see a plant on the desk. Has it had any water today?')
