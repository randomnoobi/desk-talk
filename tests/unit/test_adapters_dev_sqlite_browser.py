"""The developer page's read-only look inside the databases (desktalk/adapters/dev/sqlite_browser.py), on a temp
copy of the real object state history (SqliteStateStore) holding three rows."""

from __future__ import annotations

import pytest

from desktalk.adapters.dev.sqlite_browser import SqliteBrowser
from desktalk.adapters.storage import SqliteStateStore
from desktalk.domain import Phase, QueryRefused
from desktalk.ports import DatabaseBrowser
from desktalk_shared.clock import SystemClock
from tests.contracts.dev_contracts import DatabaseBrowserContract
from tests.contracts.state_store_contract import a_record
from tests.fakes import FakeClock
from tests.unit.test_core_vision_kit import app_config

WORDS = app_config().prompts.dev
CHECK_EVERY_STEPS = app_config().dev.sql_check_every_steps
NEVER_ENDS = 'WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r) SELECT count(*) FROM r'


def three_rows():
    return [a_record(100.0, label='cup'),
            a_record(101.0, label='pen/pencil'),
            a_record(102.0, label='cup', type='removed', prev_state=Phase.PRESENT, new_state=Phase.ABSENT)]


@pytest.fixture
def db_file(tmp_path):
    """An object state history file with three rows, closed again."""
    path = tmp_path / 'object_states.sqlite'
    store = SqliteStateStore(path)
    store.append(three_rows())
    store.close()
    return path


def browser_for(path, *, max_rows=500, timeout_sec=5.0, clock=None, check_every_steps=CHECK_EVERY_STEPS):
    return SqliteBrowser({'object_states': path}, max_rows=max_rows, timeout_sec=timeout_sec,
                         clock=clock or FakeClock(), words=WORDS, check_every_steps=check_every_steps)


class TestSqliteBrowserPassesDatabaseBrowserContract(DatabaseBrowserContract):
    @pytest.fixture
    def browser(self, db_file):
        return browser_for(db_file)


def test_it_is_a_database_browser(db_file):
    assert isinstance(browser_for(db_file), DatabaseBrowser)


def test_tables_show_columns_and_row_counts(db_file):
    browser = browser_for(db_file)
    assert browser.databases() == ['object_states']
    [events] = browser.tables('object_states')
    assert events.name == 'events'
    assert len(events.columns) == 11
    assert events.columns[:3] == ('id', 'ts', 'timezone')
    assert events.rows == 3
    assert events.sql.startswith('CREATE TABLE')
    assert 'append-only' in events.sql          # its triggers come with it


def test_a_select_returns_rows(db_file):
    result = browser_for(db_file).query('object_states', 'SELECT object, type FROM events ORDER BY ts')
    assert result.columns == ('object', 'type')
    assert result.rows == (('cup', 'appeared'), ('pen/pencil', 'appeared'), ('cup', 'removed'))
    assert result.truncated is False


def test_a_select_may_end_with_a_semicolon_or_a_comment(db_file):
    browser = browser_for(db_file)
    assert browser.query('object_states', 'SELECT count(*) FROM events;').rows == ((3,),)
    assert browser.query('object_states', 'SELECT count(*) FROM events -- how many').rows == ((3,),)


def test_rows_are_capped(db_file):
    result = browser_for(db_file, max_rows=2).query('object_states', 'SELECT * FROM events')
    assert len(result.rows) == 2
    assert result.truncated is True


def test_exactly_the_cap_is_not_truncated(db_file):
    result = browser_for(db_file, max_rows=3).query('object_states', 'SELECT * FROM events')
    assert len(result.rows) == 3
    assert result.truncated is False


def test_the_time_taken_comes_from_the_clock(db_file):
    clock = FakeClock()
    result = browser_for(db_file, clock=clock).query('object_states', 'SELECT 1')
    assert result.took_ms == 0.0


def test_rows_still_in_the_open_store_are_seen(tmp_path):
    """The app keeps the store open while the page looks, so the newest rows may still be in the WAL file."""
    path = tmp_path / 'object_states.sqlite'
    store = SqliteStateStore(path)
    try:
        store.append(three_rows())
        assert browser_for(path).query('object_states', 'SELECT count(*) FROM events').rows == ((3,),)
        store.append([a_record(103.0, label='phone')])          # and the store can still write afterwards
        assert browser_for(path).tables('object_states')[0].rows == 4
    finally:
        store.close()


@pytest.mark.parametrize('sql', [
    'DELETE FROM events',
    "INSERT INTO events (ts, timezone, device_id, type, object, prev_state, new_state) "
    "VALUES (1.0, '+08:00', 'pi-1', 'appeared', 'cup', 'ABSENT', 'PRESENT')",
    'DROP TABLE events',
    "ATTACH 'x.db' AS x",
    "VACUUM INTO 'x.db'",
    'PRAGMA query_only = OFF',
    'CREATE TEMP TABLE t (a)',
    'SELECT 1; DELETE FROM events',
])
def test_writing_is_refused(db_file, tmp_path, monkeypatch, sql):
    monkeypatch.chdir(tmp_path)                 # where 'x.db' would be made
    before = db_file.read_bytes()
    with pytest.raises(QueryRefused):
        browser_for(db_file).query('object_states', sql)
    assert db_file.read_bytes() == before
    assert not (tmp_path / 'x.db').exists()


def test_a_write_is_refused_in_the_words_from_prompts_yaml(db_file):
    with pytest.raises(QueryRefused) as refused:
        browser_for(db_file).query('object_states', 'DELETE FROM events')
    assert str(refused.value) == WORDS.query_refused.format(reason='not authorized')


@pytest.mark.parametrize('sql', ['SELECT 1; DELETE FROM events', 'SELECT 1; SELECT 2', "SELECT 'not closed", '', ' ; '])
def test_anything_but_one_whole_statement_is_refused(db_file, sql):
    with pytest.raises(QueryRefused) as refused:
        browser_for(db_file).query('object_states', sql)
    assert str(refused.value) == WORDS.query_not_one_statement


def test_a_mistake_in_the_query_is_refused_and_says_what_sqlite_said(db_file):
    with pytest.raises(QueryRefused, match='syntax error'):
        browser_for(db_file).query('object_states', 'SELEC 1')
    with pytest.raises(QueryRefused, match='no such table'):
        browser_for(db_file).query('object_states', 'SELECT * FROM no_such_table')


def test_a_mistake_isnt_called_a_write(db_file):
    """A typing mistake gets the plain query_failed words, not "opened read-only" (that's for writes)."""
    with pytest.raises(QueryRefused) as refused:
        browser_for(db_file).query('object_states', 'SELEC 1')
    assert str(refused.value) == WORDS.query_failed.format(reason='near "SELEC": syntax error')
    assert str(refused.value) != WORDS.query_refused.format(reason='near "SELEC": syntax error')


def test_a_slow_query_is_stopped(db_file):
    browser = browser_for(db_file, timeout_sec=0.2, clock=SystemClock())
    with pytest.raises(QueryRefused, match='sql_timeout_sec') as refused:
        browser.query('object_states', NEVER_ENDS)
    assert str(refused.value) == WORDS.query_timed_out.format(seconds=0.2)


@pytest.mark.parametrize('check_every_steps', [1, 100_000])
def test_a_slow_query_is_stopped_however_often_it_checks(db_file, check_every_steps):
    browser = browser_for(db_file, timeout_sec=0.2, clock=SystemClock(), check_every_steps=check_every_steps)
    with pytest.raises(QueryRefused, match='sql_timeout_sec'):
        browser.query('object_states', NEVER_ENDS)


def test_the_time_limit_is_per_query(db_file):
    clock = FakeClock()
    browser = browser_for(db_file, timeout_sec=1.0, clock=clock)
    browser.query('object_states', 'SELECT 1')
    clock.advance(10)                           # time passing between queries doesn't count
    assert browser.query('object_states', 'SELECT count(*) FROM events').rows == ((3,),)


def test_a_missing_database_has_no_tables(tmp_path):
    path = tmp_path / 'not_yet.sqlite'
    browser = browser_for(path)
    assert browser.databases() == ['object_states']
    assert browser.tables('object_states') == []
    with pytest.raises(QueryRefused):
        browser.query('object_states', 'SELECT 1')
    assert not path.exists()                    # looking doesn't make it


def test_an_unknown_database_says_so_in_the_words_from_prompts_yaml(db_file):
    with pytest.raises(KeyError) as unknown:
        browser_for(db_file).tables('nope')
    assert unknown.value.args[0] == WORDS.unknown_database.format(name='nope')
