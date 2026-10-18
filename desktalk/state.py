"""
DeskState: what everyone needs to know about the desk right now.

One instance is shared by the camera producer, the consumers, the AI and the
web routes. Always read or change it while holding `desk.lock`.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections import OrderedDict

import cv2
import numpy as np


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
        self.cooldowns: dict = {}
        self.interaction_counts: dict = {}
        self.pet_last_seen: dict = {}
        self.pet_near_cooldown: dict = {}
        self.last_clutter_alert = 0.0
        self.last_water_plants_reminder = 0.0
        self.active_scenarios = {'risk_detection': True, 'lost_object': True, 'reminders': True}

        # ── Agents and chat ──
        self.object_first_seen: dict = {}     # agent id → first time this session
        self.object_last_seen: dict = {}
        self.char_widgets: dict = {}          # agent id → tracker data sent by the app (to-do list...)
        self.widget_views: dict = {}          # agent id → [{'ts', 'action'}]
        self.char_memory: dict = {}           # agent id → recent things it said
        self.last_user_activity = 0.0         # user typing / sending → idle chat waits

        # ── Lost items ──
        self.lost_objects: dict = {}          # item id → {'last_seen', 'alerted', 'source', 'last_near'}
        self.lost_prev_gray = None
        self.lost_last_scan_ts = 0.0
        self.lost_last_vlm_ts = 0.0
        self.lost_last_vlm_visible: set = set()
        self.loseable_snap_armed = False
        self.loseable_snap_prev: set = set()
        self.loseable_snap_last_ts: dict = {}
        self.loseable_vlm_snap_armed = False
        self.last_desk_scan_snapshot_ts = 0.0

        # ── Vision (Ollama) habit checks ──
        self.habit_vision_brief = ''
        self.habit_vlm_brief = ''
        self.vlm_habit_state: dict = {}
        self.vlm_habit_ts = 0.0
        self.vlm_habit_alert_ts: dict = {}
        self.vlm_posture_slouch_since = None
        self.vlm_prev_nail_biting = False
        self.habit_nail_last_event_ts = 0.0
        self.habit_events: list = []          # posture / nail-biting log (newest first)

        # ── Proof photos ──
        self.snapshots: OrderedDict = OrderedDict()
        self.snapshot_order: list = []        # newest first: id, ts, label, tags

    def first_raw_frame(self):
        """Newest plain JPEG from any camera (call with the lock held)."""
        for ci in sorted(self.raw_frames):
            if self.raw_frames.get(ci):
                return self.raw_frames[ci]
        return None

    def save_snapshot(self, cam_idx=None, crop_bboxes=None, label='Capture'):
        """Copy frame bytes under lock; run cv2 crop *outside* lock so the camera thread never stalls."""
        snap_id = uuid.uuid4().hex[:12]
        with self.lock:
            jpeg = None
            if cam_idx is not None:
                j = self.frames.get(cam_idx)
                if j:
                    jpeg = bytes(j)
            if not jpeg:
                for ci in sorted(self.frames.keys()):
                    j = self.frames.get(ci)
                    if j:
                        jpeg = bytes(j)
                        break
        if not jpeg:
            return None

        if crop_bboxes and len(crop_bboxes) > 0:
            arr = np.frombuffer(jpeg, dtype=np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if img is not None:
                ih, iw = img.shape[:2]
                ux1 = min(b[0] for b in crop_bboxes)
                uy1 = min(b[1] for b in crop_bboxes)
                ux2 = max(b[2] for b in crop_bboxes)
                uy2 = max(b[3] for b in crop_bboxes)
                pw = (ux2 - ux1) * 0.25
                ph = (uy2 - uy1) * 0.25
                cx1, cy1 = max(0, int(ux1 - pw)), max(0, int(uy1 - ph))
                cx2, cy2 = min(iw, int(ux2 + pw)), min(ih, int(uy2 + ph))
                if (cx2 - cx1) > 40 and (cy2 - cy1) > 40:
                    cropped = img[cy1:cy2, cx1:cx2]
                    _, buf = cv2.imencode('.jpg', cropped, [cv2.IMWRITE_JPEG_QUALITY, 85])
                    jpeg = buf.tobytes()

        with self.lock:
            tag_meta = {
                'char_ids': list(self.objects.keys()),
                'labels': sorted(str(k).lower() for k in self.seen_labels),
                'vlm_labels': sorted(str(x).lower() for x in self.lost_last_vlm_visible),
            }
            self.snapshots[snap_id] = jpeg
            self.snapshot_order.insert(0, {'id': snap_id, 'ts': time.time(), 'label': label or 'Capture', **tag_meta})
            self.snapshot_order = self.snapshot_order[:self.max_snapshots]
            while len(self.snapshots) > self.max_snapshots:
                old_id, _ = self.snapshots.popitem(last=False)
                self.snapshot_order = [x for x in self.snapshot_order if x.get('id') != old_id]
        return snap_id
