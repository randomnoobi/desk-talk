"""
The roster: who is in the group chat right now.

It listens to detection events (3 a second). When an object has been seen for
`join_after_sec`, its agent joins ("Cup joined the chat"); when it has been
missing for `leave_after_sec`, it leaves ("Cup left the chat"). Both are
published back into the event stream as agent_joined / agent_left.

PresenceTracker is the on/off logic on its own (no camera, no threads), so it
is easy to test. The habit tracker uses it too, with shorter times.
"""

from __future__ import annotations

import threading
import time

from ..events.stream import AGENT_JOINED, AGENT_LEFT
from ..vision import relationships


class PresenceTracker:
    """Turns "seen in this picture" into steady present / gone.

    A key becomes present after it has been seen for `appear_sec` without a gap
    longer than `gone_sec`, and gone after it has been missing for `gone_sec`."""

    def __init__(self, appear_sec: float, gone_sec: float):
        self.appear_sec = float(appear_sec)
        self.gone_sec = float(gone_sec)
        self.run_start: dict = {}      # key → when the current stretch of sightings began
        self.last_seen: dict = {}      # key → last time it was in a picture
        self.present: set = set()
        self.present_since: dict = {}  # key → when it became present
        self.gone_since: dict = {}     # key → when it was declared gone

    def update(self, seen_keys, now: float) -> tuple[list, list]:
        appeared, gone = [], []
        for k in seen_keys:
            last = self.last_seen.get(k)
            if last is None or now - last > self.gone_sec:
                self.run_start[k] = now
            self.last_seen[k] = now
            if k not in self.present and now - self.run_start[k] >= self.appear_sec:
                self.present.add(k)
                self.present_since[k] = now
                self.gone_since.pop(k, None)
                appeared.append(k)
        seen = set(seen_keys)
        for k in list(self.present):
            if k not in seen and now - self.last_seen.get(k, 0) >= self.gone_sec:
                self.present.discard(k)
                self.gone_since[k] = now
                gone.append(k)
        return appeared, gone

    def is_present(self, key) -> bool:
        return key in self.present

    def absent_for(self, key, now: float) -> float | None:
        """Seconds since `key` was last in a picture (None if never seen)."""
        last = self.last_seen.get(key)
        return None if last is None else now - last


class Roster:
    """Consumer of detection events → agent_joined / agent_left events."""

    def __init__(self, profiles, stream, desk, agent_settings, presence_settings=None):
        self.profiles = profiles
        self.stream = stream
        self.desk = desk
        s = agent_settings
        self.min_conf = float(s.get('min_confidence', 0.45))
        self.only_on_desk = bool(s.get('only_on_desk', True))
        self.max_agents = int(s.get('max_agents', 12))
        self.presence = PresenceTracker(s.get('join_after_sec', 3), s.get('leave_after_sec', 45))
        # Objects used for desk geometry: present agents seen within this many seconds.
        self.fresh_sec = float((presence_settings or {}).get('disappear_after_sec', 2.5))
        self._lock = threading.Lock()
        self.members: dict[str, dict] = {}     # id → {'joined_at', 'label', 'det', 'last_seen'}
        self.history: dict[str, dict] = {}     # id → {'joined_at', 'left_at'} for agents seen today

    # ---- helpers ----
    def _on_desk(self, det: dict, fw: int, fh: int) -> bool:
        if not self.only_on_desk or not relationships.DESK_OUTLINE or not fw:
            return True
        x1, y1, x2, y2 = det['bbox']
        base = ((x1 + x2) / 2, y2)                      # where it touches the desk
        return relationships.point_in_outline(base, fw, fh)

    def _best_per_agent(self, objects: list, fw: int, fh: int) -> dict:
        best: dict[str, dict] = {}
        for o in objects:
            prof = self.profiles.for_label(o.get('label', ''))
            if prof is None:
                continue
            need = prof.min_confidence if prof.min_confidence is not None else self.min_conf
            if o.get('conf', 0) < need or not self._on_desk(o, fw, fh):
                continue
            if prof.id not in best or o['conf'] > best[prof.id]['conf']:
                best[prof.id] = o
        return best

    # ---- the consumer ----
    def on_detection(self, ev: dict) -> None:
        now = (float(ev['ts']) if ev.get('ts') is not None else time.time())
        fw, fh = (ev.get('size') or [0, 0])[:2]
        best = self._best_per_agent(ev.get('detected_obj') or [], fw, fh)
        joined, left = [], []
        with self._lock:
            appeared, gone = self.presence.update(best.keys(), now)
            for pid, o in best.items():
                if pid in self.members:
                    self.members[pid]['det'] = o
                    self.members[pid]['last_seen'] = now
            for pid in appeared:
                if len(self.members) >= self.max_agents:
                    self.presence.present.discard(pid)       # full: try again later
                    continue
                o = best[pid]
                self.members[pid] = {'joined_at': now, 'label': o['label'], 'det': o, 'last_seen': now}
                self.history.setdefault(pid, {})['joined_at'] = now
                joined.append((pid, o))
            for pid in gone:
                m = self.members.pop(pid, None)
                if m is not None:
                    self.history.setdefault(pid, {})['left_at'] = now
                    left.append((pid, m))
            objects = {
                pid: {'class_name': m['det']['label'], 'bbox': list(m['det']['bbox']),
                      'center': list(m['det']['center']), 'conf': m['det']['conf'], 'cam': 0}
                for pid, m in self.members.items() if now - m['last_seen'] <= self.fresh_sec
            }
        with self.desk.lock:
            self.desk.objects = objects
            for pid in objects:
                self.desk.object_first_seen.setdefault(pid, now)
                self.desk.object_last_seen[pid] = now
        for pid, o in joined:
            p = self.profiles.get(pid)
            self.stream.publish(AGENT_JOINED, ts=now, agent=pid, name=p.name, label=o['label'],
                                conf=o['conf'])
        for pid, m in left:
            p = self.profiles.get(pid)
            self.stream.publish(AGENT_LEFT, ts=now, agent=pid, name=p.name, label=m['label'],
                                stayed_sec=round(now - m['joined_at'], 1))

    # ---- for the app ----
    def member_ids(self) -> list[str]:
        with self._lock:
            return list(self.members)

    def is_member(self, pid: str) -> bool:
        with self._lock:
            return pid in self.members

    def snapshot(self) -> list[dict]:
        """Agents in the chat now, plus ones that were here earlier (for the chat list)."""
        with self._lock:
            members = {k: dict(v) for k, v in self.members.items()}
            history = {k: dict(v) for k, v in self.history.items()}
        out = []
        for pid in sorted(set(members) | set(history), key=lambda k: -(members.get(k, history.get(k, {})).get('joined_at') or 0)):
            p = self.profiles.get(pid)
            if not p:
                continue
            d = p.public()
            d['on_desk'] = pid in members
            d['joined_at'] = (members.get(pid) or history.get(pid) or {}).get('joined_at')
            d['left_at'] = None if pid in members else history.get(pid, {}).get('left_at')
            if pid in members:
                d['label_seen'] = members[pid]['label']
                d['confidence'] = members[pid]['det'].get('conf')
            out.append(d)
        return out
