"""
The roster: who is in the group chat right now, and what is on the desk.

It listens to DetectionEvents (about 3 a second) and keeps two lists.

The chat. Only agents on settings → agents.can_join may join. The Settings page's sensitivity slider
(Preferences 'sensitivity', read again with every picture so a change works at once) picks one of
agents.sensitivity_steps: how sure the detector must be (or the agent's own min_confidence in agents.yaml, when
that is stricter) and how long the object must be seen. Then its agent joins ("Cup joined the chat"); when it
has been missing for agents.leave_after_sec, it leaves ("Cup left the chat"). Both go back on the event bus as
AgentJoined / AgentLeft. Something seen long enough that may not join is "refused", once per stretch of
sightings. Every join, leave and refusal is kept, with why, for the developer page's trace (ChatJoins). No
pictures (camera off) means no events, so nobody leaves.

On the desk. Everything the detector names (any agent, also one made up for an object without a block in
agents.yaml, but never an ignored word), seen for agents.on_desk_after_sec at agents.on_desk_min_confidence or
more. The slider doesn't change these. The danger warnings and the live view read this list.

Both are written to the desk scene with what was in a picture just now (presence.disappear_after_sec):
DeskScene.set_members for the chat, DeskScene.set_on_desk for the desk. Once desk corners are marked (and
agents.only_on_desk is on), things off the desk count for neither.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from collections.abc import Callable, Iterable
from typing import Any

from desktalk.config import AgentsSettings, PresenceSettings, SensitivityStep
from desktalk.core.agents.presence import PresenceTracker
from desktalk.domain import AgentJoined, AgentLeft, ChatJoin, Detection, DetectionEvent, SeenObject
from desktalk.ports import DeskScene, EventBus, Preferences, Profiles

log = logging.getLogger(__name__)

# (x, y, picture width, picture height) → is this point on the desk? None when no desk outline is marked (then
# everything counts as on the desk).
OnDesk = Callable[[float, float, int, int], bool | None]


class _Sighting:
    """The best box for one agent in one picture, and whether it was inside the marked desk."""

    __slots__ = ('det', 'inside')

    def __init__(self, det: Detection, inside: bool | None):
        self.det = det
        self.inside = inside


class Roster:
    """Detection events → AgentJoined / AgentLeft. Implements RosterView, RosterList and ChatJoins."""

    def __init__(self, bus: EventBus, profiles: Profiles, scene: DeskScene, agents: AgentsSettings,
                 presence: PresenceSettings, prefs: Preferences, *, on_desk: OnDesk | None = None,
                 joins_kept: int):
        self._bus = bus
        self._profiles = profiles
        self._scene = scene
        self._prefs = prefs
        self._agents = agents
        self._can_join = set(agents.can_join)
        self._on_desk = on_desk
        step = agents.default_step()
        self._presence = PresenceTracker(step.join_after_sec, agents.leave_after_sec)     # the chat
        self._desk = PresenceTracker(agents.on_desk_after_sec, presence.disappear_after_sec)   # on the desk
        self._fresh_sec = presence.disappear_after_sec       # "in a picture just now" for the scene
        self._lock = threading.Lock()                         # snapshot() is read from the web server's thread
        self._members: dict[str, dict[str, Any]] = {}  # id → {'joined_at', 'label', 'det', 'last_seen', 'inside'}
        self._history: dict[str, dict] = {}      # id → {'joined_at', 'left_at'} for agents seen before
        self._stretch_start: dict[str, float] = {}           # id → when its current stretch of sightings began
        self._desk_last: dict[str, tuple[float, Detection]] = {}    # id → its newest sighting on the desk
        self._joins: dict[str, deque[ChatJoin]] = {}         # detector word → its newest joins, leaves, refusals
        self._joins_kept = joins_kept
        self._bad_steps: set[str] = set()                    # bad slider values already logged
        bus.subscribe('roster', self.on_detection, types=[DetectionEvent])

    # ── helpers ──
    def _step(self) -> SensitivityStep:
        """The slider's step; agents.sensitivity_default when the saved value is missing or not a step."""
        steps = self._agents.sensitivity_steps
        value = self._prefs.get('sensitivity')
        if value is None:
            return self._agents.default_step()
        if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < len(steps):
            return steps[value]
        if repr(value) not in self._bad_steps:
            self._bad_steps.add(repr(value))
            log.warning('The sensitivity preference %r is not one of the %d steps: using %s', value, len(steps),
                        self._agents.default_step().name)
        return self._agents.default_step()

    def _inside(self, det: Detection, width: int, height: int) -> bool | None:
        """The desk outline's answer for where the object touches the desk (None: no outline, or no size)."""
        if self._on_desk is None or not width:
            return None
        cx, _ = det.bbox.center
        return self._on_desk(cx, det.bbox.y2, width, height)

    def _sightings(self, detections: Iterable[Detection], width: int, height: int,
                   step: SensitivityStep) -> tuple[dict[str, _Sighting], dict[str, Detection]]:
        """The best box per agent sure enough for the chat (by the step), and per agent sure enough for the desk."""
        chat: dict[str, _Sighting] = {}
        desk: dict[str, Detection] = {}
        for det in detections:
            prof = self._profiles.for_label(det.label)
            if prof is None:
                continue
            inside = self._inside(det, width, height)
            if self._agents.only_on_desk and inside is False:
                continue
            sure_for_desk = det.confidence >= self._agents.on_desk_min_confidence
            if sure_for_desk and (prof.id not in desk or det.confidence > desk[prof.id].confidence):
                desk[prof.id] = det
            need = max(step.min_confidence, prof.min_confidence or 0)
            if det.confidence >= need and (prof.id not in chat or det.confidence > chat[prof.id].det.confidence):
                chat[prof.id] = _Sighting(det, inside)
        return chat, desk

    def _keep(self, join: ChatJoin) -> None:
        kept = self._joins.setdefault(join.label, deque(maxlen=self._joins_kept))
        kept.append(join)

    def _on_the_desk(self, desk: dict[str, Detection], now: float) -> dict[str, SeenObject]:
        _, gone = self._desk.update(desk.keys(), now)
        for pid in gone:
            self._desk_last.pop(pid, None)
        for pid, det in desk.items():
            if self._desk.is_present(pid):
                self._desk_last[pid] = (now, det)
        return {pid: SeenObject.from_detection(det) for pid, (seen_at, det) in self._desk_last.items()
                if now - seen_at <= self._fresh_sec}

    # ── the consumer ──
    def on_detection(self, event: DetectionEvent) -> None:
        now = event.ts
        width, height = event.size
        step = self._step()
        chat, desk = self._sightings(event.detections, width, height, step)
        joined, left = [], []
        with self._lock:
            self._presence.appear_sec = float(step.join_after_sec)       # the slider works at once
            for pid in chat:
                last = self._presence.last_seen(pid)
                if last is None or now - last > self._presence.gone_sec:
                    self._stretch_start[pid] = now
            appeared, gone = self._presence.update(chat.keys(), now)
            for pid, seen in chat.items():
                if pid in self._members:
                    self._members[pid].update(det=seen.det, last_seen=now, inside=seen.inside)
            for pid in appeared:
                seen = chat[pid]
                seen_for = round(now - self._stretch_start.get(pid, now), 3)
                if pid not in self._can_join:
                    self._keep(ChatJoin(agent=pid, label=seen.det.label, kind='refused', ts=now,
                                        confidence=seen.det.confidence, seen_for_sec=seen_for, on_desk=seen.inside,
                                        step=step.name))
                    continue                          # stays "present", so it's refused once per stretch
                if len(self._members) >= self._agents.max_agents:
                    self._presence.drop(pid)          # full: try again on its next sighting
                    continue
                self._members[pid] = {'joined_at': now, 'label': seen.det.label, 'det': seen.det, 'last_seen': now,
                                      'inside': seen.inside}
                self._history.setdefault(pid, {})['joined_at'] = now
                self._keep(ChatJoin(agent=pid, label=seen.det.label, kind='joined', ts=now,
                                    confidence=seen.det.confidence, seen_for_sec=seen_for, on_desk=seen.inside,
                                    step=step.name))
                joined.append((pid, seen.det))
            for pid in gone:
                start = self._stretch_start.pop(pid, None)
                member = self._members.pop(pid, None)
                if member is None:
                    continue
                if start is None:
                    start = member['joined_at']
                self._history.setdefault(pid, {})['left_at'] = now
                self._keep(ChatJoin(agent=pid, label=member['label'], kind='left', ts=now,
                                    confidence=member['det'].confidence,
                                    seen_for_sec=round(member['last_seen'] - start, 3),
                                    on_desk=member['inside'], step=step.name))
                left.append((pid, member))
            members = {pid: SeenObject.from_detection(m['det']) for pid, m in self._members.items()
                       if now - m['last_seen'] <= self._fresh_sec}
            on_desk = self._on_the_desk(desk, now)
        self._scene.set_members(members, now)
        self._scene.set_on_desk(on_desk, now)
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

    def joins(self, label: str) -> list[ChatJoin]:
        """The newest joins, leaves and refusals for a detector word (settings → dev.trace_events_kept), oldest
        first."""
        with self._lock:
            return list(self._joins.get(label, ()))

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
