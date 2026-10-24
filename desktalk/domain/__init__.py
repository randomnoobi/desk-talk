"""
Plain data, no I/O: what the parts of Desk Talk pass to each other.

    Detection, BBox        one thing YOLO found (shared with the Pi: desktalk_shared.detection)
    Frame                  a picture from a camera, with what came with it
    Event and subclasses   what happened (DetectionEvent, AgentJoined, Alert, HabitEvent ...)
    AgentProfile, Nudge    a character, a habit reminder
    Picture, Size, Box     a picture, how big it is, a box on it (Point, Gravity too)
    DeskRules, Zone        the danger rules in use
"""

from desktalk.domain.agents import HABITS, AgentProfile
from desktalk.domain.camera import Frame
from desktalk.domain.desk_rules import DeskRules, Zone
from desktalk.domain.errors import AIUnavailable, DetectorUnavailable, plain_error
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
    event_from_dict,
    new_event_id,
)
from desktalk.domain.habits import Nudge
from desktalk.domain.pictures import Box, Gravity, Picture, Point, Size
from desktalk_shared.detection import BBox, Detection, keep_inside
from desktalk_shared.wire import DetectorState, PiHealth, UploadInfo

__all__ = [
    'EVENT_TYPES', 'HABITS', 'AIUnavailable', 'AgentJoined', 'AgentLeft', 'AgentProfile', 'Alert', 'BBox', 'Box',
    'CameraNotice', 'Compaction', 'DeskRules', 'Detection', 'DetectionEvent', 'DetectorState', 'DetectorUnavailable',
    'Event', 'Frame', 'Gravity', 'HabitEvent', 'Light', 'Nudge', 'NudgeSent', 'Picture', 'PiHealth', 'Point', 'Size',
    'UploadInfo', 'Zone', 'event_from_dict', 'keep_inside', 'new_event_id', 'plain_error',
]
