"""
Lens's eyes: checks on the camera itself (not the objects).

Lens speaks up in the group chat when something about the camera needs fixing: too dark, glare, blurry,
an object cut off at the side of the picture, the camera got bumped, the video is slow or stopped. When
the problem goes away, Lens says thanks. All the numbers are in settings → lens, the words in
config/prompts.yaml → camera.

    health = CameraHealth(config.lens, source_kind='pi', moved_within_sec=config.camera.moved_within_sec,
                          pictures=OpenCvPictureTools(), words=config.prompts.camera,
                          ignore_labels=config.agents_file.ignore)
    health.sample(picture, detections, now)          # every analysed picture (measures about once a second)
    for msg in health.check(now, connected=True, fps=2.9, ...):   # every couple of seconds (Lens's job)
        msg['text']

It is the front of three parts: camera_measure.py (the numbers from a picture), camera_problems.py (when a
problem starts, is said and is fixed) and the wording, here.

The camera job calls sample(), Lens's job calls check() and forget(), and the web server calls issues()
and phone_tip(), all from different threads. One lock guards everything they share.
"""

from __future__ import annotations

import threading
from collections.abc import Iterable, Sequence
from typing import Any

from desktalk.config import LensSettings
from desktalk.config.prompts import CameraPrompts
from desktalk.core.vision.camera_measure import CameraMeasure, Reading
from desktalk.core.vision.camera_problems import PEOPLE, CameraProblems, Detail, Said, Seen
from desktalk.domain import Box, Detection, Picture
from desktalk.ports import PictureTools

PHONE_TIP_KEYS = ('covered', 'dark', 'glare', 'blurry', 'bumped', 'cutoff')   # shown on the phone's own screen


class CameraHealth:
    """Implements PictureCheck (sample, moved_recently) and CameraHealthView (check)."""

    def __init__(self, lens: LensSettings, *, source_kind: str, moved_within_sec: float, pictures: PictureTools,
                 words: CameraPrompts, ignore_labels: Iterable[str] = (), watch_labels: Iterable[str] = ()):
        self._lens = lens
        self._source_kind = source_kind
        self._moved_within = moved_within_sec
        self._words = words
        self._measure = CameraMeasure(pictures, width=lens.measure_width, white_level=lens.white_level)
        self._pictures = pictures
        self._problems = CameraProblems(lens, thanked=type(words.fixed).model_fields, ignore_labels=ignore_labels,
                                        watch_labels=watch_labels)
        self._lock = threading.RLock()

        # what sample() saw
        self._last_sample = 0.0
        self._prev_gray: Picture | None = None
        self._latest: Reading | None = None
        self._latest_ts = 0.0
        self._boxes: list[tuple[str, Box]] = []
        self._size = (0, 0)
        self._motion_ts = 0.0           # last time the picture changed a lot
        self._last_seen_anything = 0.0

    # ── PictureCheck: every analysed picture (cheap: measures at most once per sample_every_sec) ──
    def sample(self, picture: Picture, detections: Sequence[Detection], now: float) -> None:
        if now - self._last_sample < self._lens.sample_every_sec:
            return
        self._last_sample = now
        gray = self._measure.gray(picture)
        reading = self._measure.reading(gray)
        size = self._pictures.size(picture)
        with self._lock:
            if self._prev_gray is not None and self._measure.change(self._prev_gray, gray) > self._lens.motion_above:
                self._motion_ts = now
            self._prev_gray = gray
            self._latest = reading
            self._latest_ts = now
            self._boxes = [(d.label, (d.bbox.x1, d.bbox.y1, d.bbox.x2, d.bbox.y2)) for d in detections]
            self._size = (size.width, size.height)
            if any(label != PEOPLE for label, _ in self._boxes):
                self._last_seen_anything = now

    def moved_recently(self, now: float) -> bool:
        with self._lock:
            moved = self._motion_ts
        return bool(moved) and 0 <= now - moved < self._moved_within

    # ── CameraHealthView: every couple of seconds; returns what Lens should say ──
    def check(self, now: float, *, connected: bool, fps: float | None = None, gravity: Any = None,
              reference_gravity: Any = None, has_outline: bool = True, stopped: bool = False,
              quiet: bool = False) -> list[dict[str, Any]]:
        """`stopped` = the video was stopped on purpose (the phone's Stop button): no complaint.
        `quiet` = quiet hours: problems are still followed, but nothing is said or counted as said, so no
        "fixed, thanks" can come later for a problem nobody heard."""
        with self._lock:
            fresh = now - self._latest_ts < self._lens.reading_fresh_sec
            seen = Seen(reading=self._latest if fresh else None, boxes=list(self._boxes), size=self._size,
                        last_seen_anything=self._last_seen_anything)
            said = self._problems.check(now, seen, connected=connected, fps=fps, gravity=gravity,
                                        reference_gravity=reference_gravity, has_outline=has_outline,
                                        stopped=stopped, quiet=quiet)
            return [self._message(m) for m in said]

    def forget(self, key: str) -> None:
        """Drop a problem without a thank-you (e.g. the desk was just marked again)."""
        with self._lock:
            self._problems.forget(key)

    # ── the wording (config/prompts.yaml → camera) ──
    def _message(self, said: Said) -> dict[str, Any]:
        if said.kind == 'problem':
            return {'key': said.key, 'kind': said.kind, 'text': self._problem_text(said.key, said.detail),
                    'detail': said.detail}
        if said.kind == 'fixed':
            return {'key': said.key, 'kind': said.kind, 'text': getattr(self._words.fixed, said.key)}
        return {'key': said.key, 'kind': said.kind, 'text': self._words.edge_tip}

    def _problem_text(self, key: str, d: Detail) -> str:
        words = self._words
        if key == 'lost':
            return words.lost.model_dump().get(self._source_kind, words.lost.phone)
        problems = words.problems
        if key == 'bumped':
            return problems.bumped.format(degrees=d.get('degrees', self._lens.bump_degrees))
        if key == 'cutoff':
            side = d.get('side', 'left')
            hint = words.side_hints.model_dump().get(side, words.other_side_hint)
            return problems.cutoff.format(object=d.get('object', words.unknown_object), side=side, hint=hint)
        if key == 'slow':
            return problems.slow.format(fps=d.get('fps', 0))
        return getattr(problems, key)

    # ── for the app ──
    def issues(self) -> list[dict[str, str]]:
        """Current problems, most important first: [{key, title, fix}]."""
        with self._lock:
            active = self._problems.active()
        words = self._words
        out = []
        for key, d in active.items():
            title, fix = getattr(words.titles, key).title, getattr(words.titles, key).fix
            if key == 'lost':
                title = words.lost_title
                fix = words.lost_fix.model_dump().get(self._source_kind, fix)
            if key == 'cutoff' and d.get('object'):
                title = words.cutoff_title.format(object=d['object'].capitalize(), side=d.get('side'))
            out.append({'key': key, 'title': title, 'fix': fix})
        return out

    def phone_tip(self) -> str | None:
        """A few words to show on the phone screen itself, or None."""
        with self._lock:
            active = self._problems.active()
        key = next((k for k in PHONE_TIP_KEYS if k in active), None)
        return getattr(self._words.titles, key).title if key else None
