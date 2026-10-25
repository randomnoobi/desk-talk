"""Stand-ins for the developer page's tools (desktalk/ports.py: TestRunner, DatabaseBrowser, DataFiles,
SmoothingTraceView). They pass the same contracts as the real ones (tests/contracts/dev_contracts.py), but run
nothing, open no file and need no tracker."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import PurePosixPath

from desktalk.domain import (
    DataFile,
    QueryRefused,
    QueryResult,
    RunAlreadyGoing,
    RunStatus,
    SmoothingStep,
    StepStatus,
    TableInfo,
    TestItem,
    TestOutcome,
)
from tests.fakes.clock import FakeClock

CHECK = 'check'                                     # RunStatus.what for "Run everything"
READING_WORDS = ('SELECT', 'WITH', 'PRAGMA', 'EXPLAIN', 'VALUES')


class FakeTestRunner:
    """Knows the tests in `items` and runs nothing: a run finishes as soon as status() is called.

    `results`: test id → its outcome (a test not in it passes). `checks`: each "Run everything" step's name →
    the exit code it finishes with. `started` records every run started, as ('check', []) or ('tests', ids)."""

    def __init__(self, items: Sequence[TestItem] = (), results: Mapping[str, TestOutcome] | None = None,
                 checks: Mapping[str, int] | None = None, clock: FakeClock | None = None):
        self.items = list(items)
        self.results = dict(results or {})
        self.checks = dict(checks if checks is not None else {'Fast tests': 0})
        self.clock = clock or FakeClock()
        self.started: list[tuple[str, list[str]]] = []
        self._known: set[str] = set()
        self._runs: dict[str, RunStatus] = {}
        self._asked: dict[str, list[str]] = {}

    def collect(self) -> list[TestItem]:
        self._known = {item.id for item in self.items} | {item.file for item in self.items}
        return list(self.items)

    def start_check(self) -> str:
        self._refuse_while_running()
        self.started.append((CHECK, []))
        steps = tuple(StepStatus(name, 'running' if i == 0 else 'waiting', None) for i, name in enumerate(self.checks))
        return self._start(CHECK, steps, [])

    def start_tests(self, ids: list[str]) -> str:
        unknown = [i for i in ids if i not in self._known]
        if not ids or unknown:
            raise ValueError(f'not a collected test: {", ".join(unknown) or "(none asked for)"}')
        self._refuse_while_running()
        self.started.append(('tests', list(ids)))
        return self._start(' '.join(ids), (), list(ids))

    def status(self, run_id: str, output_from: int) -> RunStatus:
        run = self._runs[run_id]
        if run.state == 'running':
            run = self._finish(run)
            self._runs[run_id] = run
        return replace(run, output=run.output[output_from:], output_end=len(run.output))

    def stop(self, run_id: str) -> None:
        run = self._runs.get(run_id)
        if run is not None and run.state == 'running':
            steps = tuple(replace(s, state='stopped') if s.exit_code is None else s for s in run.steps)
            output = run.output + 'stopped\n'
            self._runs[run_id] = replace(run, state='stopped', finished=self.clock.now(), steps=steps,
                                         output=output, output_end=len(output))

    def _refuse_while_running(self) -> None:
        if any(run.state == 'running' for run in self._runs.values()):
            raise RunAlreadyGoing('A run is already going.')

    def _start(self, what: str, steps: tuple[StepStatus, ...], ids: list[str]) -> str:
        run_id = f'run-{len(self._runs) + 1}'
        self._runs[run_id] = RunStatus(run_id, what, 'running', self.clock.now(), None, steps, (), '', 0)
        self._asked[run_id] = ids
        return run_id

    def _finish(self, run: RunStatus) -> RunStatus:
        if run.what == CHECK:
            steps = tuple(StepStatus(name, 'passed' if code == 0 else 'failed', code)
                          for name, code in self.checks.items())
            output = ''.join(f'{s.name}: {s.state}\n' for s in steps)
            state = 'passed' if all(s.exit_code == 0 for s in steps) else 'failed'
            return replace(run, state=state, finished=self.clock.now(), steps=steps, output=output,
                           output_end=len(output))
        asked = self._asked[run.run_id]
        tests = [item.id for item in self.items if item.id in asked or item.file in asked]
        outcomes = tuple(self.results.get(i, TestOutcome(i, 'passed', 0.0, '')) for i in tests)
        counts = {kind: sum(o.outcome == kind for o in outcomes) for kind in ('passed', 'failed', 'skipped', 'error')}
        output = ''.join(f'{o.id} {o.outcome.upper()}\n' for o in outcomes)
        output += ', '.join(f'{n} {kind}' for kind, n in counts.items() if n) + '\n'
        state = 'failed' if counts['failed'] or counts['error'] else 'passed'
        return replace(run, state=state, finished=self.clock.now(), outcomes=outcomes, output=output,
                       output_end=len(output))


class FakeDatabaseBrowser:
    """`tables`: database name → its tables. `answers`: a query's text → what it gives back (any other query
    that only reads gives no rows). Like the real one, it refuses writes and more than one statement."""

    def __init__(self, tables: Mapping[str, Sequence[TableInfo]] | None = None,
                 answers: Mapping[str, QueryResult] | None = None):
        self._tables = {db: list(found) for db, found in (tables or {}).items()}
        self.answers = dict(answers or {})
        self.asked: list[tuple[str, str]] = []

    def databases(self) -> list[str]:
        return list(self._tables)

    def tables(self, db: str) -> list[TableInfo]:
        return list(self._tables[db])

    def query(self, db: str, sql: str) -> QueryResult:
        if db not in self._tables:
            raise KeyError(db)
        self.asked.append((db, sql))
        text = sql.strip().rstrip(';').strip()
        if not text or ';' in text:
            raise QueryRefused('not one statement')
        if text.split()[0].upper() not in READING_WORDS:
            raise QueryRefused('not a query that only reads')
        return self.answers.get(sql.strip(), QueryResult((), (), False, 0.0))


class FakeDataFiles:
    """`files`: path in the data folder (with / between folders) → the file's text. Folders come from the paths."""

    def __init__(self, files: Mapping[str, str] | None = None, modified: float = 1_760_000_000.0):
        self.files = dict(files or {})
        self.modified = modified

    def list(self) -> list[DataFile]:
        folders: dict[str, int] = {}
        for path, text in self.files.items():
            for parent in PurePosixPath(path).parents:
                if str(parent) != '.':
                    folders[str(parent)] = folders.get(str(parent), 0) + len(text.encode())
        found = [DataFile(folder, size, self.modified, True) for folder, size in folders.items()]
        found += [DataFile(path, len(text.encode()), self.modified, False) for path, text in self.files.items()]
        return sorted(found, key=lambda f: f.path)

    def preview(self, path: str) -> str:
        where = PurePosixPath(path)
        if where.is_absolute() or '..' in where.parts:
            raise ValueError(f'{path} is outside the data folder')
        if any(str(where) in map(str, PurePosixPath(p).parents) for p in self.files):
            raise ValueError(f'{path} is a folder')
        if str(where) not in self.files:
            raise FileNotFoundError(path)
        return self.files[str(where)]


def smoothing_step(ts: float, conf: float | None = 0.9, **change) -> SmoothingStep:
    """One picture's smoothing for a test: seen (PRESENT) unless `change` says otherwise."""
    seen = conf is not None
    step = SmoothingStep(ts=ts, conf=conf, box_seen=seen, votes_yes=int(seen), votes_total=1, seen=seen,
                         center=(0.5, 0.5) if seen else None, phase='PRESENT' if seen else 'ABSENT', heading=None,
                         waiting_sec=None)
    return replace(step, **change)


class FakeSmoothingTrace:
    """`steps`: label → its pictures' smoothing, oldest first."""

    def __init__(self, steps: Mapping[str, Sequence[SmoothingStep]] | None = None):
        self.steps = {label: list(found) for label, found in (steps or {}).items()}

    def traced_labels(self) -> list[str]:
        return list(self.steps)

    def trace(self, label: str) -> list[SmoothingStep]:
        return list(self.steps.get(label, []))
