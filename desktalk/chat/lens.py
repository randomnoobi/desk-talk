"""
Lens, the camera's own character, posting in the group chat.

Every 2 seconds this asks CameraHealth (vision/camera_health.py) whether the
camera needs fixing and posts what Lens says. No AI is used, so it works even
without a key. Problems are also published as "camera" events for the log.
"""

from __future__ import annotations

import threading
import time

from ..events.stream import CAMERA
from ..habits.reminders import quiet_hours_now
from ..vision import relationships

SNAPSHOT_KEYS = {'cutoff', 'bumped', 'empty', 'glare'}


class LensThread(threading.Thread):
    def __init__(self, app):
        super().__init__(daemon=True, name='Lens')
        self.app = app

    def run(self):
        while True:
            time.sleep(2)
            try:
                self.tick(time.time())
            except Exception as exc:
                print(f'  [lens] {exc!r}')

    def tick(self, now: float) -> list:
        app = self.app
        src = app.source
        kw = {}
        if getattr(src, 'kind', '') == 'phone':
            kw = dict(fps=src.fps, gravity=src.recent_gravity(), stopped=src.stopped_by_user)
        elif getattr(src, 'kind', '') == 'pi':
            kw = dict(fps=src.fps, stopped=src.stopped_by_user)
        msgs = app.camera_health.check(now, connected=app.camera_connected(),
                                       reference_gravity=app.camera_reference_gravity,
                                       has_outline=bool(relationships.DESK_OUTLINE), **kw)
        if not msgs or quiet_hours_now(app.prefs, now):
            return []
        out = []
        for m in msgs:
            msg = {'type': 'group_message', 'character': 'lens', 'text': m['text'], 'lens': m['kind'],
                   'toast': m['kind'] == 'problem'}
            if m['kind'] == 'problem' and m['key'] in SNAPSHOT_KEYS:
                snap = app.desk.save_snapshot(label='Lens: camera view')
                if snap:
                    msg['snapshot'] = snap
            app.generator.broadcast(msg)
            app.stream.publish(CAMERA, ts=now, key=m['key'], kind=m['kind'], text=m['text'])
            out.append(msg)
            print(f'  [lens] {m["text"][:90]}')
        return out
