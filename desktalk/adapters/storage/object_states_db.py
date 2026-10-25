"""
The object state history, kept on this computer in one SQLite file (data/object_states.sqlite).

One table, `events`: a row each time an object appears, goes away or moves, plus a `snapshot` row for every
object now and then. Rows are only ever added: two triggers make the database refuse any UPDATE or DELETE,
so the history can't be rewritten by mistake. WAL mode is on, so reading and writing don't block each other.

Two questions are asked of it:
- latest_states(since): each object's newest appeared / removed / snapshot row, for picking up after a restart;
- changes(since): the appeared / removed / moved rows after a time, oldest first, for the rule engine.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from collections.abc import Sequence
from pathlib import Path

from desktalk.domain.object_states import CHANGE_TYPES, Phase, StateRecord

log = logging.getLogger(__name__)

_TYPES = (*CHANGE_TYPES, 'snapshot')
_TYPE_LIST = ', '.join(f"'{t}'" for t in _TYPES)
_CHANGE_LIST = ', '.join(f"'{t}'" for t in CHANGE_TYPES)
_COLUMNS = 'ts, timezone, device_id, type, object, prev_state, new_state, conf_avg, bbox, dwell_sec'

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY,
    ts         REAL NOT NULL,
    timezone   TEXT NOT NULL,
    device_id  TEXT NOT NULL,
    type       TEXT NOT NULL CHECK (type IN ({_TYPE_LIST})),
    object     TEXT NOT NULL,
    prev_state TEXT NOT NULL,
    new_state  TEXT NOT NULL,
    conf_avg   REAL,
    bbox       TEXT,
    dwell_sec  REAL
);
CREATE INDEX IF NOT EXISTS events_object_ts ON events (object, ts);
CREATE INDEX IF NOT EXISTS events_ts ON events (ts);
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'events is append-only'); END;
"""


def _row_of(record: StateRecord) -> tuple:
    return (record.ts, record.timezone, record.device_id, record.type, record.label,
            str(record.prev_state), str(record.new_state), record.conf_avg,
            None if record.bbox is None else json.dumps(list(record.bbox)), record.dwell_sec)


def _record_of(row: tuple) -> StateRecord:
    ts, timezone, device_id, type_, label, prev_state, new_state, conf_avg, bbox, dwell_sec = row
    return StateRecord(ts=ts, timezone=timezone, device_id=device_id, type=type_, label=label,
                       prev_state=Phase(prev_state), new_state=Phase(new_state), conf_avg=conf_avg,
                       bbox=None if bbox is None else tuple(float(v) for v in json.loads(bbox)),
                       dwell_sec=dwell_sec)


class SqliteStateStore:
    """The ObjectStateStore (desktalk/ports.py).

    path: the database file (config.resolve(...)); its folder is made if it isn't there yet.
    One connection is shared by every thread, one at a time."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        with self._lock:
            self._db.execute('PRAGMA journal_mode=WAL')
            self._db.executescript(_SCHEMA)
        log.info('Object states are kept in %s', self.path)

    def append(self, records: Sequence[StateRecord]) -> None:
        """Add the rows, all or none."""
        if not records:
            return
        rows = [_row_of(r) for r in records]
        with self._lock, self._db:          # the `with self._db` commits, or rolls everything back on an error
            self._db.executemany(f'INSERT INTO events ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', rows)

    def latest_states(self, since: float) -> dict[str, StateRecord]:
        """Per object, its newest appeared / removed / snapshot row at or after `since`."""
        with self._lock:
            rows = self._db.execute(
                f'SELECT {_COLUMNS} FROM events WHERE type != ? AND ts >= ? ORDER BY ts, id',
                ('moved', since)).fetchall()
        return {record.label: record for record in map(_record_of, rows)}      # later rows replace earlier ones

    def changes(self, since: float) -> list[StateRecord]:
        """The appeared / removed / moved rows after `since`, oldest first."""
        with self._lock:
            rows = self._db.execute(
                f'SELECT {_COLUMNS} FROM events WHERE type IN ({_CHANGE_LIST}) AND ts > ? ORDER BY ts, id',
                (since,)).fetchall()
        return [_record_of(row) for row in rows]

    def close(self) -> None:
        with self._lock:
            self._db.close()
