"""
The tracker: detection events in, a steady state for each kind of object out.

It listens to DetectionEvents (about 3 a second). For each kind of object it picks one box per picture,
smooths the answer (Hysteresis, then MajorityWindow) and runs `step()`. When something really changes
(`appeared`, `removed`, `moved`) it publishes an ObjectStateChanged on the event bus first, then writes the
change to the ObjectStateStore, so a database problem can't hide the event from the app.

The person goes through the same machine with its own waits, and movement in the picture can count as
"seen" (the camera often only sees your hands). That makes the person's state the on/off switch.

It also answers the rule engine (ObjectStatesView): `snapshot()` is the state of every object seen so far,
and `changes(since)` reads the store, so changes from before a restart are still there.
`save_snapshot(now)` and `restore(now)` let a restart pick up where the last run left off.
"""

from __future__ import annotations

import logging
import math
import threading
from datetime import datetime

from desktalk.config import ObjectStatesSettings
from desktalk.core.object_states.machine import ObjectState, Rules, StepInput, Transition, step
from desktalk.core.object_states.smoothing import Hysteresis, MajorityWindow
from desktalk.domain import (
    DeskSnapshot,
    Detection,
    DetectionEvent,
    ObjectSnapshot,
    ObjectStateChanged,
    Phase,
    StateChange,
    StateRecord,
)
from desktalk.ports import EventBus, ObjectStateStore
from desktalk.runtime.errors import plain_error

log = logging.getLogger(__name__)

Center = tuple[float, float]


def _timezone(ts: float) -> str:
    """The desk's UTC offset at that moment, e.g. '+08:00'."""
    return datetime.fromtimestamp(ts).astimezone().isoformat()[-6:]


class ObjectStateTracker:
    """Detection events → object states and ObjectStateChanged events. Implements ObjectStatesView."""

    def __init__(self, settings: ObjectStatesSettings, bus: EventBus, store: ObjectStateStore, *,
                 device_id: str, new_session_after_sec: float):
        self._bus = bus
        self._store = store
        self._device_id = device_id
        self._new_session_after_sec = new_session_after_sec    # a gap this long starts a new run of watching
        self._on_conf = settings.on_confidence
        self._off_conf = settings.off_confidence
        self._window_pictures = settings.window_pictures
        self._vote_share = settings.vote_share
        self._no_move_labels = set(settings.no_move_labels)
        self._person_label = settings.person_label
        self._person_counts_movement = settings.person_counts_movement
        self._restore_max_age_sec = settings.restore_max_age_sec
        self._object_rules = (settings.appear_sec, settings.gone_sec, settings.move_fraction)
        self._person_rules = (settings.person_appear_sec, settings.person_gone_sec, settings.move_fraction)

        # One lock for the states, the smoothing and watched_since: pictures come in on the event bus
        # thread, snapshot() and changes() are read from the rule engine's and reminders' threads, and
        # save_snapshot() runs on the scheduler's.
        self._lock = threading.Lock()
        self._states: dict[str, ObjectState] = {}
        self._hysteresis: dict[str, Hysteresis] = {}
        self._windows: dict[str, MajorityWindow] = {}
        self._watched_since: float | None = None
        self._last_picture: float | None = None
        self._store_errors_in_row = 0
        bus.subscribe('object-states', self.on_detection, types={DetectionEvent})

    # ── helpers ──
    def _rules(self, label: str) -> Rules:
        appear, gone, move = self._person_rules if label == self._person_label else self._object_rules
        return Rules(appear, gone, move, track_moves=label not in self._no_move_labels)

    def _smoothing(self, label: str) -> tuple[Hysteresis, MajorityWindow]:
        if label not in self._hysteresis:
            self._hysteresis[label] = Hysteresis(self._on_conf, self._off_conf)
            self._windows[label] = MajorityWindow(self._window_pictures, self._vote_share)
        return self._hysteresis[label], self._windows[label]

    @staticmethod
    def _share(det: Detection, width: int, height: int) -> Center | None:
        """The box's center as a share of the picture (0 to 1 across and down)."""
        if not width or not height:
            return None
        cx, cy = det.bbox.center
        return cx / width, cy / height

    def _pick(self, boxes: list[Detection], anchor: Center | None, width: int, height: int) -> Detection | None:
        """One box per kind of object: the one nearest where it was, else the most confident."""
        if not boxes:
            return None
        if anchor is not None:
            placed = [(det, self._share(det, width, height)) for det in boxes]
            placed = [(det, c) for det, c in placed if c is not None]
            if placed:
                return min(placed, key=lambda p: math.dist(anchor, p[1]))[0]
        return max(boxes, key=lambda det: det.confidence)

    def _record(self, t: Transition) -> StateRecord:
        return StateRecord(ts=t.ts, timezone=_timezone(t.ts), device_id=self._device_id, type=t.type,
                           label=t.label, prev_state=t.prev_state, new_state=t.new_state, conf_avg=t.conf_avg,
                           bbox=t.bbox, dwell_sec=t.dwell_sec)

    def _event(self, t: Transition) -> ObjectStateChanged:
        return ObjectStateChanged(ts=t.ts, change=t.type, label=t.label, prev_state=t.prev_state.value,
                                  new_state=t.new_state.value, device_id=self._device_id, conf_avg=t.conf_avg,
                                  bbox=t.bbox, dwell_sec=t.dwell_sec)

    # ── the consumer ──
    def on_detection(self, event: DetectionEvent) -> None:
        ts = event.ts
        width, height = event.size
        boxes: dict[str, list[Detection]] = {}
        for det in event.detections:
            boxes.setdefault(det.label, []).append(det)
        movement_is_person = self._person_counts_movement and event.moved

        transitions: list[Transition] = []
        with self._lock:
            if self._last_picture is None or ts - self._last_picture >= self._new_session_after_sec:
                self._watched_since = ts
            self._last_picture = ts

            # Every kind we know of: the ones tracked so far, plus the ones in this picture.
            labels = dict.fromkeys([*self._states, *boxes])
            if movement_is_person:
                labels[self._person_label] = None
            for label in labels:
                state = self._states.get(label) or ObjectState(label)
                det = self._pick(boxes.get(label, []), state.anchor, width, height)
                hysteresis, window = self._smoothing(label)
                box_seen = hysteresis.update(det.confidence if det is not None else None)
                seen = box_seen or (label == self._person_label and movement_is_person)
                center = self._share(det, width, height) if det is not None else None
                majority, middle, _ = window.add(seen, center, det.confidence if det is not None else None)
                # This picture's own confidence and box, only when the box itself counted as seen.
                own = det if (det is not None and box_seen) else None
                inp = StepInput(ts=ts, seen=majority, center=middle,
                                conf=own.confidence if own is not None else None,
                                bbox=tuple(own.bbox.to_list()) if own is not None else None)
                self._states[label], fired = step(state, inp, self._rules(label))
                transitions.extend(fired)

        if not transitions:
            return
        # The bus first, so a database problem can't hide the change from the app.
        for t in transitions:
            self._bus.publish(self._event(t))
        try:
            self._store.append([self._record(t) for t in transitions])
        except Exception as exc:  # a full disk or a locked file must never stop the tracker
            self._store_errors_in_row += 1
            # The full traceback only when it starts failing, so a broken database doesn't flood the log.
            log.error('could not save %d object state change(s): %s', len(transitions), plain_error(exc),
                      exc_info=self._store_errors_in_row == 1)
        else:
            self._store_errors_in_row = 0

    # ── ObjectStatesView ──
    def snapshot(self) -> DeskSnapshot:
        with self._lock:
            objects = {label: ObjectSnapshot(label, s.phase.value, s.since) for label, s in self._states.items()}
            return DeskSnapshot(objects=objects, watched_since=self._watched_since)

    def changes(self, since: float) -> list[StateChange]:
        return [StateChange(r.ts, r.label, change=r.type) for r in self._store.changes(since)]

    # ── saving and restarting ──
    def save_snapshot(self, now: float) -> int:
        """Write one `snapshot` row per object that isn't UNKNOWN. Returns how many."""
        with self._lock:
            records = [
                StateRecord(ts=now, timezone=_timezone(now), device_id=self._device_id, type='snapshot',
                            label=label, prev_state=s.phase, new_state=s.phase,
                            dwell_sec=now - s.since if s.since is not None else None)
                for label, s in self._states.items() if s.phase != Phase.UNKNOWN
            ]
        if records:
            self._store.append(records)
        return len(records)

    def restore(self, now: float) -> int:
        """Start each object in its newest saved state, if that's recent enough. Returns how many."""
        latest = self._store.latest_states(now - self._restore_max_age_sec)
        restored = 0
        with self._lock:
            for label, row in latest.items():
                if row.new_state not in (Phase.PRESENT, Phase.ABSENT):
                    continue
                since = row.ts - row.dwell_sec if row.type == 'snapshot' and row.dwell_sec is not None else row.ts
                # No spot yet: the first picture that shows it sets one, quietly. The smoothing starts empty.
                self._states[label] = ObjectState(label, Phase(row.new_state), since=since)
                self._hysteresis.pop(label, None)
                self._windows.pop(label, None)
                restored += 1
        return restored
