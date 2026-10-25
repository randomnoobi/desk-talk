"""What the developer page's tools must do (desktalk/ports.py: TestRunner, DatabaseBrowser, DataFiles,
SmoothingTraceView). The fakes in tests/fakes/dev.py run these in test_fakes_pass_contracts.py, and each real
one in its own unit test file (tests/unit/test_adapters_dev_*.py, test_core_object_states_tracker.py).

(The classes aren't named Test... so pytest only runs them through a subclass that gives the fixtures.)"""

from __future__ import annotations

import time

import pytest

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

FINISHED = ('passed', 'failed', 'stopped', 'timed out')
OUTCOMES = ('passed', 'failed', 'skipped', 'error')
LONGEST_RUN_SEC = 120          # a real run in these tests takes a few seconds; this only stops a stuck test


def wait_until_finished(runner, run_id: str) -> RunStatus:
    """Ask for the run's status until it isn't running any more."""
    give_up = time.monotonic() + LONGEST_RUN_SEC
    while True:
        status = runner.status(run_id, 0)
        if status.state != 'running':
            return status
        assert time.monotonic() < give_up, f'run {run_id} still running after {LONGEST_RUN_SEC} s'
        time.sleep(0.05)


class RunnerContract:
    """For a TestRunner. Subclass this and give a `runner` fixture: a TestRunner whose collect() finds at least
    one test. Its first test should take about a second, so a run of it is still going when the next start
    comes; the checks (settings → dev.checks) should be quick."""

    def test_collect_gives_each_test_with_its_file(self, runner):
        items = runner.collect()
        assert items
        for item in items:
            assert isinstance(item, TestItem)
            assert '::' in item.id and item.id.startswith(item.file)
            assert isinstance(item.markers, tuple)

    def test_a_second_start_waits_its_turn(self, runner):
        first = runner.start_tests([runner.collect()[0].id])
        with pytest.raises(RunAlreadyGoing):
            runner.start_check()
        with pytest.raises(RunAlreadyGoing):
            runner.start_tests([runner.collect()[0].id])
        runner.stop(first)
        wait_until_finished(runner, first)

    def test_an_unknown_test_id_is_refused_and_nothing_starts(self, runner):
        known = runner.collect()[0].id
        with pytest.raises(ValueError):
            runner.start_tests(['tests/x.py::nope; rm -rf /'])
        run_id = runner.start_tests([known])          # nothing is going, so this one may start
        wait_until_finished(runner, run_id)

    def test_a_finished_run_says_how_each_test_went(self, runner):
        test = runner.collect()[0]
        run_id = runner.start_tests([test.id])
        status = wait_until_finished(runner, run_id)
        assert isinstance(status, RunStatus)
        assert status.run_id == run_id
        assert status.state in ('passed', 'failed')
        assert status.finished is not None and status.finished >= status.started
        assert [o.id for o in status.outcomes] == [test.id]
        for outcome in status.outcomes:
            assert isinstance(outcome, TestOutcome)
            assert outcome.outcome in OUTCOMES
            assert outcome.duration_sec >= 0
            assert outcome.outcome != 'passed' or outcome.message == ''

    def test_the_output_comes_in_pieces(self, runner):
        run_id = runner.start_tests([runner.collect()[0].id])
        done = wait_until_finished(runner, run_id)
        assert done.output and done.output_end >= len(done.output)
        cut = min(3, len(done.output))
        assert runner.status(run_id, done.output_end - len(done.output) + cut).output == done.output[cut:]
        later = runner.status(run_id, done.output_end)
        assert (later.output, later.output_end) == ('', done.output_end)

    def test_the_check_runs_every_step(self, runner):
        run_id = runner.start_check()
        status = wait_until_finished(runner, run_id)
        assert status.steps
        for step in status.steps:
            assert isinstance(step, StepStatus)
            assert step.state in ('passed', 'failed')
            assert isinstance(step.exit_code, int)
        every_step_passed = all(step.exit_code == 0 for step in status.steps)
        assert status.state == ('passed' if every_step_passed else 'failed')

    def test_a_stopped_run_lets_the_next_one_start(self, runner):
        run_id = runner.start_tests([runner.collect()[0].id])
        runner.stop(run_id)
        assert wait_until_finished(runner, run_id).state in FINISHED
        wait_until_finished(runner, runner.start_check())

    def test_close_stops_a_run_still_going(self, runner):
        run_id = runner.start_tests([runner.collect()[0].id])
        runner.close()
        assert wait_until_finished(runner, run_id).state in FINISHED
        runner.close()                                  # nothing going: nothing to do

    def test_an_unknown_run_is_a_key_error(self, runner):
        with pytest.raises(KeyError):
            runner.status('no-such-run', 0)


class DatabaseBrowserContract:
    """For a DatabaseBrowser. Subclass this and give a `browser` fixture: a DatabaseBrowser whose first database's
    first table has at least one row (and fewer than the row cap), so `SELECT * FROM <table>` gives them all."""

    @staticmethod
    def first_table(browser) -> tuple[str, TableInfo]:
        db = browser.databases()[0]
        return db, browser.tables(db)[0]

    def test_tables_say_their_columns_rows_and_how_they_were_made(self, browser):
        dbs = browser.databases()
        assert dbs and all(isinstance(db, str) for db in dbs)
        for table in browser.tables(dbs[0]):
            assert isinstance(table, TableInfo)
            assert table.columns and table.rows >= 0
            assert 'CREATE' in table.sql.upper()

    def test_a_select_gives_the_rows(self, browser):
        db, table = self.first_table(browser)
        result = browser.query(db, f'SELECT * FROM {table.name}')
        assert isinstance(result, QueryResult)
        assert result.columns == table.columns
        assert len(result.rows) == table.rows >= 1
        assert all(isinstance(row, tuple) and len(row) == len(table.columns) for row in result.rows)
        assert result.truncated is False
        assert result.took_ms >= 0

    @pytest.mark.parametrize('sql', ['DELETE FROM {table}', 'DROP TABLE {table}', 'SELECT 1; DELETE FROM {table}'])
    def test_anything_that_writes_is_refused(self, browser, sql):
        db, table = self.first_table(browser)
        with pytest.raises(QueryRefused):
            browser.query(db, sql.format(table=table.name))
        assert self.first_table(browser)[1].rows == table.rows

    def test_an_unknown_database_is_a_key_error(self, browser):
        with pytest.raises(KeyError):
            browser.tables('no_such_database')
        with pytest.raises(KeyError):
            browser.query('no_such_database', 'SELECT 1')


class DataFilesContract:
    """For a DataFiles. Subclass this and give a `data_files` fixture: one whose data folder has a folder `logs`
    holding `notes.txt`, which says `hello desk` (it may hold other things too)."""

    def test_the_list_shows_files_and_folders_inside(self, data_files):
        found = {f.path: f for f in data_files.list()}
        assert all(isinstance(f, DataFile) for f in found.values())
        assert all(not path.startswith('/') and '..' not in path.split('/') for path in found)
        assert found['logs/notes.txt'].bytes == len('hello desk')
        assert found['logs/notes.txt'].is_folder is False
        assert found['logs'].is_folder is True

    def test_a_preview_reads_the_file(self, data_files):
        assert data_files.preview('logs/notes.txt') == 'hello desk'

    @pytest.mark.parametrize('path', ['../x', '../../.env', '/etc/passwd', 'logs/../../x'])
    def test_paths_outside_the_folder_are_refused(self, data_files, path):
        with pytest.raises(ValueError):
            data_files.preview(path)

    def test_a_folder_has_no_preview(self, data_files):
        with pytest.raises(ValueError):
            data_files.preview('logs')

    def test_a_missing_file(self, data_files):
        with pytest.raises(FileNotFoundError):
            data_files.preview('logs/missing.txt')


class SmoothingTraceContract:
    """For a SmoothingTraceView. Subclass this and give a `view` fixture: one that has seen at least two pictures
    with a `cup` and one with a `pen/pencil`."""

    def test_an_unknown_label_has_an_empty_trace(self, view):
        assert view.trace('nothing') == []

    def test_every_object_seen_is_traced(self, view):
        assert {'cup', 'pen/pencil'} <= set(view.traced_labels())
        assert view.trace('pen/pencil')

    def test_the_trace_is_oldest_first(self, view):
        steps = view.trace('cup')
        assert len(steps) >= 2
        assert all(isinstance(step, SmoothingStep) for step in steps)
        assert [s.ts for s in steps] == sorted(s.ts for s in steps)
        for step in steps:
            assert step.phase in ('UNKNOWN', 'PRESENT', 'ABSENT')
            assert 0 <= step.votes_yes <= step.votes_total

    def test_the_trace_is_a_copy(self, view):
        view.trace('cup').clear()
        assert view.trace('cup')
