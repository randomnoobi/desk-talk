"""
Lost items: "have you seen my keys?"

The tracked items are listed in config/settings.yaml (lost_items.items), each with the detector
words for it and the words you might type.

  1. Every analysed picture (DetectionEvent): if a tracked item is in it, note the time and which
     agents were in the chat. When one turns up again, save a "Spotted" photo.
  2. Every lost_items.check_every_sec: if the picture changed (or it's been rescan_every_sec), a vision
     AI on this computer (Ollama) looks for small things the detector misses.
  3. If an item has been gone for its alert time, Lens tells the chat (an Alert event).
  4. When you ask, the answer uses those sightings, plus a look at a saved photo or the live picture.

    lost = LostItems(clock, bus, desk, roster, profiles, habits, chat_model, ollama_or_none, config.lost_items,
                     config.llm.replies, photos=proof_photos,
                     default_temperature=config.agents_file.default.temperature,
                     phone_is_camera=config.camera.source == 'phone')
    supervisor.add(lost, start_after=config.lost_items.start_delay_sec)
    lost.items, lost.is_lost_query(text), lost.answer(text)     # for the web routes
"""

from __future__ import annotations

import logging
import re
import threading
from collections.abc import Iterable
from typing import Any

import cv2
import numpy as np

from desktalk.config import AiReplies, LostItemsSettings
from desktalk.core.agents.speakers import LENS, speaker_for
from desktalk.core.chat import prompts
from desktalk.core.chat.workers.ai import ask, look
from desktalk.core.chat.workers.text import json_in
from desktalk.core.vision.proof_photos import ProofPhotos
from desktalk.desk_state import DeskState
from desktalk.domain import Alert, DetectionEvent, Event
from desktalk.ports import ChatModel, Clock, EventBus, HabitsView, Profiles, RosterView
from desktalk.util import format_local_ts

log = logging.getLogger(__name__)

TRIGGERS = (
    'seen my', 'see my', 'saw my', 'where is my', 'where are my', "where's my", 'wheres my',
    'lost my', 'anyone see', 'anyone seen', 'anybody see', 'anybody seen',
    'have you seen', 'did you see', 'did anyone see', 'last see', 'do you see my',
    'where did i put', 'find my', 'spotted my', "can't find", 'cant find',
    'looking for my', 'look for my', 'missing my', 'picked up my',
)
OTHER_THINGS = {'laptop', 'cup', 'bottle', 'book', 'remote', 'card', 'scissors'}   # askable, though not tracked
CTA = {'label': '🖼 Desk captures', 'action': 'captures'}
NOT_SPOTTED = ("I checked the desk camera but couldn't confidently spot that. Try naming it directly "
               "(keys, phone, wallet, AirPods…), or browse saved desk photos below.")


def _words(text: str) -> set[str]:
    return set(re.findall(r'[a-z0-9]+', (text or '').lower()))


def _small_gray(jpeg: bytes, size: int) -> np.ndarray | None:
    picture = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_GRAYSCALE)
    if picture is None:
        return None
    return cv2.resize(picture, (size, size), interpolation=cv2.INTER_AREA)


def _difference(a: np.ndarray | None, b: np.ndarray | None) -> float:
    """How different two small gray pictures look, 0-255 (a missing one counts as completely different)."""
    if a is None or b is None or a.shape != b.shape:
        return float('inf')
    return float(np.mean(cv2.absdiff(a, b)))


def _new_record() -> dict[str, Any]:
    return {'last_seen': None, 'alerted': False, 'source': None, 'last_near': []}


class LostItems:
    """A PeriodicWorker that also listens to DetectionEvents. `vision` is the AI that looks at pictures,
    or None when no local vision AI is set up (no scans, as before: a cloud AI's quota is kept for the chat)."""

    name = 'lost items'

    def __init__(self, clock: Clock, bus: EventBus, desk: DeskState, roster: RosterView, profiles: Profiles,
                 habits: HabitsView, chat: ChatModel, vision: ChatModel | None, settings: LostItemsSettings,
                 replies: AiReplies, *, photos: ProofPhotos, default_temperature: float,
                 phone_is_camera: bool = False):
        """`replies`: settings → llm.replies (this uses lost_alert and lost_answer). `default_temperature`:
        agents.yaml's default, for Lens when those settings have none. `phone_is_camera`: the phone can't be lost."""
        self._clock = clock
        self._bus = bus
        self._desk = desk
        self._roster = roster
        self._profiles = profiles
        self._habits = habits
        self._chat = chat
        self._vision = vision
        self._photos = photos
        self._settings = settings
        self._replies = replies
        self._default_temperature = default_temperature
        self._phone_is_camera = phone_is_camera
        self.items: dict[str, dict[str, Any]] = {
            item_id: {'name': item.name, 'labels': {label.lower() for label in item.labels},
                      'alert_after': item.alert_after_min * 60,
                      'words': {word.lower() for word in item.words} or {item_id}}
            for item_id, item in settings.items.items()}
        self._lock = threading.Lock()      # pictures, the timer and the web routes all come in on their own threads
        self._seen: dict[str, dict[str, Any]] = {}       # item id → last_seen, alerted, source, last_near
        self._first_picture_done = False
        self._in_last_picture: set[str] = set()
        self._spotted_photo_at: dict[str, float] = {}    # 'detector:keys' / 'vision:keys' → when
        self._first_scan_done = False
        self._last_gray: np.ndarray | None = None
        self._last_scan: float | None = None
        self._desk_photo_at: float | None = None
        bus.subscribe('lost-items', self.on_detection, types=[DetectionEvent])

    # ── words ──
    def items_in_text(self, text: str) -> list[str]:
        words = _words(text)
        return [item_id for item_id, item in self.items.items() if words & item['words']]

    def items_in_labels(self, labels: Iterable[str]) -> set[str]:
        """Tracked items among detector labels or the vision scan's free text ("car keys", "airpods case")."""
        labels = {str(label).lower() for label in labels}
        found = set()
        for item_id, item in self.items.items():
            if labels & item['labels']:
                found.add(item_id)
                continue
            for label in labels:
                if 'keyboard' in label and item_id == 'keys':
                    continue
                if _words(label) & item['words']:
                    found.add(item_id)
                    break
        return found

    @staticmethod
    def is_query(text: str) -> bool:
        lowered = (text or '').lower()
        return any(trigger in lowered for trigger in TRIGGERS)

    def is_lost_query(self, text: str) -> bool:
        """A "have you seen my ...?" question about a tracked item or another everyday thing."""
        return self.is_query(text) and (bool(self.items_in_text(text)) or bool(_words(text) & OTHER_THINGS))

    def sightings(self) -> dict[str, dict[str, Any]]:
        """Item id → last_seen, alerted, source (detector / vision), last_near."""
        with self._lock:
            return {item_id: dict(record, last_near=list(record['last_near']))
                    for item_id, record in self._seen.items()}

    def _member_names(self) -> list[str]:
        profiles = (self._profiles.get(agent_id) for agent_id in self._roster.member_ids())
        return [p.name for p in profiles if p]

    def _photo_due(self, key: str, now: float) -> bool:
        """A "Spotted" photo, unless the same one was taken moments ago (call with the lock held)."""
        last = self._spotted_photo_at.get(key)
        if last is not None and now - last < self._settings.spotted_photo_cooldown_sec:
            return False
        self._spotted_photo_at[key] = now
        return True

    # ── 1. every picture ──
    def on_detection(self, event: Event) -> None:
        if not isinstance(event, DetectionEvent):
            return
        now = event.ts
        labels = {d.label for d in event.detections if d.confidence >= self._settings.min_confidence}
        present = self.items_in_labels(labels)
        names = self._member_names()
        spotted = []
        with self._lock:
            for item_id in present:
                item_name = self.items[item_id]['name'].lower()
                near = [n for n in names if n.lower() != item_name][:self._settings.nearby_kept]
                self._seen.setdefault(item_id, _new_record()).update(
                    last_seen=now, alerted=False, source='detector', last_near=near)
            if self._first_picture_done:
                spotted = [f'Spotted: {self.items[i]["name"]}' for i in sorted(present - self._in_last_picture)
                           if self._photo_due(f'detector:{i}', now)]
            self._first_picture_done = True          # the first picture is only the starting point
            self._in_last_picture = set(present)
        for label in spotted:
            self._photos.save(label)

    # ── 2 + 3. on a timer ──
    def tick(self, now: float) -> float:
        with self._desk.lock:
            jpeg = self._desk.first_raw_frame()
        if not jpeg:
            return self._settings.check_every_sec
        scan_hits: set[str] = set()
        if self._vision is not None and self._scan_due(jpeg, now):
            scan_hits = self._scan(jpeg, now)
        self._alert_lost_items(now, scan_hits)
        return self._settings.check_every_sec

    def _scan_due(self, jpeg: bytes, now: float) -> bool:
        gray = _small_gray(jpeg, self._settings.compare_size)
        with self._lock:
            changed = _difference(self._last_gray, gray) >= self._settings.scene_change
            if gray is not None:
                self._last_gray = gray
            return changed or self._last_scan is None or now - self._last_scan >= self._settings.rescan_every_sec

    def _scan(self, jpeg: bytes, now: float) -> set[str]:
        """Ask the vision AI what small things are on the desk. Returns the words it saw."""
        item_names = ', '.join(item['name'] for item in self.items.values())
        answer = json_in(look(self._vision, jpeg, prompts.lost_scan(item_names), background=True,
                              what='the lost-items scan'))
        with self._lock:
            self._last_scan = now
        if answer is None:
            return set()
        visible = answer.get('visible')
        hits = {str(x).strip().lower() for x in visible if x} if isinstance(visible, list) else set()
        found = self.items_in_labels(hits)
        nearby = (self._member_names() + sorted(hits))[:self._settings.nearby_kept]
        with self._desk.lock:
            seen_before = self.items_in_labels(self._desk.lost_last_vlm_visible)
            self._desk.lost_last_vlm_visible = set(hits)     # also tags the photos saved below
        spotted = []
        with self._lock:
            for item_id in found:
                record = self._seen.setdefault(item_id, _new_record())
                fresh_detector = (record['source'] == 'detector' and record['last_seen'] is not None
                                  and now - record['last_seen'] <= self._settings.detector_fresh_sec)
                if not fresh_detector:
                    record.update(last_seen=now, alerted=False, source='vision', last_near=list(nearby))
            if self._first_scan_done:
                spotted = [f'Spotted: {self.items[i]["name"]} (vision)' for i in sorted(found - seen_before)
                           if self._photo_due(f'vision:{i}', now)]
            self._first_scan_done = True
            desk_photo_due = (self._desk_photo_at is None
                              or now - self._desk_photo_at >= self._settings.snapshot_every_sec)
        for label in spotted:
            self._photos.save(label)
        if desk_photo_due and self._photos.save('Desk scan'):
            with self._lock:
                self._desk_photo_at = now
        return hits

    def _alert_lost_items(self, now: float, scan_hits: set[str]) -> None:
        with self._desk.lock:
            labels_now = list(self._desk.seen_labels)
        seen_now = self.items_in_labels(labels_now) | self.items_in_labels(scan_hits)
        for item_id, item in self.items.items():
            if item_id in seen_now or (self._phone_is_camera and item_id == 'phone'):
                continue        # (the phone is the camera, so it's never on the desk)
            with self._lock:
                record = self._seen.get(item_id)
                if not record or record['last_seen'] is None or record['alerted']:
                    continue
                gone = now - record['last_seen']
                if gone < item['alert_after']:
                    continue
                record['alerted'] = True
                near = list(record['last_near'])
            self._alert(item_id, item['name'], gone, near, now)

    def _alert(self, item_id: str, name: str, gone: float, near: list[str], now: float) -> None:
        minutes = int(gone / 60)
        shown = near[:self._settings.nearby_mentioned]
        hint = f' Last seen near: {", ".join(shown)}.' if shown else ''
        text = ask(self._chat, prompts.lost_alert(name, minutes, hint), self._replies.lost_alert,
                   own_temperature=self._default_temperature, background=True, what='a lost-item alert')
        text = text or f"{name}: hasn't been seen for {minutes} min.{hint}"
        self._habits.bump('lost_alerts')
        self._bus.publish(Alert(ts=now, kind='scenario', scenario='lost_object', feature=item_id, agent=LENS,
                                text=text))
        log.info('Lost item: %s', text)

    # ── 4. answering "have you seen my keys?" ──
    def answer(self, user_message: str) -> dict[str, Any]:
        """The chat's reply: {'speaker', 'cta', 'text'}."""
        now = self._clock.now()
        wanted = self.items_in_text(user_message)
        records = self.sightings()
        with self._desk.lock:
            scan_words = sorted(self._desk.lost_last_vlm_visible)
        speaker = speaker_for(('tasks',), self._roster.member_ids(), self._profiles)
        if not wanted:
            note = look(self._vision, self._live_picture(),
                        prompts.lost_anything(user_message[:self._settings.question_max_chars]), background=False,
                        what='a lost-item question')
            return {'speaker': speaker, 'cta': dict(CTA), 'text': note or NOT_SPOTTED}
        notes = {}
        for item_id in wanted:
            if (records.get(item_id) or {}).get('last_seen') is None:
                note = self._vision_note(item_id)
                if note:
                    notes[item_id] = note
        facts, plain = [], []
        for item_id in wanted:
            fact, words = self._what_we_know(item_id, records.get(item_id), notes.get(item_id), scan_words, now)
            facts.append(fact)
            plain.append(words)
        profile = self._profiles.get(speaker)
        system = prompts.LOST_ITEM_SYSTEM.format(name=profile.name if profile else 'Lens',
                                                 obj=profile.object if profile else 'camera')
        user = f'User wrote:\n{user_message[:self._settings.message_max_chars]}\n\nFACTS:\n' + '\n'.join(facts)
        text = ask(self._chat, [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
                   self._replies.lost_answer, background=False, what='a lost-item answer',
                   own_temperature=profile.temperature if profile else self._default_temperature)
        return {'speaker': speaker, 'cta': dict(CTA), 'text': (text or '\n\n'.join(plain)).strip()}

    def _what_we_know(self, item_id: str, record: dict[str, Any] | None, note: str | None, scan_words: list[str],
                      now: float) -> tuple[str, str]:
        """(a fact for the AI, the same in words for you) about one item."""
        name = self.items[item_id]['name']
        last_seen = (record or {}).get('last_seen')
        if last_seen is None:
            if note:
                extra = f' Vision: {note}'
            else:
                extra = f' Last wide scan labels: {", ".join(scan_words)}.' if scan_words else ''
            words = (f"I'm not seeing a saved sighting of your {name} on the desk since we started."
                     + (f' A quick look at the live picture: {note}' if note else
                        ' Captures (in the sidebar, or the button below) has every desk photo with its time.'))
            return f'- your {name}: no desk sighting in this tracking window.{extra}', words
        ago = int(now - last_seen)
        when = format_local_ts(last_seen)
        shown = record['last_near'][:self._settings.nearby_mentioned]
        near = f' near {", ".join(shown)}' if shown else ''
        if ago < self._settings.just_seen_sec:
            return (f'- your {name}: seen on the desk within ~{ago}s ({when}){near}.',
                    f"I've got your {name} on the desk within the last minute or so ({when}){near}.")
        minutes = max(1, ago // 60)
        return (f'- your {name}: last seen on the desk ~{minutes} min ago ({when}){near}.',
                f'I last noted your {name} on the desk around {when}, about {minutes} minute(s) ago{near}.')

    def _live_picture(self) -> bytes | None:
        with self._desk.lock:
            return self._desk.first_raw_frame()

    def _photo_with(self, item_id: str) -> dict[str, Any] | None:
        """The newest saved photo tagged with the item (by the detector or the vision scan)."""
        with self._desk.lock:
            photos = [dict(p) for p in self._desk.snapshot_order]
        for photo in photos:
            if item_id in self.items_in_labels(set(photo.get('labels') or []) | set(photo.get('vlm_labels') or [])):
                return photo
        return None

    def _vision_note(self, item_id: str) -> str | None:
        """What the vision AI sees of an item never spotted: in a saved photo tagged with it, else the live picture."""
        if self._vision is None:
            return None
        name = self.items[item_id]['name']
        photo = self._photo_with(item_id)
        if photo:
            with self._desk.lock:
                jpeg = self._desk.snapshots.get(photo['id'])
            if jpeg:
                about = f'Saved desk photo from {format_local_ts(photo.get("ts"))}' + (
                    f' — {photo["label"]}' if photo.get('label') else '') + '.'
                note = look(self._vision, jpeg, prompts.lost_in_saved_photo(about, name), background=False,
                            what='a lost-item photo')
                if note and len(note.split()) >= self._settings.answer_min_words:
                    return note.strip()[:self._settings.photo_note_max_chars]
        note = look(self._vision, self._live_picture(), prompts.lost_in_live_picture(name), background=False,
                    what='a lost-item look')
        return note.strip()[:self._settings.live_note_max_chars] if note else None
