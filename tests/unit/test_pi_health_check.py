"""raspberry_pi/health_check.py: Docker restarts nothing for "unhealthy", so this ends a hung program itself."""

from __future__ import annotations

import signal
from pathlib import Path

import pytest

from raspberry_pi.health_check import Settings, check, container_age_sec, main_processes

SETTINGS = Settings(url='http://127.0.0.1:1/health', answer_wait_sec=1, grace_sec=60, fails_before_restart=3)
TICKS = 100


def fake_proc(tmp_path: Path, *, up_sec: float, started_sec: float, children: tuple[int, ...] = (7,)) -> Path:
    proc = tmp_path / 'proc'
    proc.mkdir()
    (proc / 'uptime').write_text(f'{up_sec} 1.0\n')
    stat = lambda pid, ppid, start: f'{pid} (a (strange) name) S {ppid} ' + ' '.join(  # noqa: E731
        ['0'] * 17) + f' {int(start * TICKS)} 0 0\n'
    (proc / '1').mkdir()
    (proc / '1' / 'stat').write_text(stat(1, 0, started_sec))
    for pid in children:
        (proc / str(pid)).mkdir()
        (proc / str(pid) / 'stat').write_text(stat(pid, 1, started_sec))
    (proc / '99').mkdir()
    (proc / '99' / 'stat').write_text(stat(99, 0, up_sec))    # the health check itself: Docker started it, not PID 1
    (proc / 'self').mkdir()                                   # not a number, so never a process
    return proc


class Rig:
    def __init__(self, tmp_path: Path, *, answers: bool, up_sec: float = 1000.0):
        self.proc = fake_proc(tmp_path, up_sec=up_sec, started_sec=0)
        self.counter = tmp_path / 'failures'
        self.answers = answers
        self.killed: list[tuple[int, int]] = []

    def run(self) -> int:
        return check(SETTINGS, proc=self.proc, counter=self.counter, ask=lambda url, wait: self.answers,
                     kill=lambda pid, sig: self.killed.append((pid, sig)), ticks_per_sec=TICKS)


def test_an_answer_is_healthy_and_clears_the_count(tmp_path):
    rig = Rig(tmp_path, answers=True)
    rig.counter.write_text('2')
    assert rig.run() == 0
    assert not rig.counter.exists() and rig.killed == []


def test_failures_are_counted_and_only_past_the_number_end_the_program(tmp_path):
    rig = Rig(tmp_path, answers=False)
    assert [rig.run(), rig.run()] == [1, 1]
    assert rig.counter.read_text() == '2' and rig.killed == []
    assert rig.run() == 1
    assert rig.killed == [(7, signal.SIGKILL)]
    assert not rig.counter.exists()


def test_failures_soon_after_a_start_dont_count(tmp_path):
    rig = Rig(tmp_path, answers=False, up_sec=SETTINGS.grace_sec - 1)
    for _ in range(SETTINGS.fails_before_restart * 3):
        assert rig.run() == 1
    assert rig.killed == [] and not rig.counter.exists()


def test_a_count_left_from_before_a_restart_is_dropped_in_the_grace_time(tmp_path):
    rig = Rig(tmp_path, answers=False, up_sec=SETTINGS.grace_sec - 1)
    rig.counter.write_text('99')
    rig.run()
    assert not rig.counter.exists() and rig.killed == []


def test_one_answer_in_between_starts_the_count_again(tmp_path):
    rig = Rig(tmp_path, answers=False)
    rig.run()
    rig.run()
    rig.answers = True
    rig.run()
    rig.answers = False
    rig.run()
    rig.run()
    assert rig.killed == []


def test_a_broken_count_file_counts_as_none(tmp_path):
    rig = Rig(tmp_path, answers=False)
    rig.counter.write_text('nonsense')
    assert rig.run() == 1
    assert rig.counter.read_text() == '1'


def test_with_no_init_process_it_asks_pid_1_to_stop(tmp_path):
    rig = Rig(tmp_path, answers=False)
    (rig.proc / '7' / 'stat').unlink()
    (rig.proc / '7').rmdir()
    rig.counter.write_text('2')
    rig.run()
    assert rig.killed == [(1, signal.SIGTERM)]


def test_the_age_comes_from_when_pid_1_started(tmp_path):
    proc = fake_proc(tmp_path, up_sec=500, started_sec=200)
    assert container_age_sec(proc, TICKS) == pytest.approx(300)


def test_only_the_children_of_pid_1_are_its_main_program(tmp_path):
    assert main_processes(fake_proc(tmp_path, up_sec=5, started_sec=0, children=(7, 12))) == [7, 12]


def test_settings_come_from_the_environment():
    settings = Settings.from_env({'HEALTH_URL': 'http://x/', 'HEALTH_ANSWER_WAIT_SEC': '4', 'HEALTH_GRACE_SEC': '60',
                                  'HEALTH_FAILS_BEFORE_RESTART': '5'})
    assert settings == Settings('http://x/', 4.0, 60.0, 5)
