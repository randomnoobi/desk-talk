"""
What the developer page (http://localhost:5000/dev/) is shown: tests and their runs, each object's smoothing,
the database's tables, and the files in the data folder.

    TestItem, TestOutcome    one test pytest found, and how it went
    StepStatus, RunStatus    one step of "Run everything", and a whole run (the check or some tests)
    SmoothingStep            what the object state tracker made of one picture, for one kind of object
    TableInfo, QueryResult   a database table, and what a query gave back
    DataFile                 a file or folder in the data folder
    RunAlreadyGoing          a run was started while another was still going
    QueryRefused             a query that would change the database, isn't one statement, or took too long
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TestItem:
    __test__ = False                    # not a test itself (pytest looks at classes named Test...)

    id: str                             # pytest's node id, e.g. 'tests/unit/test_x.py::test_y'
    file: str                           # the file it's in, e.g. 'tests/unit/test_x.py'
    markers: tuple[str, ...]            # e.g. ('slow',)


@dataclass(frozen=True)
class TestOutcome:
    __test__ = False

    id: str
    outcome: str                        # 'passed', 'failed', 'skipped' or 'error'
    duration_sec: float
    message: str                        # the short failure text, or ''


@dataclass(frozen=True)
class StepStatus:
    """One step of "Run everything" (settings → dev.checks)."""

    name: str
    state: str                          # 'waiting', 'running', 'passed', 'failed', 'stopped' or 'timed out'
    exit_code: int | None               # None until it has finished


@dataclass(frozen=True)
class RunStatus:
    run_id: str
    what: str                           # what was run: the check, or the tests asked for
    state: str                          # 'running', 'passed', 'failed', 'stopped' or 'timed out'
    started: float                      # seconds since 1970
    finished: float | None
    steps: tuple[StepStatus, ...]
    outcomes: tuple[TestOutcome, ...]   # each test that finished so far, in the order they finished
    output: str                         # the printout after the offset asked for
    output_end: int                     # the offset to ask from next time


@dataclass(frozen=True)
class SmoothingStep:
    """What the object state tracker made of one picture, for one kind of object."""

    ts: float                           # when the picture was taken
    conf: float | None                  # the confidence of the box it picked, or None when there was none
    box_seen: bool                      # the on/off answer for this picture (on_confidence / off_confidence)
    votes_yes: int                      # how many of the last pictures said "seen"...
    votes_total: int                    # ...out of how many (up to window_pictures)
    seen: bool                          # the vote's answer
    center: tuple[float, float] | None  # where it is (the middle of the last few boxes)
    phase: str                          # the state after this picture: 'UNKNOWN', 'PRESENT' or 'ABSENT'
    heading: str | None                 # the state it is waiting to turn into, or None
    waiting_sec: float | None           # how long it has been waiting for that, or None


@dataclass(frozen=True)
class TableInfo:
    name: str
    columns: tuple[str, ...]
    rows: int
    sql: str                            # the CREATE TABLE statement


@dataclass(frozen=True)
class QueryResult:
    columns: tuple[str, ...]
    rows: tuple[tuple, ...]
    truncated: bool                     # True when there were more rows than settings → dev.sql_max_rows
    took_ms: float


@dataclass(frozen=True)
class DataFile:
    path: str                           # relative to the data folder, with / between folders
    bytes: int
    modified: float                     # seconds since 1970
    is_folder: bool


class RunAlreadyGoing(Exception):
    """A run was started while another one was still going: only one runs at a time."""


class QueryRefused(Exception):
    """A query that isn't one statement that only reads, or that ran too long. The message says why."""
