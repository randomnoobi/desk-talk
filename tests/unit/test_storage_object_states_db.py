"""The object state history in SQLite: the shared store suite, plus what only the real table does."""

from __future__ import annotations

import sqlite3

import pytest

from desktalk.adapters.storage import SqliteStateStore
from tests.contracts.state_store_contract import StateStoreContract, a_record


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
