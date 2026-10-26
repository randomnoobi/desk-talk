"""
WholeDesk: the five desk parts behind one object, for the parts that still ask for several desk interfaces
at once (the producer, proof photos, the desk monitor, the desk status, lost items and the message generator).
It keeps nothing itself: every call goes straight to the part that owns it, so it and the
parts are always the same desk. Once those parts ask for each interface on its own, this can go.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from desktalk.domain import Danger, Light, Photo, SafetyNow, SeenObject, Size
from desktalk.ports import CharacterMemory, DeskScene, LivePictures, PhotoAlbum, SafetyBoard


class WholeDesk:
    """Implements LivePictures, DeskScene, SafetyBoard, CharacterMemory and PhotoAlbum by passing each call on."""

    def __init__(self, pictures: LivePictures, scene: DeskScene, safety: SafetyBoard, memory: CharacterMemory,
                 album: PhotoAlbum):
        self._pictures = pictures
        self._scene = scene
        self._safety = safety
        self._memory = memory
        self._album = album

    # ── LivePictures ──
    def put(self, cam: int, plain: bytes, boxed: bytes | None, size: Size) -> None:
        self._pictures.put(cam, plain, boxed, size)

    def plain(self, cam: int | None = None) -> bytes | None:
        return self._pictures.plain(cam)

    def boxed(self, cam: int | None = None) -> bytes | None:
        return self._pictures.boxed(cam)

    def camera_numbers(self) -> list[int]:
        return self._pictures.camera_numbers()

    def size(self) -> Size:
        return self._pictures.size()

    def set_light(self, brightness: float, lux: float | None) -> None:
        self._pictures.set_light(brightness, lux)

    def newest_light(self) -> Light | None:
        return self._pictures.newest_light()

    # ── DeskScene ──
    def set_members(self, members: Mapping[str, SeenObject], now: float) -> None:
        self._scene.set_members(members, now)

    def note_picture(self, seen: Mapping[str, SeenObject], now: float, *, person: bool, moved: bool,
                     new_session: bool) -> None:
        self._scene.note_picture(seen, now, person=person, moved=moved, new_session=new_session)

    def members(self) -> dict[str, SeenObject]:
        return self._scene.members()

    def set_on_desk(self, objects: Mapping[str, SeenObject], now: float) -> None:
        self._scene.set_on_desk(objects, now)

    def on_desk(self) -> dict[str, SeenObject]:
        return self._scene.on_desk()

    def seen(self) -> dict[str, SeenObject]:
        return self._scene.seen()

    def first_seen(self, agent_id: str) -> float | None:
        return self._scene.first_seen(agent_id)

    def last_seen(self, agent_id: str) -> float | None:
        return self._scene.last_seen(agent_id)

    def person_seen_at(self) -> float | None:
        return self._scene.person_seen_at()

    def last_motion(self) -> float | None:
        return self._scene.last_motion()

    def session_started_at(self) -> float | None:
        return self._scene.session_started_at()

    def set_scan_labels(self, labels: Iterable[str]) -> None:
        self._scene.set_scan_labels(labels)

    def scan_labels(self) -> set[str]:
        return self._scene.scan_labels()

    # ── SafetyBoard ──
    def report(self, state: str, dangers: Sequence[Danger], result: Mapping[str, Any] | None, now: float) -> None:
        self._safety.report(state, dangers, result, now)

    def now(self) -> SafetyNow:
        return self._safety.now()

    def danger_since(self, pair: frozenset[str]) -> float | None:
        return self._safety.danger_since(pair)

    def note_alert(self, event: Mapping[str, Any], keep: int) -> None:
        self._safety.note_alert(event, keep)

    def recent_alerts(self, limit: int) -> list[dict[str, Any]]:
        return self._safety.recent_alerts(limit)

    # ── CharacterMemory ──
    def remember(self, agent_id: str, text: str, keep: int) -> None:
        self._memory.remember(agent_id, text, keep)

    def recent(self, agent_id: str) -> list[str]:
        return self._memory.recent(agent_id)

    def set_widget(self, agent_id: str, data: Mapping[str, Any]) -> None:
        self._memory.set_widget(agent_id, data)

    def widget(self, agent_id: str) -> dict[str, Any] | None:
        return self._memory.widget(agent_id)

    def widgets(self) -> dict[str, dict[str, Any]]:
        return self._memory.widgets()

    def note_widget_view(self, agent_id: str, action: str, now: float, keep: int) -> None:
        self._memory.note_widget_view(agent_id, action, now, keep)

    def widget_views_of(self, agent_id: str) -> list[dict[str, Any]]:
        return self._memory.widget_views_of(agent_id)

    def note_user_activity(self, now: float) -> None:
        self._memory.note_user_activity(now)

    def user_active_at(self) -> float:
        return self._memory.user_active_at()

    # ── PhotoAlbum ──
    def add(self, jpeg: bytes, label: str, ts: float, *, char_ids: Sequence[str] = (), labels: Sequence[str] = (),
            vlm_labels: Sequence[str] = ()) -> str:
        return self._album.add(jpeg, label, ts, char_ids=char_ids, labels=labels, vlm_labels=vlm_labels)

    def get(self, photo_id: str) -> Photo | None:
        return self._album.get(photo_id)

    def newest(self, limit: int) -> list[Photo]:
        return self._album.newest(limit)
