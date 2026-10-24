"""
Lost items: "have you seen my keys?"

The tracked items are listed in config/settings.yaml (lost_items.items), each with the detector
words for it and the words you might type (lost_item_words.py).

  1. Every analysed picture (DetectionEvent): if a tracked item is in it, note the time and which
     agents were in the chat. When one turns up again, save a "Spotted" photo.
  2. Every lost_items.check_every_sec: if the picture changed (or it's been rescan_every_sec), a vision
     AI on this computer (Ollama) looks for small things the detector misses.
  3. If an item has been gone for its alert time, Lens tells the chat (an Alert event).
  4. When you ask, the answer uses those sightings, plus a look at a saved photo or the live picture
     (lost_items_answers.py).

    lost = LostItems(clock, bus, desk, roster, profiles, habits, chat_model, ollama_or_none, config.lost_items,
                     config.llm.replies, photos=proof_photos,
                     default_temperature=config.agents_file.default.temperature,
                     phone_is_camera=config.camera.source == 'phone', pictures=OpenCvPictureTools(),
                     prompts=config.prompts.lost_items, photos_kept=config.snapshots.max_kept)
    supervisor.add(lost, start_after=config.lost_items.start_delay_sec)
    lost.items, lost.is_lost_query(text), lost.answer(text)     # for the web routes
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from desktalk.config import AiReplies, LostItemsSettings
from desktalk.config.prompts import LostItemsPrompts
from desktalk.core.agents.speakers import LENS
from desktalk.core.chat import prompts as fill
from desktalk.core.chat.workers.ai import ask, look
from desktalk.core.chat.workers.lost_item_words import TrackedItems
from desktalk.core.chat.workers.lost_items_answers import LostItemAnswers, LostItemsDesk
from desktalk.core.chat.workers.text import json_in
from desktalk.domain import Alert, DetectionEvent, Event, Picture
from desktalk.ports import ChatModel, Clock, EventBus, HabitsView, PhotoTaker, PictureTools, Profiles, RosterView

log = logging.getLogger(__name__)


def _new_record() -> dict[str, Any]:
    return {'last_seen': None, 'alerted': False, 'source': None, 'last_near': []}


class LostItems:
    """A PeriodicWorker that also listens to DetectionEvents. `vision` is the AI that looks at pictures,
    or None when no local vision AI is set up (no scans, as before: a cloud AI's quota is kept for the chat)."""

    name = 'lost items'

    def __init__(self, clock: Clock, bus: EventBus, desk: LostItemsDesk, roster: RosterView, profiles: Profiles,
                 habits: HabitsView, chat: ChatModel, vision: ChatModel | None, settings: LostItemsSettings,
                 replies: AiReplies, *, photos: PhotoTaker, default_temperature: float, pictures: PictureTools,
                 prompts: LostItemsPrompts, photos_kept: int, phone_is_camera: bool = False):
        """`replies`: settings → llm.replies (this uses lost_alert and lost_answer). `default_temperature`:
        agents.yaml's default, for Lens when those settings have none. `pictures` compares the pictures (has the
        desk changed?). `prompts`: config/prompts.yaml → lost_items. `photos_kept`: settings → snapshots.max_kept.
        `phone_is_camera`: the phone can't be lost."""
        self._bus = bus
        self._desk = desk
        self._roster = roster
        self._profiles = profiles
        self._habits = habits
        self._chat = chat
        self._vision = vision
        self._photos = photos
        self._pictures = pictures
        self._words = prompts
        self._settings = settings
        self._replies = replies
        self._default_temperature = default_temperature
        self._phone_is_camera = phone_is_camera
        self._tracked = TrackedItems(settings, prompts)
        self.items = self._tracked.items
        self._answers = LostItemAnswers(clock, desk, roster, profiles, chat, vision, self._tracked, self.sightings,
                                        settings, replies, prompts, default_temperature=default_temperature,
                                        photos_kept=photos_kept)
        self._lock = threading.Lock()      # pictures, the timer and the web routes all come in on their own threads
        self._seen: dict[str, dict[str, Any]] = {}       # item id → last_seen, alerted, source, last_near
        self._first_picture_done = False
        self._in_last_picture: set[str] = set()
        self._spotted_photo_at: dict[str, float] = {}    # 'detector:keys' / 'vision:keys' → when
        self._first_scan_done = False
        self._last_gray: Picture | None = None
        self._last_scan: float | None = None
        self._desk_photo_at: float | None = None
        bus.subscribe('lost-items', self.on_detection, types=[DetectionEvent])

    # ── for the web routes ──
    def is_lost_query(self, text: str) -> bool:
        """A "have you seen my ...?" question about a tracked item or another everyday thing."""
        return self._tracked.is_lost_query(text)

    def answer(self, user_message: str) -> dict[str, Any]:
        """The chat's reply to a lost-item question: {'speaker', 'cta', 'text'} (lost_items_answers.py)."""
        return self._answers.answer(user_message)

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
        present = self._tracked.in_labels(labels)
        names = self._member_names()
        spotted = []
        with self._lock:
            for item_id in present:
                item_name = self.items[item_id]['name'].lower()
                near = [n for n in names if n.lower() != item_name][:self._settings.nearby_kept]
                self._seen.setdefault(item_id, _new_record()).update(
                    last_seen=now, alerted=False, source='detector', last_near=near)
            if self._first_picture_done:
                spotted = [self._words.spotted_photo.format(name=self.items[i]['name'])
                           for i in sorted(present - self._in_last_picture)
                           if self._photo_due(f'detector:{i}', now)]
            self._first_picture_done = True          # the first picture is only the starting point
            self._in_last_picture = set(present)
        for label in spotted:
            self._photos.save(label)

    # ── 2 + 3. on a timer ──
    def tick(self, now: float) -> float:
        jpeg = self._desk.plain()
        if not jpeg:
            return self._settings.check_every_sec
        scan_hits: set[str] = set()
        if self._vision is not None and self._scan_due(jpeg, now):
            scan_hits = self._scan(jpeg, now)
        self._alert_lost_items(now, scan_hits)
        return self._settings.check_every_sec

    def _scan_due(self, jpeg: bytes, now: float) -> bool:
        gray = self._small_gray(jpeg)
        with self._lock:
            changed = self._difference(self._last_gray, gray) >= self._settings.scene_change
            if gray is not None:
                self._last_gray = gray
            return changed or self._last_scan is None or now - self._last_scan >= self._settings.rescan_every_sec

    def _small_gray(self, jpeg: bytes) -> Picture | None:
        """The picture as a small gray square, for comparing (None if it isn't a picture)."""
        gray = self._pictures.decode_jpeg(jpeg, gray=True)
        if gray is None:
            return None
        size = self._settings.compare_size
        return self._pictures.shrink(gray, size, size)

    def _difference(self, a: Picture | None, b: Picture | None) -> float:
        """How different two small gray pictures look, 0-255 (a missing one counts as completely different)."""
        if a is None or b is None:
            return float('inf')
        return self._pictures.difference(a, b)

    def _scan(self, jpeg: bytes, now: float) -> set[str]:
        """Ask the vision AI what small things are on the desk. Returns the words it saw."""
        item_names = ', '.join(item['name'] for item in self.items.values())
        answer = json_in(look(self._vision, jpeg, fill.lost_scan(self._words, item_names), background=True,
                              what='the lost-items scan'))
        with self._lock:
            self._last_scan = now
        if answer is None:
            return set()
        visible = answer.get('visible')
        hits = {str(x).strip().lower() for x in visible if x} if isinstance(visible, list) else set()
        found = self._tracked.in_labels(hits)
        nearby = (self._member_names() + sorted(hits))[:self._settings.nearby_kept]
        seen_before = self._tracked.in_labels(self._desk.scan_labels())
        self._desk.set_scan_labels(hits)        # also tags the photos saved below
        spotted = []
        with self._lock:
            for item_id in found:
                record = self._seen.setdefault(item_id, _new_record())
                fresh_detector = (record['source'] == 'detector' and record['last_seen'] is not None
                                  and now - record['last_seen'] <= self._settings.detector_fresh_sec)
                if not fresh_detector:
                    record.update(last_seen=now, alerted=False, source='vision', last_near=list(nearby))
            if self._first_scan_done:
                spotted = [self._words.spotted_by_vision_photo.format(name=self.items[i]['name'])
                           for i in sorted(found - seen_before)
                           if self._photo_due(f'vision:{i}', now)]
            self._first_scan_done = True
            desk_photo_due = (self._desk_photo_at is None
                              or now - self._desk_photo_at >= self._settings.snapshot_every_sec)
        for label in spotted:
            self._photos.save(label)
        if desk_photo_due and self._photos.save(self._words.desk_scan_photo):
            with self._lock:
                self._desk_photo_at = now
        return hits

    def _alert_lost_items(self, now: float, scan_hits: set[str]) -> None:
        seen_now = self._tracked.in_labels(self._desk.seen()) | self._tracked.in_labels(scan_hits)
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
        hint = self._words.last_seen_near.format(names=', '.join(shown)) if shown else ''
        text = ask(self._chat, fill.lost_alert(self._words, name, minutes, hint), self._replies.lost_alert,
                   own_temperature=self._default_temperature, background=True, what='a lost-item alert')
        text = text or self._words.alert_plain.format(name=name, minutes=minutes, hint=hint)
        self._habits.bump('lost_alerts')
        self._bus.publish(Alert(ts=now, kind='scenario', scenario='lost_object', feature=item_id, agent=LENS,
                                text=text))
        log.info('Lost item: %s', text)
