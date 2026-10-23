"""
Object states: is each kind of object on the desk right now? Steady answers instead of flickering pictures.

    smoothing.py   Hysteresis and MajorityWindow: confidence numbers become a steady yes / no and a position
    machine.py     step(): one object's UNKNOWN / PRESENT / ABSENT state, and what changed (appeared, removed, moved)
"""

from desktalk.core.object_states.machine import ObjectState, Rules, StepInput, Transition, step
from desktalk.core.object_states.smoothing import Hysteresis, MajorityWindow

__all__ = ['Hysteresis', 'MajorityWindow', 'ObjectState', 'Rules', 'StepInput', 'Transition', 'step']
