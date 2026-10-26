"""
Plain data, no I/O: what the parts of Desk Talk pass to each other.

    Detection, BBox        one thing YOLO found (shared with the Pi: desktalk_shared.detection)
    Frame                  a picture from a camera, with what came with it
    Event and subclasses   what happened (DetectionEvent, AgentJoined, Alert, HabitEvent ...)
    AgentProfile, Nudge    a character, a habit reminder
    ChatJoin, Todo         an object joining, leaving or refused by the chat; a to-do on your list
    LENS                   Lens's id: the camera's own character
    AIUnavailable ...      errors (and UnknownChat: a 1-on-1 chat with an agent that doesn't exist)
    Picture, Size, Box     a picture, how big it is, a box on it (Point, Gravity too)
    TracePhoto             a picture kept of one kind of object for the developer page's trace
    DeskRules, Zone        the danger rules in use
    SeenObject, Danger,    the desk right now: what the camera sees, the dangers, a proof photo
    SafetyNow, Photo
    is_day                 a day's name, like 2026-10-21 (the event log's folders)
    ObjectSnapshot, DeskSnapshot, StateChange, HabitDone, RuleHistory
                           what the rule engine reads (where things are, what changed, what was logged,
                           what nudged)
    Phase, StateRecord     whether an object is on the desk (UNKNOWN, PRESENT, ABSENT), and a row of its history
    TestItem, RunStatus, SmoothingStep, TableInfo, QueryResult, DataFile ...
                           what the developer page shows (tests and their runs, smoothing, the database, the
                           data folder), and its errors RunAlreadyGoing and QueryRefused
"""

from desktalk.domain.agents import HABITS, LENS, AgentProfile, ChatJoin
from desktalk.domain.camera import Frame
from desktalk.domain.days import is_day
from desktalk.domain.desk import Danger, Photo, SafetyNow, SeenObject
from desktalk.domain.desk_rules import DeskRules, Zone
from desktalk.domain.dev import (
    DataFile,
    QueryRefused,
    QueryResult,
    RunAlreadyGoing,
    RunStatus,
    SmoothingStep,
    StepStatus,
    TableInfo,
    TestItem,
    TestOutcome,
)
from desktalk.domain.errors import AIUnavailable, DetectorUnavailable, UnknownChat, plain_error
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
    ObjectStateChanged,
    RuleOutcome,
    event_from_dict,
    new_event_id,
)
from desktalk.domain.habits import Nudge, Todo
from desktalk.domain.object_states import CHANGE_TYPES, Phase, StateRecord
from desktalk.domain.pictures import Box, Gravity, Picture, Point, Size, TracePhoto
from desktalk.domain.rules import DeskSnapshot, HabitDone, ObjectSnapshot, RuleHistory, StateChange
from desktalk_shared.detection import BBox, Detection, keep_inside
from desktalk_shared.wire import DetectorState, PiHealth, UploadInfo

__all__ = [
    'CHANGE_TYPES', 'EVENT_TYPES', 'HABITS', 'LENS', 'AgentJoined', 'AgentLeft', 'AgentProfile', 'AIUnavailable',
    'Alert', 'BBox', 'Box', 'CameraNotice', 'Compaction', 'Danger', 'DeskRules', 'DeskSnapshot', 'Detection',
    'DetectionEvent', 'DetectorState', 'DetectorUnavailable', 'Event', 'event_from_dict', 'Frame', 'Gravity',
    'HabitDone', 'HabitEvent', 'is_day', 'keep_inside', 'Light', 'new_event_id', 'Nudge', 'NudgeSent', 'ObjectSnapshot',
    'ObjectStateChanged', 'Phase', 'Photo', 'Picture', 'PiHealth', 'plain_error', 'Point', 'RuleHistory', 'RuleOutcome',
    'SafetyNow', 'SeenObject', 'Size', 'StateChange', 'StateRecord', 'UnknownChat', 'UploadInfo', 'Zone',
    'DataFile', 'QueryRefused', 'QueryResult', 'RunAlreadyGoing', 'RunStatus', 'SmoothingStep', 'StepStatus',
    'TableInfo', 'TestItem', 'TestOutcome', 'ChatJoin', 'Todo', 'TracePhoto',
]
