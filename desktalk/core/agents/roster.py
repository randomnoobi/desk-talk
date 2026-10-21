"""
The roster: who is in the group chat right now.

It listens to DetectionEvents (about 3 a second). When an object has been seen for
agents.join_after_sec, its agent joins ("Cup joined the chat"); when it has been missing for
agents.leave_after_sec, it leaves ("Cup left the chat"). Both go back on the event bus as
AgentJoined / AgentLeft. No pictures (camera off) means no events, so nobody leaves.

It also keeps DeskState.objects: the agents in the chat that were in a picture just now
(presence.disappear_after_sec), which the desk rules and the live view use.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from desktalk.config import AgentsSettings, PresenceSettings
from desktalk.core.agents.presence import PresenceTracker
from desktalk.core.agents.profiles import ProfileLookup
from desktalk.desk_state import DeskState
from desktalk.domain import AgentJoined, AgentLeft, Detection, DetectionEvent
from desktalk.ports import EventBus

log = logging.getLogger(__name__)

# (x, y, picture width, picture height) → is this point on the desk? True when no desk outline is marked.
OnDesk = Callable[[float, float, int, int], bool]


class Roster:
    """Detection events → AgentJoined / AgentLeft. Implements RosterView."""

    def __init__(self, bus: EventBus, profiles: ProfileLookup, desk: DeskState, agents: AgentsSettings,
                 presence: PresenceSettings, on_desk: OnDesk | None = None):
        self._bus = bus
        self._profiles = profiles
        self._desk = desk
        self._min_conf = agents.min_confidence
        self._only_on_desk = agents.only_on_desk
        self._max_agents = agents.max_agents
        self._on_desk = on_desk
        self._presence = PresenceTracker(agents.join_after_sec, agents.leave_after_sec)
        self._fresh_sec = presence.disappear_after_sec       # "on the desk right now" for DeskState.objects
        self._lock = threading.Lock()                         # snapshot() is read from the web server's thread
        self._members: dict[str, dict] = {}      # id → {'joined_at', 'label', 'det', 'last_seen'}, in join order
        self._history: dict[str, dict] = {}      # id → {'joined_at', 'left_at'} for agents seen before
        bus.subscribe('roster', self.on_detection, types=[DetectionEvent])

    # ── helpers ──
    def _is_on_desk(self, det: Detection, width: int, height: int) -> bool:
        if not self._only_on_desk or self._on_desk is None or not width:
            return True
        cx, _ = det.bbox.center
        return self._on_desk(cx, det.bbox.y2, width, height)    # where it touches the desk

    def _best_per_agent(self, detections, width: int, height: int) -> dict[str, Detection]:
        best: dict[str, Detection] = {}
        for det in detections:
            prof = self._profiles.for_label(det.label)
            if prof is None:
                continue
            need = prof.min_confidence if prof.min_confidence is not None else self._min_conf
            if det.confidence < need or not self._is_on_desk(det, width, height):
                continue
            if prof.id not in best or det.confidence > best[prof.id].confidence:
                best[prof.id] = det
        return best

    # ── the consumer ──
    def on_detection(self, event: DetectionEvent) -> None:
        now = event.ts
        width, height = event.size
        best = self._best_per_agent(event.detections, width, height)
        joined, left = [], []
        with self._lock:
            appeared, gone = self._presence.update(best.keys(), now)
            for pid, det in best.items():
                if pid in self._members:
                    self._members[pid]['det'] = det
                    self._members[pid]['last_seen'] = now
            for pid in appeared:
                if len(self._members) >= self._max_agents:
                    self._presence.drop(pid)          # full: try again on its next sighting
                    continue
                det = best[pid]
                self._members[pid] = {'joined_at': now, 'label': det.label, 'det': det, 'last_seen': now}
                self._history.setdefault(pid, {})['joined_at'] = now
                joined.append((pid, det))
            for pid in gone:
                member = self._members.pop(pid, None)
                if member is not None:
                    self._history.setdefault(pid, {})['left_at'] = now
                    left.append((pid, member))
            objects = {pid: _desk_object(m['det']) for pid, m in self._members.items()
                       if now - m['last_seen'] <= self._fresh_sec}
        with self._desk.lock:
            self._desk.objects = objects
            for pid in objects:
                self._desk.object_first_seen.setdefault(pid, now)
                self._desk.object_last_seen[pid] = now
        for pid, det in joined:
            name = self._name(pid)
            log.info('%s joined the chat (%s)', name, det.label)
            self._bus.publish(AgentJoined(ts=now, agent=pid, name=name, label=det.label, conf=det.confidence))
        for pid, member in left:
            name = self._name(pid)
            log.info('%s left the chat', name)
            self._bus.publish(AgentLeft(ts=now, agent=pid, name=name, label=member['label'],
                                        stayed_sec=round(now - member['joined_at'], 1)))

    def _name(self, pid: str) -> str:
        prof = self._profiles.get(pid)
        return prof.name if prof else pid

    # ── for the app ──
    def member_ids(self) -> list[str]:
        with self._lock:
            return list(self._members)

    def is_member(self, agent_id: str) -> bool:
        with self._lock:
            return agent_id in self._members

    def snapshot(self) -> list[dict]:
        """Agents in the chat now, plus ones that were here earlier (for the chat list), newest first."""
        with self._lock:
            members = {k: dict(v) for k, v in self._members.items()}
            history = {k: dict(v) for k, v in self._history.items()}

        def joined_at(pid: str) -> float:
            return (members.get(pid) or history.get(pid) or {}).get('joined_at') or 0

        out = []
        for pid in sorted(set(members) | set(history), key=lambda k: -joined_at(k)):
            prof = self._profiles.get(pid)
            if not prof:
                continue
            d = prof.public()
            d['on_desk'] = pid in members
            d['joined_at'] = (members.get(pid) or history.get(pid) or {}).get('joined_at')
            d['left_at'] = None if pid in members else history.get(pid, {}).get('left_at')
            if pid in members:
                d['label_seen'] = members[pid]['label']
                d['confidence'] = members[pid]['det'].confidence
            out.append(d)
        return out


def _desk_object(det: Detection) -> dict:
    """An agent's box the way DeskState.objects has always held it."""
    d = det.to_dict()
    return {'class_name': d['label'], 'bbox': d['bbox'], 'center': d['center'], 'conf': d['conf'], 'cam': 0}
