"""
Lens: the phone camera's own character.

Lens watches the video itself (not the objects) and speaks up in the group chat
when something about the camera needs fixing: too dark, glare, blurry, an object
cut off at the side of the picture, the phone got bumped, the video stopped, and
so on. When the problem goes away, Lens says thanks.

This file has no Flask or AI code, so it is easy to test:

    health = CameraHealth(watch_classes={'laptop', 'cup'})
    health.sample(frame, boxes=[('cup', [x1, y1, x2, y2])])      # about once a second
    for msg in health.check(now, connected=True, fps=9.5):       # every couple of seconds
        print(msg['text'])
"""

from __future__ import annotations

import threading
import time

import cv2
import numpy as np

SAMPLE_WIDTH = 240          # frames are shrunk to this width before measuring (fast)
SAMPLE_EVERY_SEC = 1.0

COVERED_BELOW = 12          # average brightness 0-255
DARK_BELOW = 45
GLARE_ABOVE = 0.30          # share of pixels that are pure white
BLUR_BELOW = 25             # sharpness score (Laplacian variance at SAMPLE_WIDTH)
MOTION_ABOVE = 6.0          # average pixel change between samples that counts as movement
BUMP_DEGREES = 12           # phone tipped this far from where it was when the desk was marked
SLOW_FPS = 3.0
EDGE_TOUCH_PX = 3           # a box this close to the picture border counts as cut off

SPEAK_GAP_SEC = 20          # at least this long between two different problems
REPEAT_AFTER_SEC = 20 * 60  # the same problem is mentioned again at most this often
CLEAR_HOLD_SEC = 5          # a problem must stay gone this long before "fixed!"
EDGE_TIP_AFTER_SEC = 120    # one-time tip about marking the desk edges

# key: (seconds the problem must last before Lens says something, short title, how to fix)
ISSUES = {
    'lost':    (15, 'No video from the phone', 'Keep the phone unlocked with the Desk Talk page open.'),
    'covered': (4, 'Camera is covered', 'Check nothing is on the lens and the phone is facing the desk.'),
    'dark':    (8, 'Too dark', 'Turn on a lamp near the desk.'),
    'glare':   (8, 'Glare', 'Angle the phone away from windows or bright lights.'),
    'blurry':  (12, 'Blurry picture', 'Wipe the lens, or move the phone back a little so it can focus.'),
    'bumped':  (5, 'Phone was moved', 'Put the phone back, or mark the desk edges again in Settings.'),
    'empty':   (180, 'Nothing recognized', 'Tilt the phone so the whole desk top is in the picture.'),
    'cutoff':  (45, 'Something is cut off', 'Turn or move the phone back so the whole object fits.'),
    'slow':    (30, 'Video is slow', 'Keep the phone close to the Wi-Fi router and plugged in.'),
}
PRIORITY = ['lost', 'covered', 'dark', 'glare', 'blurry', 'bumped', 'empty', 'cutoff', 'slow']
# While one of these is happening, the picture-based checks below it can't be trusted.
MASKS = {
    'lost': {'covered', 'dark', 'glare', 'blurry', 'empty', 'cutoff', 'slow', 'bumped'},
    'covered': {'dark', 'glare', 'blurry', 'empty', 'cutoff'},
    'dark': {'blurry', 'empty', 'cutoff'},
    'glare': {'blurry', 'empty'},
    'blurry': {'empty', 'cutoff'},
}

FIXED_TEXT = {
    'lost': "Video's back! I can see the desk again.",
    'covered': 'There we go, I can see again.',
    'dark': 'Much better, thanks for the light!',
    'glare': 'Glare is gone. Thanks!',
    'blurry': "Sharp again. I can see clearly now.",
    'bumped': "Thanks, I'm back where I was.",
    'empty': 'I can see the desk now. Thanks!',
}

SIDE_HINT = {
    'left': 'point me a little more to the left',
    'right': 'point me a little more to the right',
    'top': 'tilt me up a little',
    'bottom': 'tilt me down a little',
}


def measure(frame):
    """Small grayscale copy plus brightness, glare share and sharpness."""
    h, w = frame.shape[:2]
    small = cv2.resize(frame, (SAMPLE_WIDTH, max(1, round(h * SAMPLE_WIDTH / w))),
                       interpolation=cv2.INTER_AREA)
    gray = small if small.ndim == 2 else cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    return {
        'gray': gray,
        'brightness': float(gray.mean()),
        'glare': float((gray >= 250).mean()),
        'sharpness': float(cv2.Laplacian(gray, cv2.CV_64F).var()),
    }


def cut_off_side(bbox, fw, fh):
    """Which picture border a box is touching ('left', 'top', ...), or None.
    Boxes that fill most of the picture are ignored (nothing to fix by turning)."""
    x1, y1, x2, y2 = bbox
    if (x2 - x1) > 0.9 * fw or (y2 - y1) > 0.9 * fh:
        return None
    gaps = {'left': x1, 'top': y1, 'right': fw - x2, 'bottom': fh - y2}
    side, gap = min(gaps.items(), key=lambda kv: kv[1])
    return side if gap <= EDGE_TOUCH_PX else None


def _angle(g1, g2):
    a = float(np.linalg.norm(g1)); b = float(np.linalg.norm(g2))
    if a < 1 or b < 1:
        return 0.0
    cos = float(np.dot(g1, g2)) / (a * b)
    return float(np.degrees(np.arccos(max(-1.0, min(1.0, cos)))))


class CameraHealth:
    def __init__(self, watch_classes=()):
        self.watch_classes = set(watch_classes)
        self._lock = threading.Lock()
        self._last_sample = 0.0
        self._prev_gray = None
        self.latest = None            # last measure() result (without the image)
        self.latest_ts = 0.0
        self.boxes = []
        self.frame_size = (0, 0)
        self.motion_ts = 0.0          # last time something in the picture moved
        self.last_seen_anything = 0.0
        self.stream_started = 0.0     # when the current run of video began
        self.ever_connected = False

        self._raw_since = {}          # problem key -> when it started (not yet confirmed)
        self._clear_since = {}        # problem key -> when it went away
        self.active = {}              # confirmed problems: key -> {'since', 'said_at', 'detail'}
        self._last_spoke = 0.0
        self._edge_tip_given = False
        self._cut = {}                # object class -> {'side', 'since', 'last'} while cut off
        self._cutoff_said = {}        # object class -> last time Lens mentioned it

    # ---- called by the camera loop with every frame (cheap: works at most once a second) ----
    def sample(self, frame, boxes=(), now=None):
        now = now or time.time()
        if now - self._last_sample < SAMPLE_EVERY_SEC:
            return False
        self._last_sample = now
        m = measure(frame)
        gray = m.pop('gray')
        with self._lock:
            if self._prev_gray is not None and self._prev_gray.shape == gray.shape:
                change = float(cv2.absdiff(gray, self._prev_gray).mean())
                if change > MOTION_ABOVE:
                    self.motion_ts = now
            self._prev_gray = gray
            self.latest = m
            self.latest_ts = now
            self.boxes = [(c, list(b)) for c, b in boxes]
            self.frame_size = (frame.shape[1], frame.shape[0])
            if any(c != 'person' for c, _ in self.boxes):
                self.last_seen_anything = now
        return True

    # ---- called every couple of seconds; returns things Lens should say ----
    def check(self, now=None, *, connected=True, fps=None, gravity=None,
              reference_gravity=None, has_outline=True, stopped=False):
        """`stopped` means the user pressed Stop on the phone on purpose (no complaint)."""
        now = now or time.time()
        with self._lock:
            m = self.latest if (now - self.latest_ts) < 10 else None
            boxes = list(self.boxes)
            fw, fh = self.frame_size

        raw = {}      # problem key -> detail dict (what is wrong right now)
        if connected:
            if not self.stream_started:
                self.stream_started = now
                self.last_seen_anything = max(self.last_seen_anything, now)
            self.ever_connected = True
        else:
            self.stream_started = 0.0
            if stopped:
                self.ever_connected = False
                self.active.clear(); self._raw_since.clear(); self._clear_since.clear()
            elif self.ever_connected:
                raw['lost'] = {}

        if connected and m:
            b = m['brightness']
            if b < COVERED_BELOW:
                raw['covered'] = {}
            elif b < DARK_BELOW:
                raw['dark'] = {}
            if m['glare'] > GLARE_ABOVE:
                raw['glare'] = {}
            if m['sharpness'] < BLUR_BELOW and b >= DARK_BELOW:
                raw['blurry'] = {}
            if now - self.last_seen_anything > ISSUES['empty'][0]:
                raw['empty'] = {}
            for cls, bbox in boxes:
                if cls in self.watch_classes and fw:
                    side = cut_off_side(bbox, fw, fh)
                    if side:
                        prev = self._cut.get(cls)
                        since = prev['since'] if prev and prev['side'] == side else now
                        self._cut[cls] = {'side': side, 'since': since, 'last': now}
            # Forget objects that haven't been cut off for a while (allows brief detection flicker)
            self._cut = {c: v for c, v in self._cut.items() if now - v['last'] < 8}
            ready = [(v['since'], c, v['side']) for c, v in self._cut.items()
                     if now - v['since'] >= ISSUES['cutoff'][0]]
            if ready:
                _, cls, side = min(ready)
                raw['cutoff'] = {'object': cls, 'side': side}
        if connected and fps is not None and 0 < fps < SLOW_FPS:
            raw['slow'] = {'fps': fps}
        if connected and gravity and reference_gravity:
            tilt = _angle(gravity, reference_gravity)
            if tilt > BUMP_DEGREES:
                raw['bumped'] = {'degrees': round(tilt)}

        # Hide problems that are only side effects of a bigger one. While hidden,
        # they are neither confirmed nor counted as fixed.
        masked = set()
        for big, hidden in MASKS.items():
            if big in raw:
                masked |= hidden
        for key in masked:
            raw.pop(key, None)

        out = []
        # Start / confirm problems
        for key in PRIORITY:
            if key in masked:
                continue
            if key in raw:
                self._clear_since.pop(key, None)
                started = self._raw_since.setdefault(key, now)
                # "empty" and "cutoff" already waited their time above
                hold = 0 if key in ('empty', 'cutoff') else ISSUES[key][0]
                if key not in self.active and now - started >= hold:
                    self.active[key] = {'since': started, 'said_at': 0.0, 'detail': raw[key]}
                elif key in self.active:
                    if key == 'cutoff' and self.active[key]['detail'].get('object') != raw[key].get('object'):
                        self.active[key]['said_at'] = 0.0   # a different object: OK to mention it
                    self.active[key]['detail'] = raw[key]
            else:
                self._raw_since.pop(key, None)
                if key in self.active:
                    gone = self._clear_since.setdefault(key, now)
                    if now - gone >= CLEAR_HOLD_SEC:
                        info = self.active.pop(key)
                        self._clear_since.pop(key, None)
                        if info['said_at'] and key in FIXED_TEXT and not out:
                            # one thank-you is enough, even if several things cleared at once
                            out.append({'key': key, 'kind': 'fixed', 'text': FIXED_TEXT[key]})

        # Say the most important problem that hasn't been mentioned recently
        if not self._last_spoke or now - self._last_spoke >= SPEAK_GAP_SEC:
            for key in PRIORITY:
                info = self.active.get(key)
                if not info or (info['said_at'] and now - info['said_at'] < REPEAT_AFTER_SEC):
                    continue
                if key == 'cutoff':
                    obj = info['detail'].get('object', '')
                    if obj in self._cutoff_said and now - self._cutoff_said[obj] < REPEAT_AFTER_SEC * 1.5:
                        continue
                    self._cutoff_said[obj] = now
                info['said_at'] = now
                out.append({'key': key, 'kind': 'problem',
                            'text': self._problem_text(key, info['detail']),
                            'detail': info['detail']})
                break

        # One-time tip: mark the desk edges
        if (not out and not has_outline and not self._edge_tip_given and not self.active
                and self.stream_started and now - self.stream_started >= EDGE_TIP_AFTER_SEC
                and (not self._last_spoke or now - self._last_spoke >= SPEAK_GAP_SEC)):
            self._edge_tip_given = True
            out.append({'key': 'edge_tip', 'kind': 'tip', 'text': (
                "Tip from me, Lens: open Settings and tap the corners of your desk on the picture. "
                "Then I know where the desk really ends, and everyone can warn you before something "
                "slides off the edge.")})

        if any(m['kind'] != 'fixed' for m in out):
            self._last_spoke = now    # a quick "thanks, fixed" never delays the next warning
        return out

    @staticmethod
    def _problem_text(key, d):
        if key == 'lost':
            return ("I lost the video from the phone. Is its screen still on? Phones stop the camera "
                    "when they lock, so keep the Desk Talk page open on it.")
        if key == 'covered':
            return ("Everything just went black. Is something covering my lens, "
                    "or did the phone get turned face down?")
        if key == 'dark':
            return "It's too dark for me to see the desk well. Could you turn on a lamp?"
        if key == 'glare':
            return ("Something really bright is shining into my camera. "
                    "Can you angle me away from the window or lamp?")
        if key == 'blurry':
            return ("Everything looks blurry to me. Could you wipe my lens, "
                    "or move me back a little so I can focus?")
        if key == 'bumped':
            return (f"Whoa, I think I got bumped. I'm tilted about {d.get('degrees', BUMP_DEGREES)}° from "
                    "where I was, so the desk edges you marked are off now. Put me back, "
                    "or mark the edges again in Settings.")
        if key == 'empty':
            return ("I can't spot anything I recognize on the desk. Am I pointed at it? "
                    "Tilt me so the whole desk top is in the picture.")
        if key == 'cutoff':
            obj = d.get('object', 'something')
            side = d.get('side', 'left')
            return (f"Your {obj} is cut off at the {side} side of the picture, so the others can't "
                    f"see all of it. Could you {SIDE_HINT.get(side, 'move me back a little')}?")
        if key == 'slow':
            return (f"The video is only coming through at about {d.get('fps', 0):.0f} frames a second. "
                    "Keeping the phone near the Wi-Fi router (and plugged in) helps.")
        return ISSUES.get(key, ('', 'Camera problem', ''))[1]

    def forget(self, key):
        """Drop a problem without a thank-you (e.g. the desk was just re-marked)."""
        self.active.pop(key, None)
        self._raw_since.pop(key, None)
        self._clear_since.pop(key, None)

    # ---- for the UI ----
    def issues(self):
        """Current problems, most important first: [{key, title, fix}]."""
        out = []
        for key in PRIORITY:
            if key in self.active:
                _, title, fix = ISSUES[key]
                d = self.active[key]['detail']
                if key == 'cutoff' and d.get('object'):
                    title = f"{d['object'].capitalize()} is cut off ({d.get('side')})"
                out.append({'key': key, 'title': title, 'fix': fix})
        return out

    def phone_tip(self):
        """A few words to show on the phone screen itself, or None."""
        for key in ('covered', 'dark', 'glare', 'blurry', 'bumped', 'cutoff'):
            if key in self.active:
                return ISSUES[key][1]
        return None
