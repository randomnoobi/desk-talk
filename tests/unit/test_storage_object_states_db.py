"""The object state history in SQLite: the shared store suite, plus what only the real table does."""

from __future__ import annotations

import sqlite3

import pytest

from desktalk.adapters.storage import SqliteStateStore
from tests.contracts.state_store_contract import StateStoreContract, a_record

# The table as it was before rows kept the boot they were saved in
OLD_SCHEMA = """
CREATE TABLE events (
    id         INTEGER PRIMARY KEY,
    ts         REAL NOT NULL,
    timezone   TEXT NOT NULL,
    device_id  TEXT NOT NULL,
    type       TEXT NOT NULL CHECK (type IN ('appeared', 'removed', 'moved', 'snapshot')),
    object     TEXT NOT NULL,
    prev_state TEXT NOT NULL,
    new_state  TEXT NOT NULL,
    conf_avg   REAL,
    bbox       TEXT,
    dwell_sec  REAL
);
CREATE INDEX events_object_ts ON events (object, ts);
CREATE INDEX events_ts ON events (ts);
CREATE TRIGGER events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'events is append-only'); END;
CREATE TRIGGER events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'events is append-only'); END;
"""


class TestSqliteStateStore(StateStoreContract):
    @pytest.fixture
    def store(self, tmp_path):
        store = SqliteStateStore(tmp_path / 'states.sqlite')
        yield store
        store.close()

    def test_the_table_is_append_only(self, store, tmp_path):
        store.append([a_record(100.0)])
        raw = sqlite3.connect(tmp_path / 'states.sqlite')
        try:
            with pytest.raises(sqlite3.DatabaseError, match='append-only'):
                raw.execute("UPDATE events SET object = 'cup'")
            with pytest.raises(sqlite3.DatabaseError, match='append-only'):
                raw.execute('DELETE FROM events')
        finally:
            raw.close()

    def test_the_table_is_still_append_only_after_a_prune(self, store, tmp_path):
        store.append([a_record(100.0), a_record(200.0, label='cup')])
        assert store.prune(150.0) == 1
        raw = sqlite3.connect(tmp_path / 'states.sqlite')
        try:
            with pytest.raises(sqlite3.DatabaseError, match='append-only'):
                raw.execute('DELETE FROM events')
            with pytest.raises(sqlite3.DatabaseError, match='append-only'):
                raw.execute("UPDATE events SET object = 'pen'")
        finally:
            raw.close()

    def test_a_prune_that_fails_deletes_nothing_and_keeps_the_rule(self, store, tmp_path):
        """The rule is taken off and put back in one transaction: when the delete fails, nothing changes."""
        store.append([a_record(100.0), a_record(200.0, label='cup')])
        raw = sqlite3.connect(tmp_path / 'states.sqlite')
        try:
            raw.execute("CREATE TRIGGER stop_it BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT, 'stopped'); END")
            raw.commit()
            with pytest.raises(sqlite3.DatabaseError, match='stopped'):
                store.prune(150.0)
            assert len(store.changes(0.0)) == 2
            triggers = {row[0] for row in raw.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")}
            assert 'events_no_delete' in triggers
            raw.execute('DROP TRIGGER stop_it')
            raw.commit()
        finally:
            raw.close()
        assert store.prune(150.0) == 1          # and the store still works

    def test_type_must_be_one_of_the_four(self, store, tmp_path):
        raw = sqlite3.connect(tmp_path / 'states.sqlite')
        try:
            with pytest.raises(sqlite3.IntegrityError):
                raw.execute("INSERT INTO events (ts, timezone, device_id, type, object, prev_state, new_state) "
                            "VALUES (1.0, '+08:00', 'pi-1', 'teleported', 'bottle', 'ABSENT', 'PRESENT')")
        finally:
            raw.close()

    def test_rows_survive_reopening(self, tmp_path):
        path = tmp_path / 'states.sqlite'
        rows = [a_record(100.0, conf_avg=0.5, bbox=(1.0, 2.0, 3.0, 4.0), dwell_sec=7.0), a_record(101.0, label='cup')]
        first = SqliteStateStore(path)
        first.append(rows)
        first.close()
        second = SqliteStateStore(path)
        try:
            assert second.changes(0.0) == rows
        finally:
            second.close()

    def test_a_database_from_before_boot_ids_still_opens(self, tmp_path):
        """A Pi updated from an older version keeps its file: its rows come back with no boot, and new rows get one."""
        path = tmp_path / 'states.sqlite'
        raw = sqlite3.connect(path)
        raw.executescript(OLD_SCHEMA)
        raw.execute("INSERT INTO events (ts, timezone, device_id, type, object, prev_state, new_state) "
                    "VALUES (100.0, '+08:00', 'pi-1', 'appeared', 'bottle', 'ABSENT', 'PRESENT')")
        raw.commit()
        raw.close()
        store = SqliteStateStore(path)
        try:
            [old] = store.changes(0.0)
            assert old.boot_id is None
            new = a_record(101.0, label='cup', boot_id='boot-a')
            store.append([new])
            assert store.changes(0.0) == [old, new]
            assert store.latest_states(0.0, boot_id='boot-a') == {'cup': new}
        finally:
            store.close()
        # Opening it again doesn't try to add the column twice
        SqliteStateStore(path).close()

    def test_a_database_from_before_boot_ids_can_be_pruned(self, tmp_path):
        path = tmp_path / 'states.sqlite'
        raw = sqlite3.connect(path)
        raw.executescript(OLD_SCHEMA)
        raw.execute("INSERT INTO events (ts, timezone, device_id, type, object, prev_state, new_state) "
                    "VALUES (100.0, '+08:00', 'pi-1', 'appeared', 'bottle', 'ABSENT', 'PRESENT')")
        raw.commit()
        raw.close()
        store = SqliteStateStore(path)
        try:
            assert store.prune(150.0) == 1
            assert store.changes(0.0) == []
        finally:
            store.close()

    def test_wal_mode_is_on(self, store, tmp_path):
        raw = sqlite3.connect(tmp_path / 'states.sqlite')
        try:
            assert raw.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
        finally:
            raw.close()

    def test_the_folder_is_made(self, tmp_path):
        store = SqliteStateStore(tmp_path / 'not' / 'there' / 'yet' / 'states.sqlite')
        try:
            store.append([a_record(100.0)])
            assert len(store.changes(0.0)) == 1
        finally:
            store.close()

    def test_a_failed_append_writes_none_of_its_rows(self, store):
        bad = a_record(101.0, type='teleported')
        with pytest.raises(sqlite3.IntegrityError):
            store.append([a_record(100.0), bad])
        assert store.changes(0.0) == []
