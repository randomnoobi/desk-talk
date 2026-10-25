"""Album: the proof photos, newest first, kept in memory (settings → snapshots.max_kept)."""

from __future__ import annotations

import threading
import uuid
from collections.abc import Sequence

from desktalk.domain import Photo

PHOTO_ID_CHARS = 12      # proof photo ids: the first 12 letters of a random uuid, as they have always been


class Album:
    """Implements PhotoAlbum. Keeps at most `keep` photos; when it is full, the oldest goes."""

    def __init__(self, keep: int):
        self._keep = keep
        self._lock = threading.Lock()
        self._photos: list[Photo] = []        # newest first

    def add(self, jpeg: bytes, label: str, ts: float, *, char_ids: Sequence[str] = (), labels: Sequence[str] = (),
            vlm_labels: Sequence[str] = ()) -> str:
        photo = Photo(id=uuid.uuid4().hex[:PHOTO_ID_CHARS], ts=ts, label=label, jpeg=jpeg, char_ids=tuple(char_ids),
                      labels=tuple(labels), vlm_labels=tuple(vlm_labels))
        with self._lock:
            self._photos = [photo, *self._photos][:max(0, self._keep)]
        return photo.id

    def get(self, photo_id: str) -> Photo | None:
        with self._lock:
            return next((p for p in self._photos if p.id == photo_id), None)

    def newest(self, limit: int) -> list[Photo]:
        with self._lock:
            return self._photos[:max(0, limit)]
