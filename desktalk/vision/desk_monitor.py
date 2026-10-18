"""
The desk monitor: safety alerts from desk geometry.

A consumer of detection events. For the agents currently in the chat it runs
the relationship engine (relationships.py: danger zones, desk edges, chain
reactions, objects approaching) and publishes "alert" events, each with a
cropped proof photo. It also handles the other camera alerts that were in the
original server: pets near the desk, clutter, and the plant that needs water.
Timings: config/settings.yaml → alerts.
"""

from __future__ import annotations

import time

from ..events.stream import ALERT

PET_LABELS = {'cat', 'dog'}
PLANT_LABELS = {'potted plant', 'flower', 'houseplant'}


class DeskMonitor:
    def __init__(self, app):
        self.app = app
        s = app.settings.alerts
        self.cooldown = float(s.get('cooldown_sec', 25))
        self.reset_after = float(s.get('reset_after_sec', 300))
        self.pet_cooldown = float(s.get('pet_cooldown_sec', 120))
        self.clutter_threshold = int(s.get('clutter_threshold', 6))
        self.clutter_cooldown = float(s.get('clutter_cooldown_sec', 1800))
        self.plant_every = float(s.get('plant_water_every_sec', 86400))

    # ── one alert per pair, backing off while the problem stays ──
    def emit_interaction(self, speaker, cam, crop_bboxes, now, **kw) -> bool:
        desk = self.app.desk
        key = f'proximity_{speaker}_{kw.get("near", "")}'
        with desk.lock:
            last = desk.cooldowns.get(key, 0)
            count = desk.interaction_counts.get(key, 0)
            if now - last > self.reset_after:
                count = 0
            wait = self.cooldown * min(2 ** count, 8)
            if last and now - last < wait:
                return False
            desk.cooldowns[key] = now
            desk.interaction_counts[key] = count + 1
            desk.recent_events.append({'time': now, 'type': 'proximity', 'character': speaker,
                                       'near': kw.get('near', ''), 'severity': kw.get('severity', ''),
                                       'rule_label': kw.get('rule_label', '')})
            desk.recent_events = desk.recent_events[-30:]
        self.app.habits.bump('danger_count')
        snap_label = kw.pop('snapshot_label', 'Proximity alert')
        snap = desk.save_snapshot(cam_idx=cam, crop_bboxes=crop_bboxes, label=snap_label)
        self.app.stream.publish(ALERT, ts=now, kind='proximity', agent=speaker, snapshot=snap,
                                follow_up=count, **kw)
        return True

    def scenario_alert(self, now, scenario, feature, agent, text, **kw):
        self.app.stream.publish(ALERT, ts=now, kind='scenario', scenario=scenario, feature=feature,
                                agent=agent, text=text, **kw)

    @staticmethod
    def _bboxes(objects, ids):
        return [objects[i]['bbox'] for i in ids if i in objects]

    # ── the consumer ──
    def on_detection(self, ev: dict) -> None:
        app, desk = self.app, self.app.desk
        now = (float(ev['ts']) if ev.get('ts') is not None else time.time())
        with desk.lock:
            objects = dict(desk.objects)
            fw, fh = desk.frame_w or 640, desk.frame_h or 480
            risk_on = desk.active_scenarios.get('risk_detection', True)
        app.analyzer.update_frame_size(fw, fh)
        result = app.analyzer.analyze(objects)
        dangers, checked = [], set()
        for rev in result.get('risk_events', []) if risk_on else []:
            if rev['type'] == 'zone_intrusion':
                intruder, target = rev['intruder'], rev['target']
                pair = frozenset({intruder, target})
                if pair in checked:
                    continue
                checked.add(pair)
                tp = app.profiles.get(target)
                speaker = target if (tp and tp.fragile) else intruder
                other = target if speaker == intruder else intruder
                self.emit_interaction(speaker, 0, self._bboxes(objects, [intruder, target]), now,
                                      near=other, severity='high', rule_label=rev.get('zone_label', ''),
                                      depth_px=rev.get('depth_px', 0))
                dangers.append({'pair': [rev['intruder_class'], rev['target_class']],
                                'label': rev.get('zone_label', ''), 'depth_px': rev.get('depth_px', 0),
                                'type': 'zone_intrusion'})
            elif rev['type'] == 'edge_danger':
                pid = rev['object']
                if pid in objects:
                    self.emit_interaction(pid, 0, self._bboxes(objects, [pid]), now, severity='medium',
                                          rule_label=f"Near {rev['edge']} edge ({int(rev['distance_px'])}px)")
                    dangers.append({'pair': [rev['class'], 'desk_edge'],
                                    'label': f"{rev['class']} near {rev['edge']} edge", 'type': 'edge_danger'})
        risk_level = result.get('risk_level', 0)
        with desk.lock:
            desk.safety_state = 'DANGEROUS' if risk_level >= 2 else 'SAFE'
            desk.safety_dangers = dangers
            desk.rel_result = result
            active = set()
            for d in dangers:
                pair = frozenset(d['pair'])
                active.add(pair)
                desk.danger_active_since.setdefault(pair, now)
            for pair in list(desk.danger_active_since):
                if pair not in active:
                    del desk.danger_active_since[pair]

        labels = {o['label'] for o in ev.get('detected_obj') or []}
        warm = app.warm(now)
        # Pets near the desk
        for pet in PET_LABELS & labels:
            with desk.lock:
                desk.pet_last_seen[pet] = now
                due = now - desk.pet_near_cooldown.get(pet, 0) > self.pet_cooldown
                if due:
                    desk.pet_near_cooldown[pet] = now
            if due and risk_on:
                at_risk = [pid for pid in objects if pid in (app.profiles.ids_for_habit('hydration')
                                                             + app.profiles.ids_for_habit('food'))]
                names = ', '.join(app.profiles.get(p).name for p in at_risk if app.profiles.get(p))
                fragile = next((p for p in objects if app.profiles.get(p) and app.profiles.get(p).fragile), 'lens')
                self.scenario_alert(now, 'risk_detection', 'pet_risk', fragile,
                                    f'{pet.title()} detected near the desk!'
                                    + (f' {names} could get knocked over!' if names else '') + ' Careful!')
        # Clutter
        countable = {l for l in labels if not app.profiles.is_ignored(l)}
        with desk.lock:
            due = now - desk.last_clutter_alert > self.clutter_cooldown
        if len(countable) >= self.clutter_threshold and due and warm:
            with desk.lock:
                desk.last_clutter_alert = now
            speaker = next(iter(app.profiles.ids_for_habit('tasks')), 'lens')
            speaker = speaker if speaker in objects else 'lens'
            self.scenario_alert(now, 'reminders', 'organization', speaker,
                                f'I count {len(countable)} things on the desk right now. Maybe tidy up a bit?')
        # A plant on the desk
        if PLANT_LABELS & labels and warm:
            with desk.lock:
                due = now - desk.last_water_plants_reminder > self.plant_every
                if due:
                    desk.last_water_plants_reminder = now
            if due:
                plant = next((p for p in objects if app.profiles.get(p) and
                              set(app.profiles.get(p).labels) & PLANT_LABELS), 'lens')
                self.scenario_alert(now, 'reminders', 'water_plants', plant,
                                    'I can see a plant on the desk. Has it had any water today?')
