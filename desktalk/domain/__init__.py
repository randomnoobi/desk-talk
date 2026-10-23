"""
Plain data, no I/O: what the parts of Desk Talk pass to each other.

    Detection, BBox        one thing YOLO found (shared with the Pi: desktalk_shared.detection)
    Frame                  a picture from a camera, with what came with it
    Event and subclasses   what happened (DetectionEvent, AgentJoined, Alert, HabitEvent ...)
    AgentProfile, Nudge    a character, a habit reminder
    ObjectSnapshot, DeskSnapshot, StateChange, RuleHistory
                           what the rule engine reads (where things are, what changed, what nudged)
"""

from desktalk.domain.agents import HABITS, AgentProfile
from desktalk.domain.camera import Frame
from desktalk.domain.errors import AIUnavailable, DetectorUnavailable
from desktalk.domain.events import (
    EVENT_TYPES,
    AgentJoined,
    AgentLeft,
    Alert,
    CameraNotice,
    Compaction,
    DetectionEvent,
    Event,
    HabitEvent,
    Light,
    NudgeSent,
    RuleOutcome,
    event_from_dict,
    new_event_id,
)
from desktalk.domain.habits import Nudge
from desktalk.domain.rules import DeskSnapshot, ObjectSnapshot, RuleHistory, StateChange
from desktalk_shared.detection import BBox, Detection, keep_inside
from desktalk_shared.wire import DetectorState, PiHealth, UploadInfo

__all__ = [
    'EVENT_TYPES', 'HABITS', 'AIUnavailable', 'AgentJoined', 'AgentLeft', 'AgentProfile', 'Alert', 'BBox',
    'CameraNotice', 'Compaction', 'Detection', 'DetectionEvent', 'DetectorState', 'DetectorUnavailable',
    'DeskSnapshot', 'Event', 'Frame', 'HabitEvent', 'Light', 'Nudge', 'NudgeSent', 'ObjectSnapshot', 'PiHealth',
    'RuleHistory', 'RuleOutcome', 'StateChange', 'UploadInfo', 'event_from_dict', 'keep_inside', 'new_event_id',
]
