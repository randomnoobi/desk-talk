"""Fakes for the five desk interfaces (the desk right now). Plain dicts and lists, no lock: tests run in one thread."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from desktalk.domain import Danger, Light, Photo, SafetyNow, SeenObject, Size


class FakeLivePictures:
    def __init__(self, size: Size | None = None):
        self.plain_pictures: dict[int, bytes] = {}
        self.boxed_pictures: dict[int, bytes | None] = {}
        self._size = size or Size(640, 480)
        self._light: Light | None = None

    def put(self, cam: int, plain: bytes, boxed: bytes | None, size: Size) -> None:
        self.plain_pictures[cam], self.boxed_pictures[cam], self._size = plain, boxed, size

    def plain(self, cam: int | None = None) -> bytes | None:
        return self._pick(self.plain_pictures, cam)

    def boxed(self, cam: int | None = None) -> bytes | None:
        return self._pick(self.boxed_pictures, cam)

    @staticmethod
    def _pick(pictures: Mapping[int, bytes | None], cam: int | None) -> bytes | None:
        if cam is not None:
            return pictures.get(cam)
        return next((pictures[c] for c in sorted(pictures) if pictures[c]), None)

    def camera_numbers(self) -> list[int]:
        return sorted(self.plain_pictures)

    def size(self) -> Size:
        return self._size

    def set_light(self, brightness: float, lux: float | None) -> None:
        self._light = Light(brightness=brightness, lux=lux)

    def newest_light(self) -> Light | None:
        return self._light


class FakeDeskScene:
    def __init__(self):
        self._members: dict[str, SeenObject] = {}
        self._seen: dict[str, SeenObject] = {}
        self._first: dict[str, float] = {}
        self._last: dict[str, float] = {}
        self._person: float | None = None
        self._motion: float | None = None
        self._session: float | None = None
        self._scan: set[str] = set()

    def set_members(self, members: Mapping[str, SeenObject], now: float) -> None:
        self._members = dict(members)
        for agent_id in members:
            self._first.setdefault(agent_id, now)
            self._last[agent_id] = now

    def note_picture(self, seen: Mapping[str, SeenObject], now: float, *, person: bool, moved: bool,
                     new_session: bool) -> None:
        if new_session or self._session is None:
            self._session = now
        self._person = now if person else self._person
        self._motion = now if moved else self._motion
        self._seen = dict(seen)

    def members(self) -> dict[str, SeenObject]:
        return dict(self._members)

    def seen(self) -> dict[str, SeenObject]:
        return dict(self._seen)

    def first_seen(self, agent_id: str) -> float | None:
        return self._first.get(agent_id)

    def last_seen(self, agent_id: str) -> float | None:
        return self._last.get(agent_id)

    def person_seen_at(self) -> float | None:
        return self._person

    def last_motion(self) -> float | None:
        return self._motion

    def session_started_at(self) -> float | None:
        return self._session

    def set_scan_labels(self, labels: Iterable[str]) -> None:
        self._scan = set(labels)

    def scan_labels(self) -> set[str]:
        return set(self._scan)


class FakeSafetyBoard:
    def __init__(self):
        self._now = SafetyNow()
        self._since: dict[frozenset[str], float] = {}
        self._alerts: list[dict[str, Any]] = []

    def report(self, state: str, dangers: Sequence[Danger], result: Mapping[str, Any] | None, now: float) -> None:
        self._now = SafetyNow(state=state, dangers=tuple(dangers), result=result)
        active = {frozenset(d.pair) for d in dangers}
        self._since = {pair: self._since.get(pair, now) for pair in active}

    def now(self) -> SafetyNow:
        return self._now

    def danger_since(self, pair: frozenset[str]) -> float | None:
        return self._since.get(pair)

    def note_alert(self, event: Mapping[str, Any], keep: int) -> None:
        self._alerts = [*self._alerts, dict(event)][-keep:] if keep > 0 else []

    def recent_alerts(self, limit: int) -> list[dict[str, Any]]:
        return [dict(e) for e in self._alerts[-limit:]] if limit > 0 else []


class FakeCharacterMemory:
    def __init__(self):
        self._said: dict[str, list[str]] = {}
        self._widgets: dict[str, dict[str, Any]] = {}
        self._views: dict[str, list[dict[str, Any]]] = {}
        self._active_at = 0.0
        self._brief = ''

    def remember(self, agent_id: str, text: str, keep: int) -> None:
        lines = [*self._said.get(agent_id, []), text]
        self._said[agent_id] = lines[-keep:] if keep > 0 else []

    def recent(self, agent_id: str) -> list[str]:
        return list(self._said.get(agent_id, []))

    def set_widget(self, agent_id: str, data: Mapping[str, Any]) -> None:
        self._widgets[agent_id] = dict(data)

    def widget(self, agent_id: str) -> dict[str, Any] | None:
        data = self._widgets.get(agent_id)
        return dict(data) if data is not None else None

    def widgets(self) -> dict[str, dict[str, Any]]:
        return {agent_id: dict(data) for agent_id, data in self._widgets.items()}

    def note_widget_view(self, agent_id: str, action: str, now: float, keep: int) -> None:
        views = [*self._views.get(agent_id, []), {'ts': now, 'action': action}]
        self._views[agent_id] = views[-keep:] if keep > 0 else []

    def widget_views_of(self, agent_id: str) -> list[dict[str, Any]]:
        return [dict(v) for v in self._views.get(agent_id, [])]

    def note_user_activity(self, now: float) -> None:
        self._active_at = now

    def user_active_at(self) -> float:
        return self._active_at

    def set_vision_brief(self, text: str) -> None:
        self._brief = text

    def vision_brief(self) -> str:
        return self._brief


class FakePhotoAlbum:
    """Keeps at most `keep` photos; ids are photo-1, photo-2 ..."""

    def __init__(self, keep: int = 150):
        self._keep = keep
        self._photos: list[Photo] = []        # newest first
        self._added = 0

    def add(self, jpeg: bytes, label: str, ts: float, *, char_ids: Sequence[str] = (), labels: Sequence[str] = (),
            vlm_labels: Sequence[str] = ()) -> str:
        self._added += 1
        photo = Photo(id=f'photo-{self._added}', ts=ts, label=label, jpeg=jpeg, char_ids=tuple(char_ids),
                      labels=tuple(labels), vlm_labels=tuple(vlm_labels))
        self._photos = [photo, *self._photos][:self._keep]
        return photo.id

    def get(self, photo_id: str) -> Photo | None:
        return next((p for p in self._photos if p.id == photo_id), None)

    def newest(self, limit: int) -> list[Photo]:
        return self._photos[:max(0, limit)]
