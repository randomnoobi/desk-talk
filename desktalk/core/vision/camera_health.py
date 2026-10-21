"""
Lens's eyes: checks on the camera itself (not the objects).

Lens speaks up in the group chat when something about the camera needs fixing: too dark, glare, blurry,
an object cut off at the side of the picture, the camera got bumped, the video is slow or stopped. When
the problem goes away, Lens says thanks. All the numbers are in settings → lens.

    health = CameraHealth(config.lens, source_kind='pi', moved_within_sec=config.camera.moved_within_sec,
                          ignore_labels=config.agents_file.ignore)
    health.sample(picture, detections, now)          # every analysed picture (measures about once a second)
    for msg in health.check(now, connected=True, fps=2.9, ...):   # every couple of seconds (Lens's job)
        msg['text']

The camera job calls sample() and Lens's job calls check(), from different threads.
"""

from __future__ import annotations

import threading
from collections.abc import Iterable, Sequence
from typing import Any

import cv2
import numpy as np

from desktalk.config import LensSettings
from desktalk.domain import Detection

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
# key: (short title, how to fix it), for the app's camera panel
TITLES = {
    'lost': ('No video from the phone', 'Keep the phone unlocked with the Desk Talk page open.'),
    'covered': ('Camera is covered', 'Check nothing is on the lens and the camera is facing the desk.'),
    'dark': ('Too dark', 'Turn on a lamp near the desk.'),
    'glare': ('Glare', 'Angle the camera away from windows or bright lights.'),
    'blurry': ('Blurry picture', 'Wipe the lens, or move the camera back a little so it can focus.'),
    'bumped': ('Phone was moved', 'Put the phone back, or mark the desk edges again in Settings.'),
    'empty': ('Nothing recognized', 'Tilt the camera so the whole desk top is in the picture.'),
    'cutoff': ('Something is cut off', 'Turn or move the camera back so the whole object fits.'),
    'slow': ('Video is slow', 'Move the camera (or the Wi-Fi router) closer, and keep it plugged in.'),
}
FIXED_TEXT = {
    'lost': "Video's back! I can see the desk again.",
    'covered': 'There we go, I can see again.',
    'dark': 'Much better, thanks for the light!',
    'glare': 'Glare is gone. Thanks!',
    'blurry': 'Sharp again. I can see clearly now.',
    'bumped': "Thanks, I'm back where I was.",
    'empty': 'I can see the desk now. Thanks!',
}
SIDE_HINT = {
    'left': 'point me a little more to the left',
    'right': 'point me a little more to the right',
    'top': 'tilt me up a little',
    'bottom': 'tilt me down a little',
}
# What to check when the video stops, by camera (settings → camera.source).
LOST_TEXT = {
    'pi': ('I lost the pictures from the Raspberry Pi. Is it still on and connected to the Wi-Fi? '
           'On the Pi, check that the camera program is still running (sudo systemctl status desk-talk-camera, '
           'or start it with python3 -m raspberry_pi.camera).'),
    'phone': ('I lost the video from the phone. Is its screen still on? Phones stop the camera '
              'when they lock, so keep the Desk Talk page open on it.'),
    'webcam': 'I lost the webcam picture. Is it still plugged in, or is another app using it?',
}
LOST_FIX = {
    'pi': 'Check the Pi is on, on the Wi-Fi, and running the camera program (python3 -m raspberry_pi.camera).',
    'phone': 'Keep the phone unlocked with the Desk Talk page open.',
    'webcam': 'Plug the webcam back in or close other apps using it.',
}
EDGE_TIP = ('Tip from me, Lens: open Settings and tap the corners of your desk on the picture. Then I know where '
            'the desk really ends, and everyone can warn you before something slides off the edge.')
PHONE_TIP_KEYS = ('covered', 'dark', 'glare', 'blurry', 'bumped', 'cutoff')
PEOPLE = 'person'          # the detector's word for people: never "cut off", and not "something on the desk"


def measure(picture: Any, width: int, white_level: float) -> dict[str, Any]:
    """A small grey copy of the picture (`width` wide), its brightness (0-255), the share of pure-white
    pixels (glare) and how sharp it is."""
    h, w = picture.shape[:2]
    small = cv2.resize(picture, (width, max(1, round(h * width / w))), interpolation=cv2.INTER_AREA)
    gray = small if small.ndim == 2 else cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    return {
        'gray': gray,
        'brightness': float(gray.mean()),
        'glare': float((gray >= white_level).mean()),
        'sharpness': float(cv2.Laplacian(gray, cv2.CV_64F).var()),
    }


def cut_off_side(box: Sequence[float], size: tuple[int, int], touch_px: float, fills: float) -> str | None:
    """Which side of the picture a box touches ('left', 'top' ...), or None. A box that fills most of the
    picture is never cut off: turning the camera wouldn't help."""
    x1, y1, x2, y2 = box
    fw, fh = size
    if (x2 - x1) > fills * fw or (y2 - y1) > fills * fh:
        return None
    gaps = {'left': x1, 'top': y1, 'right': fw - x2, 'bottom': fh - y2}
    side, gap = min(gaps.items(), key=lambda kv: kv[1])
    return side if gap <= touch_px else None


def _angle(g1, g2) -> float:
    """Degrees between two gravity readings (0 when either is missing or too small to trust)."""
    a, b = float(np.linalg.norm(g1)), float(np.linalg.norm(g2))
    if a < 1 or b < 1:
        return 0.0
    cos = float(np.dot(g1, g2)) / (a * b)
    return float(np.degrees(np.arccos(max(-1.0, min(1.0, cos)))))


class CameraHealth:
    """Implements PictureCheck (sample, moved_recently) and CameraHealthView (check)."""

    def __init__(self, lens: LensSettings, *, source_kind: str, moved_within_sec: float,
                 ignore_labels: Iterable[str] = (), watch_labels: Iterable[str] = ()):
        self._lens = lens
        self._waits = lens.wait_before_saying_sec
        self._repeat_after = lens.repeat_after_min * 60
        self._source_kind = source_kind
        self._moved_within = moved_within_sec
        # Objects checked for "cut off at the side": those in watch_labels, or (when there are none)
        # anything not in ignore_labels.
        self._watch = set(watch_labels)
        self._ignore = set(ignore_labels) | {PEOPLE}
        self._lock = threading.Lock()

        # what sample() saw
        self._last_sample = 0.0
        self._prev_gray = None
        self._latest: dict[str, float] | None = None
        self._latest_ts = 0.0
        self._boxes: list[tuple[str, tuple[float, float, float, float]]] = []
        self._size = (0, 0)
        self._motion_ts = 0.0           # last time the picture changed a lot
        self._last_seen_anything = 0.0

        # what check() keeps
        self._stream_started = 0.0      # when the current run of video began
        self._ever_connected = False
        self._raw_since: dict[str, float] = {}      # problem → when it started (not yet confirmed)
        self._clear_since: dict[str, float] = {}    # problem → when it went away
        self._active: dict[str, dict[str, Any]] = {}   # confirmed problems → since, said_at, detail
        self._last_spoke = 0.0
        self._edge_tip_given = False
        self._cut: dict[str, dict[str, Any]] = {}   # object → side, since, last (while it looks cut off)
        self._cutoff_said: dict[str, float] = {}    # object → when Lens last mentioned it

    # ── PictureCheck: every analysed picture (cheap: measures at most once per sample_every_sec) ──
    def sample(self, picture: Any, detections: Sequence[Detection], now: float) -> None:
        if now - self._last_sample < self._lens.sample_every_sec:
            return
        self._last_sample = now
        reading = measure(picture, self._lens.measure_width, self._lens.white_level)
        gray = reading.pop('gray')
        with self._lock:
            same_size = self._prev_gray is not None and self._prev_gray.shape == gray.shape
            if same_size and float(cv2.absdiff(gray, self._prev_gray).mean()) > self._lens.motion_above:
                self._motion_ts = now
            self._prev_gray = gray
            self._latest = reading
            self._latest_ts = now
            self._boxes = [(d.label, (d.bbox.x1, d.bbox.y1, d.bbox.x2, d.bbox.y2)) for d in detections]
            self._size = (picture.shape[1], picture.shape[0])
            if any(label != PEOPLE for label, _ in self._boxes):
                self._last_seen_anything = now

    def moved_recently(self, now: float) -> bool:
        with self._lock:
            moved = self._motion_ts
        return bool(moved) and 0 <= now - moved < self._moved_within

    # ── CameraHealthView: every couple of seconds; returns what Lens should say ──
    def check(self, now: float, *, connected: bool, fps: float | None = None, gravity: Any = None,
              reference_gravity: Any = None, has_outline: bool = True, stopped: bool = False) -> list[dict[str, Any]]:
        """`stopped` = the video was stopped on purpose (the phone's Stop button): no complaint."""
        raw = self._problems_now(now, connected, fps, gravity, reference_gravity, stopped)
        hidden = set()
        for big, small in HIDES.items():
            if big in raw:
                hidden |= small
        for key in hidden:
            raw.pop(key, None)
        out = self._confirm_and_clear(now, raw, hidden)
        if not self._last_spoke or now - self._last_spoke >= self._lens.speak_gap_sec:
            said = self._say_one(now)
            if said:
                out.append(said)
        if (not out and not has_outline and not self._edge_tip_given and not self._active
                and self._stream_started and now - self._stream_started >= self._lens.edge_tip_after_sec
                and (not self._last_spoke or now - self._last_spoke >= self._lens.speak_gap_sec)):
            self._edge_tip_given = True
            out.append({'key': 'edge_tip', 'kind': 'tip', 'text': EDGE_TIP})
        if any(m['kind'] != 'fixed' for m in out):
            self._last_spoke = now    # a quick "thanks, fixed" never delays the next warning
        return out

    def _problems_now(self, now, connected, fps, gravity, reference_gravity, stopped) -> dict[str, dict[str, Any]]:
        """What is wrong right now (not yet waited for): problem → details."""
        with self._lock:
            fresh = now - self._latest_ts < self._lens.reading_fresh_sec
            reading = self._latest if fresh else None
            boxes = list(self._boxes)
            size = self._size
        raw: dict[str, dict[str, Any]] = {}
        if connected:
            if not self._stream_started:
                self._stream_started = now
                with self._lock:
                    self._last_seen_anything = max(self._last_seen_anything, now)
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
        if connected and reading:
            self._picture_problems(now, reading, boxes, size, raw)
        if connected and fps is not None and 0 < fps < self._lens.slow_fps_below:
            raw['slow'] = {'fps': fps}
        if connected and gravity and reference_gravity:
            tilt = _angle(gravity, reference_gravity)
            if tilt > self._lens.bump_degrees:
                raw['bumped'] = {'degrees': round(tilt)}
        return raw

    def _picture_problems(self, now, reading, boxes, size, raw) -> None:
        lens = self._lens
        b = reading['brightness']
        if b < lens.covered_below:
            raw['covered'] = {}
        elif b < lens.dark_below:
            raw['dark'] = {}
        if reading['glare'] > lens.glare_above:
            raw['glare'] = {}
        if reading['sharpness'] < lens.blur_below and b >= lens.dark_below:
            raw['blurry'] = {}
        with self._lock:
            last_seen = self._last_seen_anything
        if now - last_seen > self._waits.empty:
            raw['empty'] = {}
        for label, box in boxes:
            if self._watched(label) and size[0]:
                side = cut_off_side(box, size, lens.edge_touch_px, lens.fills_picture)
                if side:
                    prev = self._cut.get(label)
                    since = prev['since'] if prev and prev['side'] == side else now
                    self._cut[label] = {'side': side, 'since': since, 'last': now}
        # Forget objects that haven't looked cut off for a while (allows a moment of flicker in the detections)
        self._cut = {c: v for c, v in self._cut.items() if now - v['last'] < lens.cutoff_forget_sec}
        ready = [(v['since'], c, v['side']) for c, v in self._cut.items() if now - v['since'] >= self._waits.cutoff]
        if ready:
            _, label, side = min(ready)
            raw['cutoff'] = {'object': label, 'side': side}

    def _confirm_and_clear(self, now, raw, hidden) -> list[dict[str, Any]]:
        """Confirm problems that lasted long enough; drop ones that stayed gone long enough (one thanks)."""
        out: list[dict[str, Any]] = []
        for key in PRIORITY:
            if key in hidden:
                continue
            if key in raw:
                self._clear_since.pop(key, None)
                started = self._raw_since.setdefault(key, now)
                # "empty" and "cutoff" already waited their time in _picture_problems
                hold = 0 if key in ('empty', 'cutoff') else getattr(self._waits, key)
                if key not in self._active and now - started >= hold:
                    self._active[key] = {'since': started, 'said_at': 0.0, 'detail': raw[key]}
                elif key in self._active:
                    if key == 'cutoff' and self._active[key]['detail'].get('object') != raw[key].get('object'):
                        self._active[key]['said_at'] = 0.0      # a different object: OK to mention it
                    self._active[key]['detail'] = raw[key]
            else:
                self._raw_since.pop(key, None)
                if key in self._active:
                    gone = self._clear_since.setdefault(key, now)
                    if now - gone >= self._lens.fixed_after_sec:
                        info = self._active.pop(key)
                        self._clear_since.pop(key, None)
                        if info['said_at'] and key in FIXED_TEXT and not out:
                            out.append({'key': key, 'kind': 'fixed', 'text': FIXED_TEXT[key]})
        return out

    def _say_one(self, now) -> dict[str, Any] | None:
        """The most important problem not mentioned lately."""
        for key in PRIORITY:
            info = self._active.get(key)
            if not info or (info['said_at'] and now - info['said_at'] < self._repeat_after):
                continue
            if key == 'cutoff':
                obj = info['detail'].get('object', '')
                said = self._cutoff_said.get(obj)
                if said is not None and now - said < self._repeat_after * self._lens.cutoff_repeat_times:
                    continue
                self._cutoff_said[obj] = now
            info['said_at'] = now
            return {'key': key, 'kind': 'problem', 'text': self._problem_text(key, info['detail']),
                    'detail': info['detail']}
        return None

    def _watched(self, label: str) -> bool:
        if self._watch:
            return label in self._watch
        return label not in self._ignore

    def _problem_text(self, key: str, d: dict[str, Any]) -> str:
        if key == 'lost':
            return LOST_TEXT.get(self._source_kind, LOST_TEXT['phone'])
        if key == 'covered':
            return ('Everything just went black. Is something covering my lens, '
                    'or did the camera get turned away?')
        if key == 'dark':
            return "It's too dark for me to see the desk well. Could you turn on a lamp?"
        if key == 'glare':
            return ('Something really bright is shining into my camera. '
                    'Can you angle me away from the window or lamp?')
        if key == 'blurry':
            return ('Everything looks blurry to me. Could you wipe my lens, '
                    'or move me back a little so I can focus?')
        if key == 'bumped':
            return (f"Whoa, I think I got bumped. I'm tilted about {d.get('degrees', self._lens.bump_degrees)}° from "
                    'where I was, so the desk edges you marked are off now. Put me back, '
                    'or mark the edges again in Settings.')
        if key == 'empty':
            return ("I can't spot anything I recognize on the desk. Am I pointed at it? "
                    'Tilt me so the whole desk top is in the picture.')
        if key == 'cutoff':
            obj = d.get('object', 'something')
            side = d.get('side', 'left')
            return (f"Your {obj} is cut off at the {side} side of the picture, so the others can't "
                    f"see all of it. Could you {SIDE_HINT.get(side, 'move me back a little')}?")
        return (f"The pictures are only coming through at about {d.get('fps', 0):.1f} a second. "
                'Keeping the camera near the Wi-Fi router (and plugged in) helps.')

    def forget(self, key: str) -> None:
        """Drop a problem without a thank-you (e.g. the desk was just marked again)."""
        self._active.pop(key, None)
        self._raw_since.pop(key, None)
        self._clear_since.pop(key, None)

    # ── for the app ──
    def issues(self) -> list[dict[str, str]]:
        """Current problems, most important first: [{key, title, fix}]."""
        out = []
        for key in PRIORITY:
            if key not in self._active:
                continue
            title, fix = TITLES[key]
            if key == 'lost':
                title = 'No pictures from the camera'
                fix = LOST_FIX.get(self._source_kind, fix)
            d = self._active[key]['detail']
            if key == 'cutoff' and d.get('object'):
                title = f"{d['object'].capitalize()} is cut off ({d.get('side')})"
            out.append({'key': key, 'title': title, 'fix': fix})
        return out

    def phone_tip(self) -> str | None:
        """A few words to show on the phone screen itself, or None."""
        for key in PHONE_TIP_KEYS:
            if key in self._active:
                return TITLES[key][0]
        return None
