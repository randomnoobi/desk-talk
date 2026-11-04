"""
The tracker: detection events in, a steady state for each kind of object out.

It listens to DetectionEvents (about 3 a second). For each kind of object it picks one box per picture,
smooths the answer (Hysteresis, then MajorityWindow) and runs `step()`. When something really changes
(`appeared`, `removed`, `moved`) it publishes an ObjectStateChanged on the event bus first, then writes the
change to the ObjectStateStore, so a database problem can't hide the event from the app. Changes the store
couldn't take are kept in memory and saved with the next ones, so the rule engine still sees them.

The Settings page's sensitivity slider (Preferences 'sensitivity', read with every picture so a change works at
once) stretches the waits: an object's appear_sec, gone_sec and move_fraction are multiplied by the step's
`steady` (settings → agents.sensitivity_steps), so a pickier step lets less flicker through. A new thing (an
object without its own block in agents.yaml) waits agents.new_things.extra_wait_sec longer to appear.

The person goes through the same machine with its own waits (the slider doesn't change them), and movement in
the picture can count as "seen" (the camera often only sees your hands). That makes the person's state the
on/off switch.

It also answers the rule engine (ObjectStatesView): `snapshot()` is the state of every object seen so far,
and `changes(since)` reads the store (plus any changes not saved yet), so changes from before a restart are still
there.
It also keeps each object's last pictures of smoothing for the developer page (SmoothingTraceView):
`traced_labels()` and `trace(label)`, up to `trace_pictures` per object.
`save_snapshot(now)` and `restore(now)` let a restart pick up where the last run left off.
"""

from __future__ import annotations

import logging
import math
import threading
from collections import deque

from desktalk.config import AgentsSettings, ObjectStatesSettings, SensitivityStep
from desktalk.core.agents.sensitivity import Slider
from desktalk.core.object_states.machine import ObjectState, Rules, StepInput, Transition
from desktalk.core.object_states.machine import step as next_state
from desktalk.core.object_states.smoothing import Hysteresis, MajorityWindow
from desktalk.domain import (
    DeskSnapshot,
    Detection,
    DetectionEvent,
    Event,
    ObjectSnapshot,
    ObjectStateChanged,
    Phase,
    SmoothingStep,
    StateChange,
    StateRecord,
    plain_error,
)
from desktalk.ports import Clock, EventBus, ObjectStateStore, Preferences, Profiles

log = logging.getLogger(__name__)

Center = tuple[float, float]


def _timezone(utc_offset_sec: float) -> str:
    """A UTC offset in seconds as text, e.g. 28800 → '+08:00'."""
    sign = '-' if utc_offset_sec < 0 else '+'
    minutes = round(abs(utc_offset_sec) / 60)
    return f'{sign}{minutes // 60:02d}:{minutes % 60:02d}'


def _rounded(det: Detection) -> tuple[float, float, float, float]:
    """A box as (x1, y1, x2, y2), kept to 0.1 px like Detection.to_dict() and the rows saved before."""
    x1, y1, x2, y2 = det.bbox.to_list()
    return x1, y1, x2, y2


def _value(phase: Phase | None) -> str | None:
    return phase.value if phase is not None else None


def _since(start: float | None, ts: float) -> float | None:
    """How long since `start`, or None when there is no start."""
    return ts - start if start is not None else None


class ObjectStateTracker:
    """Detection events → object states and ObjectStateChanged events. Implements ObjectStatesView and
    SmoothingTraceView."""

    def __init__(self, settings: ObjectStatesSettings, bus: EventBus, store: ObjectStateStore, *,
                 device_id: str, new_session_after_sec: float, clock: Clock, agents: AgentsSettings,
                 prefs: Preferences, profiles: Profiles):
        """`clock`: only for the desk's UTC offset written with each row. `agents` and `prefs`: the sensitivity
        slider's steps and the one picked; `profiles`: which words are new things."""
        self._bus = bus
        self._settings = settings
        self._slider = Slider(agents, prefs)
        self._new_wait_sec = agents.new_things.extra_wait_sec
        self._profiles = profiles
        self._clock = clock
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
        self._trace_pictures = settings.trace_pictures
        self._person_rules = Rules(settings.person_appear_sec, settings.person_gone_sec, settings.move_fraction,
                                   track_moves=self._person_label not in self._no_move_labels)
        self._is_new: dict[str, bool] = {}              # each word: a new thing? Looked up once, on first use

        # One lock for the states, the smoothing, the trace and watched_since: pictures come in on the event bus
        # thread, snapshot() and changes() are read from the rule engine's and reminders' threads, and
        # save_snapshot() runs on the scheduler's.
        self._lock = threading.Lock()
        self._states: dict[str, ObjectState] = {}
        self._hysteresis: dict[str, Hysteresis] = {}
        self._windows: dict[str, MajorityWindow] = {}
        self._trace: dict[str, deque[SmoothingStep]] = {}   # each object's last pictures, oldest first
        self._watched_since: float | None = None
        self._last_picture: float | None = None
        self._store_errors_in_row = 0
        self._unsaved: list[StateRecord] = []           # changes the store couldn't take yet, oldest first
        bus.subscribe('object-states', self._on_event, types={DetectionEvent})

    # ── helpers ──
    def _rules(self, label: str, step: SensitivityStep) -> Rules:
        """The waits for one kind of object at the slider's step (the person's never change)."""
        if label == self._person_label:
            return self._person_rules
        appear, gone, move = self._settings.steady_rules(step.steady)
        if self._new(label):
            appear += self._new_wait_sec
        return Rules(appear, gone, move, track_moves=label not in self._no_move_labels)

    def _new(self, label: str) -> bool:
        """Is this word a new thing (its agent made up, with no block of its own in agents.yaml)?"""
        if label not in self._is_new:
            profile = self._profiles.for_label(label)
            self._is_new[label] = profile is not None and profile.auto
        return self._is_new[label]

    def _smoothing(self, label: str) -> tuple[Hysteresis, MajorityWindow]:
        if label not in self._hysteresis:
            self._hysteresis[label] = Hysteresis(self._on_conf, self._off_conf)
            self._windows[label] = MajorityWindow(self._window_pictures, self._vote_share)
        return self._hysteresis[label], self._windows[label]

    def _add_to_trace(self, label: str, smoothing_step: SmoothingStep) -> None:
        """Call with the lock held."""
        if label not in self._trace:
            self._trace[label] = deque(maxlen=self._trace_pictures)
        self._trace[label].append(smoothing_step)

    @staticmethod
    def _share(det: Detection, width: int, height: int) -> Center | None:
        """The box's center as a share of the picture (0 to 1 across and down)."""
        if not width or not height:
            return None
        cx, cy = det.bbox.center
        return cx / width, cy / height

    def _pick(self, boxes: list[Detection], anchor: Center | None, width: int, height: int) -> Detection | None:
        """One box per kind of object: the one nearest where it was, else the most confident.

        Only boxes at least `off_confidence` sure count for "nearest", so a faint leftover box at the old spot
        can't hide the real object somewhere else."""
        if not boxes:
            return None
        if anchor is not None:
            placed = [(det, self._share(det, width, height)) for det in boxes if det.confidence >= self._off_conf]
            placed = [(det, c) for det, c in placed if c is not None]
            if placed:
                return min(placed, key=lambda p: math.dist(anchor, p[1]))[0]
        return max(boxes, key=lambda det: det.confidence)

    def _zone(self, ts: float) -> str:
        return _timezone(self._clock.utc_offset_sec(ts))

    def _record(self, t: Transition) -> StateRecord:
        return StateRecord(ts=t.ts, timezone=self._zone(t.ts), device_id=self._device_id, type=t.type,
                           label=t.label, prev_state=t.prev_state, new_state=t.new_state, conf_avg=t.conf_avg,
                           bbox=t.bbox, dwell_sec=t.dwell_sec)

    def _event(self, t: Transition) -> ObjectStateChanged:
        return ObjectStateChanged(ts=t.ts, change=t.type, label=t.label, prev_state=t.prev_state.value,
                                  new_state=t.new_state.value, device_id=self._device_id, conf_avg=t.conf_avg,
                                  bbox=t.bbox, dwell_sec=t.dwell_sec)

    # ── the consumer ──
    def _on_event(self, event: Event) -> None:
        if isinstance(event, DetectionEvent):
            self.on_detection(event)

    def on_detection(self, event: DetectionEvent) -> None:
        ts = event.ts
        width, height = event.size
        boxes: dict[str, list[Detection]] = {}
        for det in event.detections:
            boxes.setdefault(det.label, []).append(det)
        movement_is_person = self._person_counts_movement and event.moved
        step = self._slider.step()                      # the slider works at once

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
                                bbox=_rounded(own) if own is not None else None)
                self._states[label], fired = next_state(state, inp, self._rules(label, step))
                transitions.extend(fired)
                yes, total = window.votes()
                self._add_to_trace(label, SmoothingStep(
                    ts=ts, conf=det.confidence if det is not None else None, box_seen=box_seen,
                    votes_yes=yes, votes_total=total, seen=majority, center=middle,
                    phase=self._states[label].phase.value, heading=_value(self._states[label].heading),
                    waiting_sec=_since(self._states[label].pending_since, ts)))

        if not transitions:
            return
        # The bus first, so a database problem can't hide the change from the app.
        for t in transitions:
            self._bus.publish(self._event(t))
        # Changes the store couldn't take before go first, so the rows stay oldest first.
        with self._lock:
            self._unsaved.extend(self._record(t) for t in transitions)
            records = list(self._unsaved)
        try:
            self._store.append(records)
        except Exception as exc:  # a full disk or a locked file must never stop the tracker
            self._store_errors_in_row += 1
            # The full traceback only when it starts failing, so a broken database doesn't flood the log.
            log.error('could not save %d object state change(s): %s', len(records), plain_error(exc),
                      exc_info=self._store_errors_in_row == 1)
        else:
            self._store_errors_in_row = 0
            with self._lock:
                del self._unsaved[:len(records)]

    # ── ObjectStatesView ──
    def snapshot(self) -> DeskSnapshot:
        with self._lock:
            objects = {label: ObjectSnapshot(label, s.phase.value, s.since) for label, s in self._states.items()}
            return DeskSnapshot(objects=objects, watched_since=self._watched_since)

    def changes(self, since: float) -> list[StateChange]:
        """The store's changes after `since`, plus the ones it couldn't save yet, oldest first."""
        with self._lock:
            unsaved = [r for r in self._unsaved if r.ts > since]
        saved = [StateChange(r.ts, r.label, change=r.type) for r in self._store.changes(since)]
        # A change saved between the two reads is in both lists: count it once.
        known = set(saved)
        extra = [c for c in (StateChange(r.ts, r.label, change=r.type) for r in unsaved) if c not in known]
        return sorted(saved + extra, key=lambda c: c.ts)

    # ── SmoothingTraceView ──
    def traced_labels(self) -> list[str]:
        with self._lock:
            return list(self._trace)

    def trace(self, label: str) -> list[SmoothingStep]:
        """The object's last pictures of smoothing, oldest first; [] for a label never seen."""
        with self._lock:
            return list(self._trace.get(label, ()))

    # ── saving and restarting ──
    def save_snapshot(self, now: float) -> int:
        """Write one `snapshot` row per object that is PRESENT. Returns how many.

        ABSENT objects are left out: their `removed` row already says so, and writing them every minute would
        only grow the table with every label ever seen."""
        with self._lock:
            records = [
                StateRecord(ts=now, timezone=self._zone(now), device_id=self._device_id, type='snapshot',
                            label=label, prev_state=s.phase, new_state=s.phase,
                            dwell_sec=now - s.since if s.since is not None else None)
                for label, s in self._states.items() if s.phase == Phase.PRESENT
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
