"""PytestRunner (desktalk/adapters/dev/test_runner.py) on a small pytest project written into a temp folder.

The project's tests run with this Python (sys.executable -m pytest), so neither uv nor the real tests are needed.
Its first test waits a little, so a run of it is still going when a test tries to start a second one."""

from __future__ import annotations

import functools
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

import pytest

from desktalk.adapters.dev import PytestRunner
from desktalk.config import AppConfig, DevCheck, load_config
from desktalk.domain import RunAlreadyGoing
from desktalk.runtime import SystemClock
from tests.contracts.dev_contracts import RunnerContract, wait_until_finished
from tests.fakes import FakeClock

ROOT = Path(__file__).resolve().parents[2]
WAIT_SEC = 2                    # how long the project's first test takes
PROJECT = {
    'pytest.ini': '[pytest]\nmarkers =\n    slow: takes a while\n',
    'test_basic.py': (
        'import time\n'
        '\n'
        'def test_passes():\n'
        f'    time.sleep({WAIT_SEC})\n'
        '\n'
        'def test_fails():\n'
        '    assert 1 == 2\n'
    ),
    'test_slow.py': (
        'import pytest\n'
        '\n'
        '@pytest.mark.slow\n'
        'def test_takes_long():\n'
        '    pass\n'
    ),
}
QUICK_CHECKS = (('Fine', 0), ('Broken', 1), ('Fine again', 0))


@functools.cache
def app_config() -> AppConfig:
    """The real config files, without anyone's settings.local.yaml or .env."""
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        (root / 'config').mkdir()
        for name in ('settings.yaml', 'agents.yaml', 'protocols.yaml', 'prompts.yaml'):
            shutil.copy(ROOT / 'config' / name, root / 'config' / name)
        return load_config(root=root, environ={}, dotenv=root / '.env')


def make_project(folder: Path) -> Path:
    for name, text in PROJECT.items():
        (folder / name).write_text(text)
    return folder


def make_runner(project: Path, clock=None, **changes) -> PytestRunner:
    config = app_config()
    checks = tuple(DevCheck(name=name, command=(sys.executable, '-c', f'exit({code})'))
                   for name, code in QUICK_CHECKS)
    settings = config.dev.model_copy(update={
        'test_command': (sys.executable, '-m', 'pytest', '-q'),
        'checks': checks,
        **changes,
    })
    return PytestRunner(settings, config.prompts.dev, project, clock or SystemClock(),
                        stop_wait_sec=config.supervisor.stop_wait_sec)


@pytest.fixture
def project(tmp_path) -> Path:
    return make_project(tmp_path)


def test_collect_lists_every_test_with_its_marks(project):
    items = make_runner(project).collect()
    assert [item.id for item in items] == [
        'test_basic.py::test_passes', 'test_basic.py::test_fails', 'test_slow.py::test_takes_long']
    assert {item.id: item.markers for item in items}['test_slow.py::test_takes_long'] == ('slow',)
    assert {item.id: item.markers for item in items}['test_basic.py::test_passes'] == ()
    assert [item.file for item in items] == ['test_basic.py', 'test_basic.py', 'test_slow.py']


def test_collect_leaves_out_what_collect_markers_leaves_out(project):
    items = make_runner(project, collect_markers='not slow').collect()
    assert [item.id for item in items] == ['test_basic.py::test_passes', 'test_basic.py::test_fails']


def test_running_one_file_reports_each_test(project):
    runner = make_runner(project)
    runner.collect()
    run_id = runner.start_tests(['test_basic.py'])
    status = wait_until_finished(runner, run_id)
    assert status.state == 'failed'
    assert status.what == 'test_basic.py'
    assert status.steps == ()
    outcomes = {o.id: o for o in status.outcomes}
    assert {o.outcome for o in status.outcomes} == {'passed', 'failed'}
    assert outcomes['test_basic.py::test_passes'].message == ''
    assert outcomes['test_basic.py::test_passes'].duration_sec >= WAIT_SEC * 0.9
    assert 'assert 1 == 2' in outcomes['test_basic.py::test_fails'].message
    assert '1 failed' in status.output


def test_each_test_shows_up_as_soon_as_it_finishes(tmp_path):
    project = make_project(tmp_path)
    (project / 'test_basic.py').write_text(
        'import pathlib, time\n'
        '\n'
        'def test_first():\n'
        '    pass\n'
        '\n'
        'def test_second():\n'
        "    while not pathlib.Path('go').exists():\n"
        '        time.sleep(0.05)\n'
    )
    runner = make_runner(project)
    runner.collect()
    run_id = runner.start_tests(['test_basic.py'])
    give_up = time.monotonic() + 30
    while not runner.status(run_id, 0).outcomes:
        assert time.monotonic() < give_up
        time.sleep(0.05)
    status = runner.status(run_id, 0)
    assert status.state == 'running'
    assert [o.id for o in status.outcomes] == ['test_basic.py::test_first']
    (project / 'go').touch()
    assert [o.outcome for o in wait_until_finished(runner, run_id).outcomes] == ['passed', 'passed']


def test_output_comes_in_pieces(project):
    runner = make_runner(project)
    runner.collect()
    run_id = runner.start_tests(['test_slow.py'])
    first = runner.status(run_id, 0)
    done = wait_until_finished(runner, run_id)
    second = runner.status(run_id, first.output_end)
    assert first.output + second.output == done.output
    assert second.output_end == done.output_end == len(done.output)


def test_only_the_newest_output_is_kept(project):
    runner = make_runner(project, output_keep_kb=1)
    runner.collect()
    (project / 'test_slow.py').write_text('def test_takes_long():\n    print("x" * 5000)\n    assert False\n')
    run_id = runner.start_tests(['test_slow.py::test_takes_long'])
    done = wait_until_finished(runner, run_id)
    assert len(done.output) <= 1024
    assert done.output_end > len(done.output)
    assert '1 failed' in done.output                                    # the end (the summary) is kept
    assert runner.status(run_id, done.output_end - 10).output == done.output[-10:]


def test_a_second_run_waits_its_turn(project):
    runner = make_runner(project)
    runner.collect()
    run_id = runner.start_tests(['test_basic.py::test_passes'])
    with pytest.raises(RunAlreadyGoing, match='A run is already going.'):
        runner.start_tests(['test_slow.py'])
    with pytest.raises(RunAlreadyGoing):
        runner.start_check()
    wait_until_finished(runner, run_id)
    wait_until_finished(runner, runner.start_tests(['test_slow.py']))


def test_unknown_test_id_is_refused(project):
    runner = make_runner(project)
    runner.collect()
    with pytest.raises(ValueError, match='nope; rm -rf'):
        runner.start_tests(['tests/x.py::nope; rm -rf /'])
    with pytest.raises(ValueError):
        runner.start_tests([])
    with pytest.raises(KeyError):
        runner.status('run-1', 0)


def test_nothing_runs_before_the_tests_are_collected(project):
    with pytest.raises(ValueError):
        make_runner(project).start_tests(['test_slow.py'])


def test_a_run_that_takes_too_long_is_stopped(project):
    (project / 'test_hangs.py').write_text(
        'import os, pathlib, time\n'
        '\n'
        'def test_hangs():\n'
        "    pathlib.Path('pid').write_text(str(os.getpid()))\n"
        '    time.sleep(60)\n'
    )
    clock = FakeClock()
    runner = make_runner(project, clock, run_timeout_min=1 / 60)
    runner.collect()
    run_id = runner.start_tests(['test_hangs.py::test_hangs'])
    give_up = time.monotonic() + 30
    while not (project / 'pid').exists() or not (project / 'pid').read_text():
        assert time.monotonic() < give_up
        time.sleep(0.05)
    assert runner.status(run_id, 0).state == 'running'
    clock.advance(2)
    status = runner.status(run_id, 0)
    assert status.state == 'timed out'
    assert status.finished == clock.now()
    with pytest.raises(ProcessLookupError):
        os.kill(int((project / 'pid').read_text()), 0)
    assert runner.status(run_id, 0).state == 'timed out'      # and it stays that way


def test_a_stopped_run_says_so(project):
    runner = make_runner(project)
    runner.collect()
    run_id = runner.start_tests(['test_basic.py::test_passes'])
    runner.stop(run_id)
    assert runner.status(run_id, 0).state == 'stopped'
    runner.stop(run_id)                                         # a finished run is left alone
    runner.stop('no-such-run')
    assert wait_until_finished(runner, run_id).state == 'stopped'


def test_the_check_runs_every_step(project):
    runner = make_runner(project)
    status = wait_until_finished(runner, runner.start_check())
    assert [(s.name, s.state, s.exit_code) for s in status.steps] == [
        ('Fine', 'passed', 0), ('Broken', 'failed', 1), ('Fine again', 'passed', 0)]
    assert status.state == 'failed'
    assert status.what == 'check'
    assert status.outcomes == ()


def test_a_stopped_check_skips_the_steps_left(project):
    runner = make_runner(project, checks=(
        DevCheck(name='Slow', command=(sys.executable, '-c', 'import time; time.sleep(60)')),
        DevCheck(name='Next', command=(sys.executable, '-c', 'exit(0)'))))
    run_id = runner.start_check()
    runner.stop(run_id)
    status = wait_until_finished(runner, run_id)
    assert status.state == 'stopped'
    assert [s.state for s in status.steps] == ['stopped', 'stopped']


def test_a_check_that_runs_pytest_reports_each_test(project):
    runner = make_runner(project, checks=(
        DevCheck(name='Tests', command=(sys.executable, '-m', 'pytest', '-q', 'test_slow.py')),))
    status = wait_until_finished(runner, runner.start_check())
    assert [(o.id, o.outcome) for o in status.outcomes] == [('test_slow.py::test_takes_long', 'passed')]
    assert status.state == 'passed'


class TestPytestRunnerPassesRunnerContract(RunnerContract):
    @pytest.fixture
    def runner(self, tmp_path):
        return make_runner(make_project(tmp_path))
