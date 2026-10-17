"""
Quick checks for the logic that doesn't need a camera or an AI key.

Run from the project folder:   python tests/test_logic.py
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

import camera_health as ch            # noqa: E402
import relationships as rel           # noqa: E402
from reminders import evidence_for, pick_reminder   # noqa: E402

MIN = 60


def test_reminders_need_evidence():
    start = 1_000_000
    # 10 minutes in: nobody has a reason to speak yet
    assert pick_reminder({'session_start': start}, start + 10 * MIN) is None
    # 55 minutes with no break: Computer speaks, and says why
    cid, evidence, text = pick_reminder({'session_start': start}, start + 55 * MIN)
    assert cid == 'monty' and '55 minutes' in evidence and '55 minutes' in text
    # a snack sitting out for 25 minutes
    ev = evidence_for('munch', {'session_start': start, 'snack_since': start + 5 * MIN,
                                'snack_name': 'donut'}, start + 30 * MIN)
    assert ev and 'donut' in ev[0]
    # Paper only talks if there are open to-dos
    assert evidence_for('sheets', {'session_start': start, 'open_todos': []}, start + 60 * MIN) is None


def test_desk_edges_ignore_picture_border():
    an = rel.RelationshipAnalyzer()
    an.frame_w, an.frame_h = 960, 720

    def alerts(outline, bbox):
        rel.DESK_OUTLINE = outline
        res = {'risk_events': [], 'explanations': [], 'relationships': []}
        an._edge_proximity({'glug': {'class_name': 'cup', 'bbox': bbox}}, res)
        return [e['edge'] for e in res['risk_events']]

    desk = [[0, 0], [1, 0], [1, 0.8], [0, 0.8]]       # the desk runs off the left, top and right
    assert alerts([], [0, 300, 60, 400]) == []        # nothing marked: no edge alerts
    assert alerts(desk, [0, 300, 60, 400]) == []      # at the side of the picture: fine
    assert alerts(desk, [400, 480, 470, 570]) == ['front']   # near the real front edge
    assert alerts(desk, [400, 200, 470, 300]) == []   # middle of the desk


def test_lens_notices_dark_and_says_thanks():
    bright = np.full((480, 640, 3), 140, np.uint8)
    bright[::8] = 30                                  # some texture so it isn't "blurry"
    dark = (bright * 0.15).astype(np.uint8)
    h = ch.CameraHealth({'cup'})
    said = []
    for t in range(0, 60):
        frame = dark if 20 <= t < 40 else bright
        h.sample(frame, [('cup', [100, 100, 200, 200])], now=1000 + t)
        said += [m['key'] + ':' + m['kind'] for m in h.check(1000 + t, connected=True, fps=9)]
    assert said == ['dark:problem', 'dark:fixed'], said


if __name__ == '__main__':
    for name, fn in list(globals().items()):
        if name.startswith('test_'):
            fn()
            print('ok ', name)
