"""
The event log, kept on this computer in data/events/<day>/:

    detections.jsonl   one line per analysed picture (3 a second). "Discrete" events.
    presence.jsonl     what compaction turns those into: "cup was on the desk from
                       14:02:10 to 14:31:55". "Continuous" intervals [left, right].
    events.jsonl       everything else: agents joining / leaving, alerts, habits, nudges.

Every 10 minutes (settings → event_log.compact_every_min) the scheduler calls
compact(): the per-picture lines are merged into intervals and deleted, so the
log stays small. Lines are JSON, one event per line, newest at the bottom.

A detection line looks like the whiteboard sketch:
    {"type": "detection", "ts": 1792345678.1, "frame_id": "pi-000123",
     "detected_obj": [{"label": "cell phone", "center": [100, 101], "bbox": [...], "conf": 0.82}]}
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
from pathlib import Path

from ..util import local_day
from .stream import COMPACTION, DETECTION, make_event

RAW = 'detections.jsonl'
PRESENCE = 'presence.jsonl'
OTHER = 'events.jsonl'


def build_intervals(frames: list[dict], gap_sec: float) -> list[dict]:
    """Per-picture detections → one interval per object per stretch on the desk.

    A label that disappears for longer than `gap_sec` starts a new interval."""
    open_: dict[str, dict] = {}
    done: list[dict] = []
    for fr in sorted(frames, key=lambda f: f.get('ts', 0)):
        ts = float(fr.get('ts', 0))
        best: dict[str, dict] = {}
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


def merge_intervals(existing: list[dict], new: list[dict], gap_sec: float) -> list[dict]:
    """Add `new` intervals to `existing`, joining ones for the same object that touch."""
    out = [dict(i) for i in existing]
    last_by_label: dict[str, dict] = {}
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
            prev['interval'] = [min(prev['interval'][0], iv['interval'][0]), max(prev['interval'][1], iv['interval'][1])]
            prev['frames'] = n1 + n2
        else:
            iv = dict(iv)
            out.append(iv)
            last_by_label[iv['label']] = iv
    return sorted(out, key=lambda i: i['interval'][0])


def _read_jsonl(path: Path) -> list[dict]:
    out = []
    try:
        with open(path, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue          # a half-written last line after a crash
    except OSError:
        pass
    return out


class EventLog:
    def __init__(self, folder: Path, merge_gap_sec: float = 3.0, keep_days: int = 30,
                 log_empty_frames: bool = True):
        self.folder = Path(folder)
        self.merge_gap_sec = float(merge_gap_sec)
        self.keep_days = int(keep_days)
        self.log_empty_frames = log_empty_frames
        self._lock = threading.Lock()
        self.last_compaction: dict | None = None

    def day_dir(self, day: str) -> Path:
        return self.folder / day

    # ---- consumer: write every event ----
    def write(self, ev: dict) -> None:
        if ev.get('type') == DETECTION and not ev.get('detected_obj') and not self.log_empty_frames:
            return
        day = local_day(ev.get('ts'))
        name = RAW if ev.get('type') == DETECTION else OTHER
        line = json.dumps(ev, separators=(',', ':'), default=str) + '\n'
        with self._lock:
            d = self.day_dir(day)
            d.mkdir(parents=True, exist_ok=True)
            with open(d / name, 'a', encoding='utf-8') as f:
                f.write(line)

    # ---- the 10-minute job ----
    def compact(self, now: float | None = None) -> dict:
        """Turn per-picture detections into intervals, then delete the per-picture lines."""
        now = now or time.time()
        stamp = f'{now:.3f}'.replace('.', '')
        with self._lock:
            if self.folder.is_dir():
                for d in self.folder.iterdir():
                    raw = d / RAW
                    if d.is_dir() and raw.is_file():
                        os.replace(raw, d / f'detections-{stamp}.compacting.jsonl')
        total_frames = total_new = 0
        t0 = t1 = None
        for work in sorted(self.folder.glob('*/detections-*.compacting.jsonl')) if self.folder.is_dir() else []:
            frames = _read_jsonl(work)
            if frames:
                ts = [f.get('ts', 0) for f in frames]
                t0 = min(ts) if t0 is None else min(t0, min(ts))
                t1 = max(ts) if t1 is None else max(t1, max(ts))
            new = build_intervals(frames, self.merge_gap_sec)
            pres = work.parent / PRESENCE
            with self._lock:
                merged = merge_intervals(_read_jsonl(pres), new, self.merge_gap_sec)
                tmp = pres.with_suffix('.tmp')
                with open(tmp, 'w', encoding='utf-8') as f:
                    for iv in merged:
                        f.write(json.dumps(iv, separators=(',', ':')) + '\n')
                os.replace(tmp, pres)
                work.unlink()
            total_frames += len(frames)
            total_new += len(new)
        summary = {'frames': total_frames, 'intervals': total_new,
                   'from': round(t0, 3) if t0 else None, 'to': round(t1, 3) if t1 else None}
        if total_frames:
            self.write(make_event(COMPACTION, ts=now, **summary))
        self.last_compaction = {'ts': now, **summary}
        return summary

    def prune(self, now: float | None = None) -> list[str]:
        """Delete day folders older than keep_days."""
        now = now or time.time()
        keep = {local_day(now - i * 86400) for i in range(max(1, self.keep_days))}
        removed = []
        if self.folder.is_dir():
            for d in self.folder.iterdir():
                if d.is_dir() and len(d.name) == 10 and d.name[4] == '-' and d.name not in keep:
                    shutil.rmtree(d, ignore_errors=True)
                    removed.append(d.name)
        return removed

    # ---- reading (Event log page) ----
    def days(self) -> list[str]:
        if not self.folder.is_dir():
            return []
        return sorted((d.name for d in self.folder.iterdir() if d.is_dir()), reverse=True)

    def events(self, day: str, types: set | None = None, limit: int = 200) -> list[dict]:
        """Non-picture events for a day, newest first."""
        evs = _read_jsonl(self.day_dir(day) / OTHER)
        if types:
            evs = [e for e in evs if e.get('type') in types]
        return list(reversed(evs))[:limit]

    def presence(self, day: str, include_live: bool = True) -> list[dict]:
        """Intervals for a day. include_live adds the not-yet-compacted pictures too."""
        d = self.day_dir(day)
        ivs = _read_jsonl(d / PRESENCE)
        if include_live:
            frames = []
            for p in [d / RAW, *d.glob('detections-*.compacting.jsonl')]:
                frames.extend(_read_jsonl(p))
            if frames:
                ivs = merge_intervals(ivs, build_intervals(frames, self.merge_gap_sec), self.merge_gap_sec)
        return ivs

    def recent_detections(self, day: str, limit: int = 50) -> list[dict]:
        return list(reversed(_read_jsonl(self.day_dir(day) / RAW)))[:limit]

    def counts(self, day: str) -> dict:
        d = self.day_dir(day)
        raw = 0
        try:
            with open(d / RAW, encoding='utf-8') as f:
                raw = sum(1 for _ in f)
        except OSError:
            pass
        evs = _read_jsonl(d / OTHER)
        by_type: dict[str, int] = {}
        for e in evs:
            by_type[e.get('type', '?')] = by_type.get(e.get('type', '?'), 0) + 1
        return {'pictures_waiting': raw, 'events': by_type}
