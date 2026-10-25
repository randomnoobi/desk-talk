"""
The state machine for one object on the desk: UNKNOWN, PRESENT or ABSENT.

`step()` is a pure function. It gets the object's state and one picture's smoothed answer and returns the
new state plus anything that happened (`appeared`, `removed`, `moved`). It never reads the clock: every
time comes from the picture, so it is easy to test with made-up times.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

from desktalk.domain.object_states import Phase

Center = tuple[float, float]
Box = tuple[float, float, float, float]


@dataclass(frozen=True)
class Rules:
    """The numbers for one kind of object. The caller gets them from the settings."""

    appear_sec: float       # seen for more than this long before it counts as there
    gone_sec: float         # missing for more than this long before it counts as gone
    move_fraction: float    # the center must move more than this share of the picture to count as moved
    track_moves: bool       # False for things that move all the time (like a person)


@dataclass(frozen=True)
class StepInput:
    """One picture. `seen` and `center` are smoothed; `conf` and `bbox` are this picture's own."""

    ts: float
    seen: bool
    center: Center | None
    conf: float | None
    bbox: Box | None


@dataclass(frozen=True)
class ObjectState:
    label: str
    phase: Phase = Phase.UNKNOWN
    since: float | None = None            # when it entered this phase
    pending_since: float | None = None    # when the answer first disagreed with the phase
    heading: Phase | None = None          # the phase that waiting is heading to
    anchor: Center | None = None          # where it was when it last counted as placed
    anchor_since: float | None = None     # when it got to the anchor
    conf_sum: float = 0.0                 # confidence of the seen pictures since the last change
    conf_n: int = 0
    last_bbox: Box | None = None          # the last box seen


@dataclass(frozen=True)
class Transition:
    type: str                             # 'appeared', 'removed' or 'moved'
    label: str
    prev_state: Phase
    new_state: Phase
    ts: float
    conf_avg: float | None                # average confidence of the seen pictures since the last change
    bbox: Box | None                      # the last box seen
    dwell_sec: float | None               # time in the old state (for 'moved': time at the old spot)


def step(state: ObjectState, inp: StepInput, rules: Rules) -> tuple[ObjectState, list[Transition]]:
    """Take one picture into account. Returns the new state and the transitions it caused."""
    transitions: list[Transition] = []

    # 1. Remember what a seen picture showed.
    if inp.seen:
        if inp.conf is not None:
            state = replace(state, conf_sum=state.conf_sum + inp.conf, conf_n=state.conf_n + 1)
        if inp.bbox is not None:
            state = replace(state, last_bbox=inp.bbox)

    # 2. Does the answer agree with the phase? One picture that agrees resets the wait.
    heading_to = Phase.PRESENT if inp.seen else Phase.ABSENT
    if state.phase == heading_to:
        state = replace(state, pending_since=None, heading=None)
    elif state.pending_since is None or state.heading != heading_to:
        # A new wait starts, also when the answer flips to the other way (UNKNOWN agrees with neither).
        state = replace(state, pending_since=inp.ts, heading=heading_to)

    # 3. Disagreed for long enough: switch.
    if state.pending_since is not None:
        wait = rules.appear_sec if heading_to == Phase.PRESENT else rules.gone_sec
        if inp.ts - state.pending_since > wait:
            state, fired = _switch(state, heading_to, inp)
            transitions.extend(fired)
            return state, transitions

    # 4. Still PRESENT: has it moved?
    if rules.track_moves and state.phase == Phase.PRESENT and inp.seen and inp.center is not None:
        if state.anchor is None:
            # Just restored from a saved state: take this spot as the starting place, quietly.
            state = replace(state, anchor=inp.center, anchor_since=inp.ts)
        elif math.dist(state.anchor, inp.center) > rules.move_fraction:
            transitions.append(
                Transition(
                    'moved', state.label, Phase.PRESENT, Phase.PRESENT, inp.ts,
                    _average(state), state.last_bbox, _since(state.anchor_since, inp.ts),
                )
            )
            state = replace(state, anchor=inp.center, anchor_since=inp.ts)
    return state, transitions


def _switch(state: ObjectState, new_phase: Phase, inp: StepInput) -> tuple[ObjectState, list[Transition]]:
    """Move to the new phase. Only arriving and leaving fire a transition (UNKNOWN to ABSENT is quiet)."""
    fired: list[Transition] = []
    if new_phase == Phase.PRESENT:
        kind = 'appeared'
    elif state.phase == Phase.PRESENT:
        kind = 'removed'
    else:
        kind = None
    if kind is not None:
        fired.append(
            Transition(
                kind, state.label, state.phase, new_phase, inp.ts,
                _average(state), state.last_bbox, _since(state.since, inp.ts),
            )
        )
    anchor = inp.center if new_phase == Phase.PRESENT else None
    anchor_since = inp.ts if new_phase == Phase.PRESENT else None
    new_state = replace(
        state, phase=new_phase, since=inp.ts, pending_since=None, heading=None,
        anchor=anchor, anchor_since=anchor_since, conf_sum=0.0, conf_n=0,
    )
    return new_state, fired


def _average(state: ObjectState) -> float | None:
    return state.conf_sum / state.conf_n if state.conf_n else None


def _since(start: float | None, ts: float) -> float | None:
    return None if start is None else ts - start
