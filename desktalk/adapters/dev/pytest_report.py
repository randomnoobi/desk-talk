"""
A pytest plugin for the developer page's test runner (test_runner.py): one JSON line per finished test.

The runner adds `-p desktalk.adapters.dev.pytest_report` to every pytest it starts and sets the environment
variable DESKTALK_DEV_REPORT to a file. Each finished test adds one line to that file:

    {"id": "tests/unit/test_x.py::test_y", "outcome": "failed", "duration_sec": 0.01, "message": "assert 1 == 2"}

so the page sees each test as soon as it finishes. Without the variable it does nothing.

This file runs inside the child pytest, so it imports neither desktalk nor pytest: only the standard library.
"""

from __future__ import annotations

import json
import os
from typing import Any

REPORT_FILE = 'DESKTALK_DEV_REPORT'     # the environment variable with the file to add the lines to
ERROR_LINE = 'E '                       # pytest starts the lines that say what went wrong with this


def pytest_runtest_logreport(report: Any) -> None:
    """pytest calls this three times per test: for its setup, the test itself ('call') and its teardown."""
    path = os.environ.get(REPORT_FILE)
    if not path:
        return
    outcome = _outcome(report)
    if outcome is None:
        return
    line = {
        'id': report.nodeid,
        'outcome': outcome,
        'duration_sec': float(report.duration),
        'message': '' if outcome == 'passed' else _message(report),
    }
    with open(path, 'a', encoding='utf-8') as file:
        file.write(json.dumps(line) + '\n')


def _outcome(report: Any) -> str | None:
    """'passed', 'failed', 'skipped' or 'error', or None when this report doesn't finish the test."""
    if report.when == 'call':
        return report.outcome
    if report.outcome == 'failed':                  # the setup or teardown went wrong
        return 'error'
    if report.when == 'setup' and report.outcome == 'skipped':
        return 'skipped'
    return None


def _message(report: Any) -> str:
    """The short failure text: the first line pytest marks with 'E' (e.g. 'assert 1 == 2' or 'ValueError: boom'),
    else the last line it printed."""
    if isinstance(report.longrepr, tuple) and len(report.longrepr) == 3:     # a skip: (file, line, reason)
        return str(report.longrepr[2])
    lines = [line for line in str(report.longreprtext).splitlines() if line.strip()]
    marked = [line[len(ERROR_LINE):].strip() for line in lines if line.startswith(ERROR_LINE)]
    if marked:
        return marked[0]
    return lines[-1].strip() if lines else ''
