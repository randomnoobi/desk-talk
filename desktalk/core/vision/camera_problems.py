"""
Lens's camera problems, from first sign to "fixed, thanks".

A problem has to last a while before Lens says it (settings → lens.wait_before_saying_sec), is said once and
again only after lens.repeat_after_min, and once it has stayed gone for lens.fixed_after_sec Lens says thanks.
Only one thing is said at a time, with lens.speak_gap_sec between warnings. All the wording is somewhere else
(CameraHealth, from config/prompts.yaml): this part only says which problem, and its details.

Not thread-safe on its own: CameraHealth calls it with its lock held.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from desktalk.config import LensSettings
from desktalk.core.vision.camera_measure import Reading, box_share, cut_off_side, tilt_degrees
from desktalk.domain import Box

# Most important first. While one of these is happening, the problems it hides can't be trusted
# (a covered lens also looks dark and blurry), so they are neither said nor counted as fixed.
PRIORITY = ('lost', 'covered', 'dark', 'glare', 'blurry', 'bumped', 'empty', 'cutoff', 'slow')
HIDES = {
    'lost': {'covered', 'dark', 'glare', 'blurry', 'empty', 'cutoff', 'slow', 'bumped'},
    'covered': {'dark', 'glare', 'blurry', 'empty', 'cutoff'},
    'dark': {'blurry', 'empty', 'cutoff'},
    'glare': {'blurry', 'empty'},
    'blurry': {'empty', 'cutoff'},
}
WAITED_ALREADY = ('empty', 'cutoff')     # these wait their time while they are being found
PEOPLE = 'person'          # the detector's word for people: never "cut off", and not "something on the desk"

Detail = dict[str, Any]
Gravity = Sequence[float]


@dataclass(frozen=True)
class Seen:
    """What the camera checks saw lately, handed to check()."""

    reading: Reading | None                 # None when the newest one is too old to trust
    boxes: Sequence[tuple[str, Box]] = ()   # detector word and box, in the newest measured picture
    size: tuple[int, int] = (0, 0)          # that picture's width and height
    last_seen_anything: float = 0.0         # when something other than a person was last in the picture


@dataclass(frozen=True)
class Said:
    """One thing Lens should say: a problem, that one is fixed, or the tip to mark the desk."""

    key: str                    # the problem (lost, dark ...) or edge_tip
    kind: str                   # problem, fixed or tip
    detail: Detail = field(default_factory=dict)


@dataclass
class _Active:
    """A confirmed problem."""

    since: float
    said_at: float
    detail: Detail


@dataclass
class _Cut:
    """An object that looks cut off."""

    side: str
    since: float
    last: float


class CameraProblems:
    """Objects checked for "cut off at the side": those in watch_labels, or (when there are none) anything not in
    ignore_labels (and never people). Only the problems in `thanked` get a "fixed, thanks"; the others just end."""

    def __init__(self, lens: LensSettings, *, thanked: Iterable[str], ignore_labels: Iterable[str] = (),
                 watch_labels: Iterable[str] = ()):
        self._lens = lens
        self._thanked = set(thanked)
        self._waits = lens.wait_before_saying_sec
        self._repeat_after = lens.repeat_after_min * 60
        self._watch = set(watch_labels)
        self._ignore = set(ignore_labels) | {PEOPLE}

        self._stream_started = 0.0      # when the current run of video began
        self._stream_seen = 0.0         # when a run of video last began: counts as seeing something
        self._ever_connected = False
        self._raw_since: dict[str, float] = {}      # problem → when it started (not yet confirmed)
        self._clear_since: dict[str, float] = {}    # problem → when it went away
        self._active: dict[str, _Active] = {}       # confirmed problems
        self._last_spoke = 0.0
        self._edge_tip_given = False
        self._cut: dict[str, _Cut] = {}             # object → while it looks cut off
        self._cutoff_said: dict[str, float] = {}    # object → when Lens last mentioned it

    def check(self, now: float, seen: Seen, *, connected: bool, fps: float | None, gravity: Gravity | None,
              reference_gravity: Gravity | None, has_outline: bool, stopped: bool, quiet: bool) -> list[Said]:
        return self._check(now, seen, connected, fps, gravity, reference_gravity, has_outline, stopped, quiet)

    def _check(self, now: float, seen: Seen, connected: bool, fps: float | None, gravity: Gravity | None,
               reference_gravity: Gravity | None, has_outline: bool, stopped: bool, quiet: bool) -> list[Said]:
        raw = self._problems_now(now, seen, connected, fps, gravity, reference_gravity, stopped)
        hidden: set[str] = set()
        for big, small in HIDES.items():
            if big in raw:
                hidden |= small
        for key in hidden:
            raw.pop(key, None)
        out = self._confirm_and_clear(now, raw, hidden)
        if quiet:
            return []
        if not self._last_spoke or now - self._last_spoke >= self._lens.speak_gap_sec:
            said = self._say_one(now)
            if said:
                out.append(said)
        if (not out and not has_outline and not self._edge_tip_given and not self._active
                and self._stream_started and now - self._stream_started >= self._lens.edge_tip_after_sec
                and (not self._last_spoke or now - self._last_spoke >= self._lens.speak_gap_sec)):
            self._edge_tip_given = True
            out.append(Said('edge_tip', 'tip'))
        if any(m.kind != 'fixed' for m in out):
            self._last_spoke = now    # a quick "thanks, fixed" never delays the next warning
        return out

    def _problems_now(self, now: float, seen: Seen, connected: bool, fps: float | None, gravity: Gravity | None,
                      reference_gravity: Gravity | None, stopped: bool) -> dict[str, Detail]:
        """What is wrong right now (not yet waited for): problem → details."""
        raw: dict[str, Detail] = {}
        if connected:
            if not self._stream_started:
                self._stream_started = self._stream_seen = now
            self._ever_connected = True
        else:
            self._stream_started = 0.0
            if stopped:
                self._ever_connected = False
                self._active.clear()
                self._raw_since.clear()
                self._clear_since.clear()
            elif self._ever_connected:
                raw['lost'] = {}
        if connected and seen.reading:
            self._picture_problems(now, seen.reading, seen, raw)
        if connected and fps is not None and 0 < fps < self._lens.slow_fps_below:
            raw['slow'] = {'fps': fps}
        if connected and gravity and reference_gravity:
            tilt = tilt_degrees(gravity, reference_gravity, trusted=self._lens.trusted_gravity)
            if tilt > self._lens.bump_degrees:
                raw['bumped'] = {'degrees': round(tilt)}
        return raw

    def _picture_problems(self, now: float, reading: Reading, seen: Seen, raw: dict[str, Detail]) -> None:
        lens = self._lens
        b = reading.brightness
        if b < lens.covered_below or reading.sharpness < lens.covered_detail_below or self._one_thing_fills(seen):
            raw['covered'] = {}
        elif b < lens.dark_below:
            raw['dark'] = {}
        if reading.glare > lens.glare_above:
            raw['glare'] = {}
        if reading.sharpness < lens.blur_below and b >= lens.dark_below:
            raw['blurry'] = {}
        if now - max(seen.last_seen_anything, self._stream_seen) > self._waits.empty:
            raw['empty'] = {}
        for label, box in seen.boxes:
            if self._watched(label) and seen.size[0]:
                side = cut_off_side(box, seen.size, lens.edge_touch_px, lens.fills_picture)
                if side:
                    prev = self._cut.get(label)
                    since = prev.since if prev and prev.side == side else now
                    self._cut[label] = _Cut(side=side, since=since, last=now)
        # Forget objects that haven't looked cut off for a while (allows a moment of flicker in the detections)
        self._cut = {c: v for c, v in self._cut.items() if now - v.last < lens.cutoff_forget_sec}
        ready = [(v.since, c, v.side) for c, v in self._cut.items() if now - v.since >= self._waits.cutoff]
        if ready:
            _, label, side = min(ready)
            raw['cutoff'] = {'object': label, 'side': side}

    def _confirm_and_clear(self, now: float, raw: dict[str, Detail], hidden: set[str]) -> list[Said]:
        """Confirm problems that lasted long enough; drop ones that stayed gone long enough (one thanks)."""
        out: list[Said] = []
        for key in PRIORITY:
            if key in hidden:
                continue
            if key in raw:
                self._clear_since.pop(key, None)
                started = self._raw_since.setdefault(key, now)
                hold = 0 if key in WAITED_ALREADY else getattr(self._waits, key)
                active = self._active.get(key)
                if active is None and now - started >= hold:
                    self._active[key] = _Active(since=started, said_at=0.0, detail=raw[key])
                elif active is not None:
                    if key == 'cutoff' and active.detail.get('object') != raw[key].get('object'):
                        active.said_at = 0.0      # a different object: OK to mention it
                    active.detail = raw[key]
            else:
                self._raw_since.pop(key, None)
                if key in self._active:
                    gone = self._clear_since.setdefault(key, now)
                    if now - gone >= self._lens.fixed_after_sec:
                        info = self._active.pop(key)
                        self._clear_since.pop(key, None)
                        if info.said_at and key in self._thanked and not out:
                            out.append(Said(key, 'fixed'))
        return out

    def _say_one(self, now: float) -> Said | None:
        """The most important problem not mentioned lately (and not already going away: after quiet hours, a
        problem nobody heard that has since cleared up isn't brought up)."""
        for key in PRIORITY:
            info = self._active.get(key)
            if not info or key in self._clear_since or (info.said_at and now - info.said_at < self._repeat_after):
                continue
            if key == 'cutoff':
                obj = info.detail.get('object', '')
                said = self._cutoff_said.get(obj)
                if said is not None and now - said < self._repeat_after * self._lens.cutoff_repeat_times:
                    continue
                self._cutoff_said[obj] = now
            info.said_at = now
            return Said(key, 'problem', info.detail)
        return None

    def _one_thing_fills(self, seen: Seen) -> bool:
        """Something held right in front of the lens (a book, a box): one box covering most of the picture.
        People and the things in ignore_labels (the desk itself, furniture) don't count."""
        return any(label not in self._ignore and box_share(box, seen.size) > self._lens.covered_by_box_share
                   for label, box in seen.boxes)

    def _watched(self, label: str) -> bool:
        if self._watch:
            return label in self._watch
        return label not in self._ignore

    def forget(self, key: str) -> None:
        """Drop a problem without a thank-you (e.g. the desk was just marked again)."""
        self._active.pop(key, None)
        self._raw_since.pop(key, None)
        self._clear_since.pop(key, None)

    def active(self) -> dict[str, Detail]:
        """The confirmed problems, most important first: problem → a copy of its details."""
        return {key: dict(self._active[key].detail) for key in PRIORITY if key in self._active}
