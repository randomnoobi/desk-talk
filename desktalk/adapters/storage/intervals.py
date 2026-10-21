"""
Per-picture detections → intervals: "the cup was on the desk from 14:02:10 to 14:31:55".

Works on the plain dicts of the event log's files. A detections.jsonl line:
    {"type": "detection", "ts": 1792345678.1, "source": "pi",
     "detected_obj": [{"label": "cup", "conf": 0.82, "bbox": [...], "center": [100, 101]}], ...}
A presence.jsonl line:
    {"type": "presence", "label": "cup", "interval": [1792345678.1, 1792347461.9], "frames": 5310,
     "avg_conf": 0.81, "center": [100.2, 101.0], "source": "pi"}
"""

from __future__ import annotations

from typing import Any


def build_intervals(frames: list[dict[str, Any]], gap_sec: float) -> list[dict[str, Any]]:
    """One interval per object per stretch on the desk, oldest first.
    A label that disappears for longer than `gap_sec` starts a new interval."""
    open_: dict[str, dict[str, Any]] = {}
    done: list[dict[str, Any]] = []
    for fr in sorted(frames, key=lambda f: f.get('ts', 0)):
        ts = float(fr.get('ts', 0))
        best: dict[str, dict[str, Any]] = {}          # the surest detection of each label in this picture
        for o in fr.get('detected_obj') or []:
            lab = o.get('label')
            if lab and (lab not in best or o.get('conf', 0) > best[lab].get('conf', 0)):
                best[lab] = o
        for lab, o in best.items():
            iv = open_.get(lab)
            if iv and ts - iv['interval'][1] <= gap_sec:
                iv['interval'][1] = ts
            else:
                if iv:
                    done.append(iv)
                iv = open_[lab] = {'type': 'presence', 'label': lab, 'interval': [ts, ts], 'frames': 0,
                                   '_conf': 0.0, '_cx': 0.0, '_cy': 0.0, 'source': fr.get('source', '')}
            iv['frames'] += 1
            iv['_conf'] += float(o.get('conf', 0))
            c = o.get('center') or [0, 0]
            iv['_cx'] += float(c[0])
            iv['_cy'] += float(c[1])
    done.extend(open_.values())
    for iv in done:
        n = max(1, iv['frames'])
        iv['avg_conf'] = round(iv.pop('_conf') / n, 3)
        iv['center'] = [round(iv.pop('_cx') / n, 1), round(iv.pop('_cy') / n, 1)]
        iv['interval'] = [round(iv['interval'][0], 3), round(iv['interval'][1], 3)]
    return sorted(done, key=lambda i: i['interval'][0])


def merge_intervals(existing: list[dict[str, Any]], new: list[dict[str, Any]],
                    gap_sec: float) -> list[dict[str, Any]]:
    """`existing` plus `new`, joining intervals of the same object that touch (a gap of at most `gap_sec`).
    Returns new dicts, oldest first; the lists passed in are left as they were."""
    out = [dict(i) for i in existing]
    last_by_label: dict[str, dict[str, Any]] = {}
    for iv in sorted(out, key=lambda i: i['interval'][1]):
        last_by_label[iv['label']] = iv
    for iv in sorted(new, key=lambda i: i['interval'][0]):
        prev = last_by_label.get(iv['label'])
        if prev and iv['interval'][0] - prev['interval'][1] <= gap_sec:
            n1, n2 = prev.get('frames', 1), iv.get('frames', 1)
            tot = max(1, n1 + n2)
            prev['avg_conf'] = round((prev.get('avg_conf', 0) * n1 + iv.get('avg_conf', 0) * n2) / tot, 3)
            pc, ic = prev.get('center') or [0, 0], iv.get('center') or [0, 0]
            prev['center'] = [round((pc[0] * n1 + ic[0] * n2) / tot, 1), round((pc[1] * n1 + ic[1] * n2) / tot, 1)]
            prev['interval'] = [min(prev['interval'][0], iv['interval'][0]),
                                max(prev['interval'][1], iv['interval'][1])]
            prev['frames'] = n1 + n2
        else:
            iv = dict(iv)
            out.append(iv)
            last_by_label[iv['label']] = iv
    return sorted(out, key=lambda i: i['interval'][0])
