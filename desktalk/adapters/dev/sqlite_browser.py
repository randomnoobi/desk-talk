"""
The developer page's look inside the app's SQLite databases, read-only (the DatabaseBrowser in desktalk/ports.py).

Every call opens its own connection to the file in read-only mode, with `PRAGMA query_only` on, and closes it
again. A query typed on the page must be one whole statement, and only reading is allowed: an authorizer lets
SQLite run nothing but SELECTs (reading tables, calling functions, WITH RECURSIVE). That matters even on a
read-only connection, because `ATTACH 'x.db'` and `VACUUM INTO 'x.db'` would still make new files on disk.

A query that runs longer than settings → dev.sql_timeout_sec (by the clock) is stopped, and at most
dev.sql_max_rows rows come back. A database whose file isn't there yet has no tables, and looking doesn't make it.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from contextlib import closing
from pathlib import Path

from desktalk.config.prompts import DevWords
from desktalk.domain import QueryRefused, QueryResult, TableInfo
from desktalk.ports import Clock

# What a typed query may make SQLite do: read tables, call functions, and run SELECTs (WITH RECURSIVE too).
_ALLOWED = frozenset({sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE})
# What SQLite says when a query was refused (the authorizer said no) or tried to write to the read-only file:
# those get the "opened read-only" words; any other mistake gets SQLite's own words.
_REFUSED = frozenset({sqlite3.SQLITE_AUTH, sqlite3.SQLITE_READONLY})
# Python's sqlite3 says this when it is handed more than one statement.
_TWO_STATEMENTS = 'one statement'


def _quoted(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _only_reading(action: int, *_details) -> int:
    return sqlite3.SQLITE_OK if action in _ALLOWED else sqlite3.SQLITE_DENY


class SqliteBrowser:
    """The DatabaseBrowser (desktalk/ports.py).

    files: each database's name and file, e.g. {'object_states': config.resolve(config.object_states.database)}.
    max_rows, timeout_sec: settings → dev.sql_max_rows and dev.sql_timeout_sec.
    check_every_steps: settings → dev.sql_check_every_steps, how often a running query looks at the clock.
    words: config/prompts.yaml → dev, for the refusals."""

    def __init__(self, files: Mapping[str, Path], max_rows: int, timeout_sec: float, clock: Clock,
                 words: DevWords, *, check_every_steps: int):
        self._files = {name: Path(path) for name, path in files.items()}
        self._max_rows = max_rows
        self._timeout_sec = timeout_sec
        self._check_every_steps = check_every_steps
        self._clock = clock
        self._words = words

    def databases(self) -> list[str]:
        return list(self._files)

    def tables(self, db: str) -> list[TableInfo]:
        path = self._file(db)
        if not path.exists():
            return []
        with closing(self._open(path)) as connection:
            names = [name for (name,) in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
            return [self._table(connection, name) for name in names]

    def query(self, db: str, sql: str) -> QueryResult:
        path = self._file(db)
        text = self._one_statement(sql)
        started = self._clock.monotonic()
        stop_at = started + self._timeout_sec
        took_too_long = False

        def out_of_time() -> bool:
            nonlocal took_too_long
            took_too_long = self._clock.monotonic() > stop_at
            return took_too_long

        try:
            with closing(self._open(path)) as connection:
                connection.set_authorizer(_only_reading)
                connection.set_progress_handler(out_of_time, self._check_every_steps)
                cursor = connection.execute(text)
                if cursor.description is None:          # only a comment, or nothing at all
                    raise QueryRefused(self._words.query_not_one_statement)
                rows = cursor.fetchmany(self._max_rows + 1)
                columns = tuple(column[0] for column in cursor.description)
        except sqlite3.ProgrammingError as error:
            if _TWO_STATEMENTS in str(error):
                raise QueryRefused(self._words.query_not_one_statement) from error
            raise QueryRefused(self._words.query_failed.format(reason=error)) from error
        except sqlite3.Error as error:
            if took_too_long:
                raise QueryRefused(self._words.query_timed_out.format(seconds=self._timeout_sec)) from error
            if getattr(error, 'sqlite_errorcode', None) in _REFUSED:
                raise QueryRefused(self._words.query_refused.format(reason=error)) from error
            raise QueryRefused(self._words.query_failed.format(reason=error)) from error
        return QueryResult(columns=columns, rows=tuple(rows[:self._max_rows]),
                           truncated=len(rows) > self._max_rows,
                           took_ms=(self._clock.monotonic() - started) * 1000)

    def _file(self, db: str) -> Path:
        if db not in self._files:
            raise KeyError(self._words.unknown_database.format(name=db))
        return self._files[db]

    def _one_statement(self, sql: str) -> str:
        """The query, ending in a semicolon. Refused when it isn't one whole statement (an unclosed quote,
        nothing at all); a second statement after it is refused by sqlite3 itself when it runs."""
        text = sql.strip()
        if not sqlite3.complete_statement(text):
            text += '\n;'                               # on its own line, so a closing -- comment can't hide it
        if not sqlite3.complete_statement(text) or not text.strip('; \t\r\n'):
            raise QueryRefused(self._words.query_not_one_statement)
        return text

    @staticmethod
    def _open(path: Path) -> sqlite3.Connection:
        connection = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, isolation_level=None)
        connection.execute('PRAGMA query_only = ON')
        return connection

    @staticmethod
    def _table(connection: sqlite3.Connection, name: str) -> TableInfo:
        columns = tuple(column for (column,) in connection.execute(
            'SELECT name FROM pragma_table_info(?) ORDER BY cid', (name,)))
        (rows,) = connection.execute(f'SELECT count(*) FROM {_quoted(name)}').fetchone()
        made_by = [sql for (sql,) in connection.execute(         # the table first, then its indexes and triggers
            "SELECT sql FROM sqlite_schema WHERE tbl_name = ? AND sql IS NOT NULL "
            "ORDER BY type != 'table', type, name", (name,))]
        return TableInfo(name=name, columns=columns, rows=rows, sql=';\n'.join(made_by) + ';')
