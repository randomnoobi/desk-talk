"""
The event log, kept on this computer in data/events/<day>/ (the same files as always, so old logs still read):

    detections.jsonl   one line per analysed picture (3 a second), until the next compaction
    presence.jsonl     what compaction turns those into: "cup was on the desk from 14:02:10 to 14:31:55"
    events.jsonl       everything else: agents joining / leaving, alerts, habits, nudges, camera, compactions

Every 10 minutes (settings → event_log.compact_every_min) the scheduler calls compact(): the per-picture
lines are merged into intervals (desktalk/adapters/storage/intervals.py) and deleted, so the log stays
small. Days older than event_log.keep_days are deleted by prune(). Lines are JSON, one event per line,
newest at the bottom.

When something goes wrong with the files:
- a line cut off by a crash (or otherwise broken) is skipped and logged, never fatal, and the next event
  written starts on a new line so it isn't lost too;
- presence.jsonl is rewritten all-or-nothing (temp file + rename); if compaction stops part-way, the
  pictures it was working on stay in detections-*.compacting.jsonl and the next compaction picks them up.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import threading
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from desktalk.adapters.storage.files import write_atomically
from desktalk.adapters.storage.intervals import build_intervals, merge_intervals
from desktalk.config import EventLogSettings
from desktalk.domain import Compaction, DetectionEvent, Event

log = logging.getLogger(__name__)

RAW = 'detections.jsonl'
PRESENCE = 'presence.jsonl'
OTHER = 'events.jsonl'
COMPACTING = 'detections-*.compacting.jsonl'
_DAY = re.compile(r'\d{4}-\d{2}-\d{2}')          # a day folder's name, e.g. 2026-10-21


def _day_of(ts: float) -> str:
    """'2026-10-21' for a timestamp, in local time."""
    return time.strftime('%Y-%m-%d', time.localtime(ts))


def _line(data: dict[str, Any]) -> str:
    return json.dumps(data, separators=(',', ':'), default=str) + '\n'


class EventLog:
    """The EventStore (desktalk/ports.py).

    folder: where the day folders go (config.resolve(config.event_log.folder)).
    settings: merge_gap_sec, keep_days and log_empty_frames are used here; the scheduler uses
    compact_every_min and prune_every_min to call compact() and prune()."""

    def __init__(self, folder: Path, settings: EventLogSettings):
        self.folder = Path(folder)
        self._merge_gap = settings.merge_gap_sec
        self._keep_days = settings.keep_days
        self._log_empty_frames = settings.log_empty_frames
        self._lock = threading.Lock()
        self._compacting = threading.Lock()          # one compaction at a time, so no picture is counted twice
        self._broken_seen: dict[Path, int] = {}      # file → broken lines already reported
        self.last_compaction: dict[str, Any] | None = None     # the last compact() summary and its ts

    def write(self, event: Event) -> None:
        """Add one event to its day: pictures to detections.jsonl, everything else to events.jsonl."""
        if isinstance(event, DetectionEvent) and not event.detections and not self._log_empty_frames:
            return
        name = RAW if isinstance(event, DetectionEvent) else OTHER
        line = _line(event.to_dict()).encode('utf-8')
        with self._lock:
            d = self.folder / _day_of(event.ts)
            d.mkdir(parents=True, exist_ok=True)
            with open(d / name, 'a+b') as f:
                # A crash can leave the last line cut off; start on a new line so this one stays whole.
                end = f.seek(0, os.SEEK_END)
                if end:
                    f.seek(end - 1)
                    if f.read(1) != b'\n':
                        line = b'\n' + line
                f.write(line)

    def _read(self, path: Path) -> list[dict[str, Any]]:
        """The events in a .jsonl file. Broken lines (cut off by a crash, say) are skipped and logged."""
        out, broken = [], 0
        try:
            with open(path, encoding='utf-8', errors='replace') as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                    except ValueError:
                        data = None
                    if isinstance(data, dict):
                        out.append(data)
                    else:
                        broken += 1
        except FileNotFoundError:
            return []
        if broken and self._broken_seen.get(path) != broken:
            self._broken_seen[path] = broken
            log.warning('Skipped %d broken line%s in %s', broken, '' if broken == 1 else 's', path)
        return out

    def compact(self, now: float) -> dict[str, Any]:
        """Turn the per-picture lines of every day into intervals, then delete them.
        Returns {'frames', 'intervals', 'from', 'to'}; when there were pictures, a Compaction event is logged.
        A second compaction asked for meanwhile waits for this one, then finds nothing left to do."""
        with self._compacting:
            return self._compact(now)

    def _compact(self, now: float) -> dict[str, Any]:
        # Move the pictures aside first, so new ones go to a fresh detections.jsonl meanwhile.
        stamp = f'{now:.3f}'.replace('.', '')
        with self._lock:
            for d in self._day_folders():
                if (d / RAW).is_file():
                    work = d / f'detections-{stamp}.compacting.jsonl'
                    n = 0
                    while work.exists():        # one left by a compaction that failed at this same moment
                        n += 1
                        work = d / f'detections-{stamp}-{n}.compacting.jsonl'
                    os.replace(d / RAW, work)
        total_frames = total_new = 0
        first = last = None
        for work in sorted(w for d in self._day_folders() for w in d.glob(COMPACTING)):
            frames = self._read(work)
            if frames:
                times = [f.get('ts', 0) for f in frames]
                first = min(times) if first is None else min(first, *times)
                last = max(times) if last is None else max(last, *times)
            new = build_intervals(frames, self._merge_gap)
            presence = work.parent / PRESENCE
            with self._lock:
                merged = merge_intervals(self._read(presence), new, self._merge_gap)
                write_atomically(presence, ''.join(_line(iv) for iv in merged))
                work.unlink()
            total_frames += len(frames)
            total_new += len(new)
        summary = {'frames': total_frames, 'intervals': total_new,
                   'from': round(first, 3) if first is not None else None,
                   'to': round(last, 3) if last is not None else None}
        if total_frames:
            self.write(Compaction(ts=now, frames=total_frames, intervals=total_new, from_ts=summary['from'],
                                  to_ts=summary['to']))
        self.last_compaction = {'ts': now, **summary}
        return summary

    def prune(self, now: float) -> list[str]:
        """Delete the days older than keep_days (today counts as one). Returns the days deleted, oldest first.
        A day ahead of `now` (after the clock was off) is kept."""
        oldest_kept = (date.fromtimestamp(now) - timedelta(days=self._keep_days - 1)).isoformat()
        removed = []
        with self._lock:
            for d in sorted(self._day_folders()):
                if d.name < oldest_kept:
                    try:
                        shutil.rmtree(d)
                    except OSError as exc:
                        log.warning('Could not delete the old event log day %s: %s', d, exc)
                        continue
                    removed.append(d.name)
        return removed

    def _day_folders(self) -> list[Path]:
        if not self.folder.is_dir():
            return []
        return [d for d in self.folder.iterdir() if d.is_dir() and _DAY.fullmatch(d.name)]

    def _day_folder(self, day: str) -> Path | None:
        """The folder of a day, or None if `day` isn't a date like 2026-10-21 (so it can't point elsewhere)."""
        return self.folder / day if _DAY.fullmatch(day) else None

    def days(self) -> list[str]:
        """Days with a log, newest first."""
        return sorted((d.name for d in self._day_folders()), reverse=True)

    def events(self, day: str, types: set[str] | None, limit: int) -> list[dict[str, Any]]:
        """Up to `limit` non-picture events of a day, newest first; only `types` (e.g. {'alert'}) if given."""
        d = self._day_folder(day)
        if d is None:
            return []
        evs = self._read(d / OTHER)
        if types:
            evs = [e for e in evs if e.get('type') in types]
        return list(reversed(evs))[:limit]

    def presence(self, day: str, include_live: bool = True) -> list[dict[str, Any]]:
        """The intervals of a day, oldest first. include_live adds the pictures not compacted yet."""
        d = self._day_folder(day)
        if d is None:
            return []
        intervals = self._read(d / PRESENCE)
        if include_live:
            frames = []
            for path in [d / RAW, *sorted(d.glob(COMPACTING))]:
                frames.extend(self._read(path))
            if frames:
                intervals = merge_intervals(intervals, build_intervals(frames, self._merge_gap), self._merge_gap)
        return intervals

    def recent_detections(self, day: str, limit: int) -> list[dict[str, Any]]:
        """Up to `limit` picture lines not compacted yet, newest first."""
        d = self._day_folder(day)
        if d is None:
            return []
        return list(reversed(self._read(d / RAW)))[:limit]

    def counts(self, day: str) -> dict[str, Any]:
        """{'pictures_waiting': picture lines not compacted yet, 'events': {type: how many}}."""
        d = self._day_folder(day)
        if d is None:
            return {'pictures_waiting': 0, 'events': {}}
        by_type: dict[str, int] = {}
        for e in self._read(d / OTHER):
            by_type[e.get('type', '?')] = by_type.get(e.get('type', '?'), 0) + 1
        return {'pictures_waiting': len(self._read(d / RAW)), 'events': by_type}
