"""
DeskState: what everyone needs to know about the desk right now.

One instance is shared by the camera producer, the consumers, the AI and the
web routes. Always read or change it while holding `desk.lock`.
"""

from __future__ import annotations

import threading
from collections import OrderedDict


class DeskState:
    def __init__(self, max_snapshots: int = 150):
        self.lock = threading.Lock()
        self.max_snapshots = max_snapshots

        # ── What the camera sees ──
        self.objects: dict = {}        # agent id → {class_name, bbox, center, conf, cam} (agents in the chat)
        self.seen_labels: dict = {}    # every detector word in the latest picture → its best box
        self.frames: dict = {}         # cam index → annotated JPEG (live view, "Boxes")
        self.raw_frames: dict = {}     # cam index → plain JPEG ("Plain")
        self.frame_w = 640
        self.frame_h = 480
        self.cameras: dict = {}        # cam index → True once it produced a picture
        self.light: dict = {}          # latest {'brightness': 0-255, 'lux': float|None}
        self.person_last_seen: float | None = None
        self.last_motion_ts: float | None = None
        self.session_start: float | None = None    # when the camera started watching this session

        # ── Safety ──
        self.safety_state = 'SAFE'
        self.safety_dangers: list = []
        self.rel_result: dict | None = None
        self.danger_active_since: dict = {}   # frozenset pair → when the danger started
        self.recent_events: list = []         # last alerts, for the AI's context
        self.active_scenarios = {'risk_detection': True, 'lost_object': True, 'reminders': True}

        # ── Agents and chat ──
        self.object_first_seen: dict = {}     # agent id → first time this session
        self.object_last_seen: dict = {}
        self.char_widgets: dict = {}          # agent id → tracker data sent by the app (to-do list...)
        self.widget_views: dict = {}          # agent id → [{'ts', 'action'}]
        self.char_memory: dict = {}           # agent id → recent things it said
        self.last_user_activity = 0.0         # user typing / sending → idle chat waits

        # ── Lost items ──
        self.lost_last_vlm_visible: set = set()    # what the last vision scan saw (also tags proof photos)

        # ── Vision (Ollama) habit checks ──
        self.vision_habit_brief = ''          # the last posture check in a few words, for the AI's nudges

        # ── Proof photos ──
        self.snapshots: OrderedDict = OrderedDict()
        self.snapshot_order: list = []        # newest first: id, ts, label, tags

    def first_raw_frame(self):
        """Newest plain JPEG from any camera (call with the lock held)."""
        for ci in sorted(self.raw_frames):
            if self.raw_frames.get(ci):
                return self.raw_frames[ci]
        return None
