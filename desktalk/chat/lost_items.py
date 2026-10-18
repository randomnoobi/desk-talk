"""
Lost items: "have you seen my keys?"

The tracked items are listed in config/settings.yaml (lost_items.items), each
with the detector words for it and the words you might type.

How it works (same framework as before):
  1. Every picture (detection event): if a tracked item is in it, note the time
     and what was next to it. When one newly appears, save a "Spotted" photo.
  2. Every minute: if the picture changed (or 5 minutes passed), an optional
     Ollama vision scan looks for small things YOLO misses.
  3. If an item has been gone for its alert time, the group chat hears about it.
  4. When you ask, the answer uses those sightings plus tagged desk photos.
"""

from __future__ import annotations

import json
import re
import threading
import time

import cv2
import numpy as np

from ..events.stream import ALERT
from ..util import format_local_ts
from . import prompts

TRIGGERS = (
    'seen my', 'see my', 'saw my', 'where is my', 'where are my', "where's my", 'wheres my',
    'lost my', 'anyone see', 'anyone seen', 'anybody see', 'anybody seen',
    'have you seen', 'did you see', 'did anyone see', 'last see', 'do you see my',
    'where did i put', 'find my', 'spotted my', "can't find", 'cant find',
    'looking for my', 'look for my', 'missing my', 'picked up my',
)
CTA = {'label': '🖼 Desk captures', 'action': 'captures'}


def gray_small(jpeg_bytes, size=64):
    arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
    if img is None:
        return None
    return cv2.resize(img, (size, size), interpolation=cv2.INTER_AREA)


def scene_mean_diff(a, b) -> float:
    if a is None or b is None or a.shape != b.shape:
        return 1e9
    return float(np.mean(cv2.absdiff(a, b)))


def _words(text: str) -> set:
    return set(re.findall(r'[a-z0-9]+', (text or '').lower()))


class LostItems:
    def __init__(self, app):
        self.app = app
        s = app.settings.lost_items
        self.every = float(s.get('check_every_sec', 60))
        self.scene_thr = float(s.get('scene_change', 8.0))
        self.rescan = float(s.get('rescan_every_sec', 300))
        self.scan_photo_every = float(s.get('snapshot_every_sec', 120))
        self.spotted_cd = float(s.get('spotted_photo_cooldown_sec', 75))
        self.items = {}
        for iid, it in (s.get('items') or {}).items():
            self.items[iid] = {
                'name': it.get('name', iid.title()),
                'labels': {str(x).lower() for x in it.get('labels') or []},
                'alert_after': float(it.get('alert_after_min', 15)) * 60,
                'words': {str(x).lower() for x in it.get('words') or [iid]},
            }

    # ── matching ──
    def items_in_text(self, text: str) -> list[str]:
        w = _words(text)
        return [iid for iid, it in self.items.items() if w & it['words']]

    def items_in_labels(self, labels) -> set[str]:
        labs = {str(l).lower() for l in labels}
        out = set()
        for iid, it in self.items.items():
            if labs & it['labels']:
                out.add(iid)
                continue
            for l in labs:          # vision answers are free text: "car keys", "airpods case"
                if 'keyboard' in l and iid == 'keys':
                    continue
                if _words(l) & it['words']:
                    out.add(iid)
                    break
        return out

    @staticmethod
    def is_query(text: str) -> bool:
        t = (text or '').lower()
        return any(s in t for s in TRIGGERS)

    def is_lost_query(self, text: str) -> bool:
        return self.is_query(text) and (bool(self.items_in_text(text)) or bool(
            _words(text) & {'laptop', 'cup', 'bottle', 'book', 'remote', 'card', 'scissors'}))

    # ── 1. every picture ──
    def on_detection(self, ev: dict) -> None:
        app, desk = self.app, self.app.desk
        now = (float(ev['ts']) if ev.get('ts') is not None else time.time())
        labels = {o['label'] for o in ev.get('detected_obj') or [] if o.get('conf', 0) >= 0.3}
        present = self.items_in_labels(labels)
        names = [app.profiles.get(p).name for p in app.roster.member_ids() if app.profiles.get(p)]
        spotted = []
        with desk.lock:
            if not desk.active_scenarios.get('lost_object', True):
                return
            for iid in present:
                e = desk.lost_objects.setdefault(iid, {'last_seen': None, 'alerted': False, 'source': None, 'last_near': []})
                e.update(last_seen=now, alerted=False, source='detector')
                e['last_near'] = [n for n in names if n.lower() != self.items[iid]['name'].lower()][:5]
            if not desk.loseable_snap_armed:
                desk.loseable_snap_armed = True          # the first picture is the baseline
            else:
                for iid in present - desk.loseable_snap_prev:
                    k = f'yolo:{iid}'
                    if now - desk.loseable_snap_last_ts.get(k, 0) >= self.spotted_cd:
                        desk.loseable_snap_last_ts[k] = now
                        spotted.append(f'Spotted: {self.items[iid]["name"]}')
            desk.loseable_snap_prev = set(present)
        for label in spotted:
            desk.save_snapshot(label=label)

    # ── 2 + 3. every minute ──
    def start(self):
        threading.Thread(target=self._loop, daemon=True, name='LostItems').start()

    def _loop(self):
        time.sleep(5)
        while True:
            try:
                self.tick(time.time())
            except Exception as exc:
                print(f'  [lost-items] {exc!r}')
            time.sleep(self.every)

    def tick(self, now: float) -> None:
        app, desk = self.app, self.app.desk
        with desk.lock:
            if not desk.active_scenarios.get('lost_object', True):
                return
            jpg = desk.first_raw_frame()
            prev = desk.lost_prev_gray
            last_scan = desk.lost_last_scan_ts
        if not jpg:
            return
        gray = gray_small(jpg)
        changed = scene_mean_diff(prev, gray) >= self.scene_thr if prev is not None else True
        heartbeat = (now - last_scan) >= self.rescan if last_scan > 0 else True
        with desk.lock:
            if gray is not None:
                desk.lost_prev_gray = np.copy(gray)
            desk.lost_last_scan_ts = now
        vlm_hits, ran = set(), False
        if (changed or heartbeat) and app.llm.vision_client is not None:
            vlm_hits = self._vision_scan(jpg, now)
            ran = True
        else:
            with desk.lock:
                vlm_hits = set(desk.lost_last_vlm_visible)
        self._alerts(now, vlm_hits if ran else set())

    def _vision_scan(self, jpg, now) -> set:
        app, desk = self.app, self.app.desk
        names = ', '.join(it['name'] for it in self.items.values())
        raw = app.llm.vision(jpg, (
            'Look at the image. For desk lost-item tracking reply ONLY this JSON, no other text: '
            '{"visible":["label",...]}. Labels: short English for visible items such as '
            f'{names}, keys, airpods, wallet, phone, scissors. Omit items you are not confident about.'))
        hits = set()
        if raw:
            m = re.search(r'\{[\s\S]*\}', raw)
            try:
                data = json.loads(m.group(0) if m else raw)
                hits = {str(x).strip().lower() for x in data.get('visible', []) if x}
            except (ValueError, AttributeError):
                hits = set()
        found = self.items_in_labels(hits)
        names_on = [app.profiles.get(p).name for p in app.roster.member_ids() if app.profiles.get(p)]
        snap_labels = []
        with desk.lock:
            prev_found = self.items_in_labels(desk.lost_last_vlm_visible)
            for iid in found:
                e = desk.lost_objects.setdefault(iid, {'last_seen': None, 'alerted': False, 'source': None, 'last_near': []})
                if e.get('source') != 'detector' or not e.get('last_seen') or now - e['last_seen'] > 5:
                    e.update(last_seen=now, alerted=False, source='vision', last_near=(names_on + sorted(hits))[:5])
            if desk.loseable_vlm_snap_armed:
                for iid in found - prev_found:
                    k = f'vlm:{iid}'
                    if now - desk.loseable_snap_last_ts.get(k, 0) >= self.spotted_cd:
                        desk.loseable_snap_last_ts[k] = now
                        snap_labels.append(f'Spotted: {self.items[iid]["name"]} (vision)')
            desk.loseable_vlm_snap_armed = True
            desk.lost_last_vlm_ts = now
            desk.lost_last_vlm_visible = set(hits)
            scan_due = now - desk.last_desk_scan_snapshot_ts >= self.scan_photo_every
        for lbl in snap_labels:
            desk.save_snapshot(label=lbl)
        if scan_due and desk.save_snapshot(label='Desk scan'):
            with desk.lock:
                desk.last_desk_scan_snapshot_ts = time.time()
        return hits

    def _alerts(self, now, vlm_hits):
        app, desk = self.app, self.app.desk
        with desk.lock:
            labels_now = list(desk.seen_labels)
        seen_now = self.items_in_labels(labels_now) | self.items_in_labels(vlm_hits)
        for iid, it in self.items.items():
            if app.phone_is_camera and iid == 'phone':
                continue        # the phone is the camera, so it is never on the desk
            if iid in seen_now:
                continue
            with desk.lock:
                e = desk.lost_objects.get(iid)
                if not e or not e.get('last_seen') or e.get('alerted'):
                    continue
                gone = now - e['last_seen']
                if gone < it['alert_after']:
                    continue
                e['alerted'] = True
                near = list(e.get('last_near', []))
            mins = int(gone / 60)
            hint = f' Last seen near: {", ".join(near[:3])}.' if near else ''
            text = None
            if app.llm.ready:
                text = app.llm.chat(prompts.lost_alert(it['name'], mins, hint), max_tokens=60, temperature=0.7,
                                    background=True)
            text = text or f"{it['name']}: hasn't been seen for {mins} min.{hint}"
            app.habits.bump('lost_alerts')
            app.stream.publish(ALERT, ts=now, kind='scenario', scenario='lost_object', feature=iid,
                               agent='lens', text=text)

    # ── 4. answering "have you seen my keys?" ──
    def _snapshot_with(self, iid):
        with self.app.desk.lock:
            order = list(self.app.desk.snapshot_order)
        for entry in order:
            tags = set(entry.get('labels') or []) | set(entry.get('vlm_labels') or [])
            if iid in self.items_in_labels(tags):
                return entry
        return None

    def _vision_note(self, iid, user_message):
        app = self.app
        if app.llm.vision_client is None:
            return None
        name = self.items[iid]['name']
        meta = self._snapshot_with(iid)
        if meta:
            with app.desk.lock:
                jpg = app.desk.snapshots.get(meta['id'])
            if jpg:
                ctx = f'Saved desk photo from {format_local_ts(meta.get("ts"))}' + (
                    f' — {meta.get("label")}' if meta.get('label') else '') + '.'
                vn = app.llm.vision(jpg, f'{ctx} The user is looking for their {name}. Describe where it appears in '
                                         f'THIS image — left/right, near laptop, under paper, etc. If you truly '
                                         f'cannot see it, say so in one sentence.')
                if vn and len(vn.split()) >= 3:
                    return vn.strip()[:450]
        with app.desk.lock:
            jpg = app.desk.first_raw_frame()
        if jpg:
            vn = app.llm.vision(jpg, f'Is a {name} visible on the desk in this image? Reply with one short sentence. '
                                     f'Start with "Visible:" or "Not visible:". If visible, mention where on the '
                                     f'desk relative to other objects.')
            if vn:
                return vn.strip()[:220]
        return None

    def answer(self, user_message: str) -> dict:
        """Reply for the chat: {'text', 'cta', 'speaker'}."""
        app = self.app
        now = time.time()
        wanted = self.items_in_text(user_message)
        with app.desk.lock:
            records = {k: dict(v) for k, v in app.desk.lost_objects.items()}
            vlm_vis = sorted(app.desk.lost_last_vlm_visible)
        members = app.roster.member_ids()
        speaker = next((p for p in members if 'tasks' in (app.profiles.get(p).habits if app.profiles.get(p) else [])),
                       'lens')
        if not wanted:
            note = None
            with app.desk.lock:
                jpg = app.desk.first_raw_frame()
            if jpg and app.llm.vision_client is not None:
                note = app.llm.vision(jpg, f'The user asked: "{user_message[:200]}". Is that item visible on this '
                                           f'desk? Answer in one or two friendly sentences, saying where it is.')
            return {'speaker': speaker, 'cta': CTA, 'text': note or (
                "I checked the desk camera but couldn't confidently spot that. Try naming it directly "
                "(keys, phone, wallet, AirPods…), or browse saved desk photos below.")}
        notes = {}
        for iid in wanted:
            if not (records.get(iid) or {}).get('last_seen'):
                vn = self._vision_note(iid, user_message)
                if vn:
                    notes[iid] = vn
        facts, plain = [], []
        for iid in wanted:
            name = self.items[iid]['name']
            e = records.get(iid) or {}
            if e.get('last_seen'):
                ago = int(now - e['last_seen'])
                when = format_local_ts(e['last_seen'])
                near = f' near {", ".join(e.get("last_near", [])[:3])}' if e.get('last_near') else ''
                if ago < 90:
                    facts.append(f'- your {name}: seen on the desk within ~{ago}s ({when}){near}.')
                    plain.append(f"I've got your {name} on the desk within the last minute or so ({when}){near}.")
                else:
                    m = max(1, ago // 60)
                    facts.append(f'- your {name}: last seen on the desk ~{m} min ago ({when}){near}.')
                    plain.append(f'I last noted your {name} on the desk around {when}, about {m} minute(s) ago{near}.')
            else:
                extra = f' Vision: {notes[iid]}' if iid in notes else (
                    f' Last wide scan labels: {", ".join(vlm_vis)}.' if vlm_vis else '')
                facts.append(f'- your {name}: no desk sighting in this tracking window.{extra}')
                plain.append(f"I'm not seeing a saved sighting of your {name} on the desk since we started."
                             + (f' A quick look at the live picture: {notes[iid]}' if iid in notes else
                                ' Captures (in the sidebar, or the button below) has every desk photo with its time.'))
        text = None
        if app.llm.ready:
            a = app.profiles.get(speaker)
            system = prompts.LOST_ITEM_SYSTEM.format(name=a.name if a else 'Lens',
                                                     obj=a.object if a else 'camera')
            text = app.llm.chat([{'role': 'system', 'content': system},
                                 {'role': 'user', 'content': f'User wrote:\n{user_message[:500]}\n\nFACTS:\n' + '\n'.join(facts)}],
                                max_tokens=200, temperature=0.35)
        return {'speaker': speaker, 'cta': CTA, 'text': (text or '\n\n'.join(plain)).strip()}
