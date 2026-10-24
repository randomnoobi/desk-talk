"""
DeskState: what everyone needs to know about the desk right now.

One instance is shared by the camera producer, the consumers, the AI and the web routes.

It is on its way out (Task 10 of the cleanup plan). Its methods implement the five desk interfaces in
desktalk/ports.py (LivePictures, DeskScene, SafetyBoard, CharacterMemory, PhotoAlbum), and each takes the
lock itself. Code that still reads or changes the attributes directly must hold `desk.lock` while it does.
The methods read and write the same attributes, so both kinds of code see the same desk.
"""

from __future__ import annotations

import threading
import uuid
from collections import OrderedDict
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from desktalk.domain import Danger, Light, Photo, SafetyNow, SeenObject, Size

PHOTO_ID_CHARS = 12      # proof photo ids: the first 12 letters of a random uuid, as they have always been


def _seen_object(d: Mapping[str, Any]) -> SeenObject:
    """A box in either of the two old shapes: an agent's {class_name, bbox, center, conf, cam}, or a
    detection's {label, conf, bbox, center}."""
    x1, y1, x2, y2 = (float(v) for v in d['bbox'])
    cx, cy = (float(v) for v in d['center'])
    return SeenObject(label=str(d.get('class_name', d.get('label', ''))), box=(x1, y1, x2, y2), center=(cx, cy),
                      confidence=float(d['conf']), cam=int(d.get('cam', 0)))


def _agent_dict(o: SeenObject) -> dict[str, Any]:
    """The shape DeskState.objects has always held."""
    return {'class_name': o.label, 'bbox': list(o.box), 'center': list(o.center), 'conf': o.confidence, 'cam': o.cam}


def _detection_dict(o: SeenObject) -> dict[str, Any]:
    """The shape DeskState.seen_labels has always held (Detection.to_dict)."""
    return {'label': o.label, 'conf': o.confidence, 'bbox': list(o.box), 'center': list(o.center)}


def _photo(entry: Mapping[str, Any], jpeg: bytes) -> Photo:
    return Photo(id=str(entry['id']), ts=float(entry.get('ts') or 0.0), label=str(entry.get('label', '')),
                 jpeg=jpeg, char_ids=tuple(entry.get('char_ids') or ()), labels=tuple(entry.get('labels') or ()),
                 vlm_labels=tuple(entry.get('vlm_labels') or ()))


class DeskState:
    def __init__(self, max_snapshots: int = 150):
        # Re-entrant, so code that holds the lock can still call the methods below while it moves over.
        self.lock = threading.RLock()
        self.max_snapshots = max_snapshots

        # ── What the camera sees ──
        self.objects: dict = {}        # agent id → {class_name, bbox, center, conf, cam} (agents in the chat)
        self.seen_labels: dict = {}    # every detector word in the latest picture → its best box
        self.frames: dict = {}         # cam index → annotated JPEG (live view, "Boxes")
        self.raw_frames: dict = {}     # cam index → plain JPEG ("Plain")
        self.frame_w = 640
        self.frame_h = 480
        self.cameras: dict = {}        # cam index → True once it produced a picture
        self.light: dict = {}          # latest {'brightness': 0-255, 'lux': float|None}
        self.person_last_seen: float | None = None
        self.last_motion_ts: float | None = None
        self.session_start: float | None = None    # when the camera started watching this session

        # ── Safety ──
        self.safety_state = 'SAFE'
        self.safety_dangers: list = []
        self.rel_result: dict | None = None
        self.danger_active_since: dict = {}   # frozenset pair → when the danger started
        self.recent_events: list = []         # last alerts, for the AI's context

        # ── Agents and chat ──
        self.object_first_seen: dict = {}     # agent id → first time this session
        self.object_last_seen: dict = {}
        self.char_widgets: dict = {}          # agent id → tracker data sent by the app (to-do list...)
        self.widget_views: dict = {}          # agent id → [{'ts', 'action'}]
        self.char_memory: dict = {}           # agent id → recent things it said
        self.last_user_activity = 0.0         # user typing / sending → idle chat waits

        # ── Lost items ──
        self.lost_last_vlm_visible: set = set()    # what the last vision scan saw (also tags proof photos)

        # ── Vision (Ollama) habit checks ──
        self.vision_habit_brief = ''          # the last posture check in a few words, for the AI's nudges

        # ── Proof photos ──
        self.snapshots: OrderedDict = OrderedDict()
        self.snapshot_order: list = []        # newest first: id, ts, label, tags

    # ── LivePictures ──
    def put(self, cam: int, plain: bytes, boxed: bytes | None, size: Size) -> None:
        with self.lock:
            self.raw_frames[cam] = plain
            self.frames[cam] = boxed
            self.cameras[cam] = True
            self.frame_w, self.frame_h = size.width, size.height

    def plain(self, cam: int | None = None) -> bytes | None:
        with self.lock:
            return self._newest(self.raw_frames, cam)

    def boxed(self, cam: int | None = None) -> bytes | None:
        with self.lock:
            return self._newest(self.frames, cam)

    @staticmethod
    def _newest(pictures: Mapping[int, bytes | None], cam: int | None) -> bytes | None:
        if cam is not None:
            return pictures.get(cam) or None
        return next((pictures[c] for c in sorted(pictures) if pictures[c]), None)

    def camera_numbers(self) -> list[int]:
        with self.lock:
            return sorted(self.cameras)

    def size(self) -> Size:
        with self.lock:
            return Size(int(self.frame_w), int(self.frame_h))

    def set_light(self, brightness: float, lux: float | None) -> None:
        with self.lock:
            self.light = Light(brightness=brightness, lux=lux).to_dict()

    def newest_light(self) -> Light | None:
        with self.lock:
            light = dict(self.light)
        if 'brightness' not in light:
            return None
        return Light(brightness=float(light['brightness']), lux=light.get('lux'))

    # ── DeskScene ──
    def set_members(self, members: Mapping[str, SeenObject], now: float) -> None:
        with self.lock:
            self.objects = {agent_id: _agent_dict(o) for agent_id, o in members.items()}
            for agent_id in members:
                self.object_first_seen.setdefault(agent_id, now)
                self.object_last_seen[agent_id] = now

    def note_picture(self, seen: Mapping[str, SeenObject], now: float, *, person: bool, moved: bool,
                     new_session: bool) -> None:
        with self.lock:
            if new_session or self.session_start is None:
                self.session_start = now
            if person:
                self.person_last_seen = now
            if moved:
                self.last_motion_ts = now
            self.seen_labels = {label: _detection_dict(o) for label, o in seen.items()}

    def members(self) -> dict[str, SeenObject]:
        with self.lock:
            objects = dict(self.objects)
        return {agent_id: _seen_object(o) for agent_id, o in objects.items()}

    def seen(self) -> dict[str, SeenObject]:
        with self.lock:
            seen = dict(self.seen_labels)
        return {label: _seen_object(o) for label, o in seen.items()}

    def first_seen(self, agent_id: str) -> float | None:
        with self.lock:
            return self.object_first_seen.get(agent_id)

    def last_seen(self, agent_id: str) -> float | None:
        with self.lock:
            return self.object_last_seen.get(agent_id)

    def person_seen_at(self) -> float | None:
        with self.lock:
            return self.person_last_seen

    def last_motion(self) -> float | None:
        with self.lock:
            return self.last_motion_ts

    def session_started_at(self) -> float | None:
        with self.lock:
            return self.session_start

    def set_scan_labels(self, labels: Iterable[str]) -> None:
        with self.lock:
            self.lost_last_vlm_visible = set(labels)

    def scan_labels(self) -> set[str]:
        with self.lock:
            return set(self.lost_last_vlm_visible)

    # ── SafetyBoard ──
    def report(self, state: str, dangers: Sequence[Danger], result: Mapping[str, Any] | None, now: float) -> None:
        active = {frozenset(d.pair) for d in dangers}
        with self.lock:
            self.safety_state = state
            self.safety_dangers = [d.to_dict() for d in dangers]
            self.rel_result = dict(result) if result is not None else None
            for pair in active:
                self.danger_active_since.setdefault(pair, now)
            for pair in list(self.danger_active_since):
                if pair not in active:
                    del self.danger_active_since[pair]

    def now(self) -> SafetyNow:
        with self.lock:
            state, dangers, result = self.safety_state, list(self.safety_dangers), self.rel_result
        return SafetyNow(state=state, dangers=tuple(Danger.from_dict(d) for d in dangers), result=result)

    def danger_since(self, pair: frozenset[str]) -> float | None:
        with self.lock:
            return self.danger_active_since.get(pair)

    def note_alert(self, event: Mapping[str, Any], keep: int) -> None:
        with self.lock:
            self.recent_events = [*self.recent_events, dict(event)][-keep:] if keep > 0 else []

    def recent_alerts(self, limit: int) -> list[dict[str, Any]]:
        with self.lock:
            return [dict(e) for e in self.recent_events[-limit:]] if limit > 0 else []

    # ── CharacterMemory ──
    def remember(self, agent_id: str, text: str, keep: int) -> None:
        with self.lock:
            lines = [*self.char_memory.get(agent_id, []), text]
            self.char_memory[agent_id] = lines[-keep:] if keep > 0 else []

    def recent(self, agent_id: str) -> list[str]:
        with self.lock:
            return list(self.char_memory.get(agent_id, []))

    def set_widget(self, agent_id: str, data: Mapping[str, Any]) -> None:
        with self.lock:
            self.char_widgets[agent_id] = dict(data)

    def widget(self, agent_id: str) -> dict[str, Any] | None:
        with self.lock:
            data = self.char_widgets.get(agent_id)
            return dict(data) if data is not None else None

    def widgets(self) -> dict[str, dict[str, Any]]:
        with self.lock:
            return {agent_id: dict(data) for agent_id, data in self.char_widgets.items()}

    def note_widget_view(self, agent_id: str, action: str, now: float, keep: int) -> None:
        with self.lock:
            views = [*self.widget_views.get(agent_id, []), {'ts': now, 'action': action}]
            self.widget_views[agent_id] = views[-keep:] if keep > 0 else []

    def widget_views_of(self, agent_id: str) -> list[dict[str, Any]]:
        with self.lock:
            return [dict(v) for v in self.widget_views.get(agent_id, [])]

    def note_user_activity(self, now: float) -> None:
        with self.lock:
            self.last_user_activity = now

    def user_active_at(self) -> float:
        with self.lock:
            return self.last_user_activity

    def set_vision_brief(self, text: str) -> None:
        with self.lock:
            self.vision_habit_brief = text

    def vision_brief(self) -> str:
        with self.lock:
            return self.vision_habit_brief

    # ── PhotoAlbum ──
    def add(self, jpeg: bytes, label: str, ts: float, *, char_ids: Sequence[str] = (), labels: Sequence[str] = (),
            vlm_labels: Sequence[str] = ()) -> str:
        photo_id = uuid.uuid4().hex[:PHOTO_ID_CHARS]
        entry = {'id': photo_id, 'ts': ts, 'label': label, 'char_ids': list(char_ids), 'labels': list(labels),
                 'vlm_labels': list(vlm_labels)}
        with self.lock:
            self.snapshots[photo_id] = jpeg
            self.snapshot_order = [entry, *self.snapshot_order][:self.max_snapshots]
            while len(self.snapshots) > self.max_snapshots:
                old_id, _ = self.snapshots.popitem(last=False)
                self.snapshot_order = [x for x in self.snapshot_order if x.get('id') != old_id]
        return photo_id

    def get(self, photo_id: str) -> Photo | None:
        with self.lock:
            jpeg = self.snapshots.get(photo_id)
            entry = next((x for x in self.snapshot_order if x.get('id') == photo_id), None)
        return _photo(entry, jpeg) if jpeg is not None and entry is not None else None

    def newest(self, limit: int) -> list[Photo]:
        with self.lock:
            entries = [dict(x) for x in self.snapshot_order[:max(0, limit)]]
            pictures = {x['id']: self.snapshots.get(x['id']) for x in entries}
        return [_photo(x, jpeg) for x in entries if (jpeg := pictures[x['id']]) is not None]
