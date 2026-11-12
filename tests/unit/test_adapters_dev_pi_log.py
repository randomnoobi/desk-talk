"""SshPiLog (desktalk/adapters/dev/pi_log.py) with `sh -c` standing in for ssh, so the "Pi" is this computer and
each source's command runs here. A FakeClock decides when the tab last asked and when a retry is due; the lines
themselves come from real child processes, so the tests wait (briefly) for them."""

from __future__ import annotations

import functools
import os
import shlex
import shutil
import tempfile
import time
from pathlib import Path

import pytest

from desktalk.adapters.dev.pi_log import MARKER, SshPiLog, time_in, unix_seconds
from desktalk.config import AppConfig, DevPiLogSettings, load_config
from desktalk.domain import PiLogPage
from tests.contracts.pi_log_contract import PiLogContract
from tests.fakes import FakeClock

ROOT = Path(__file__).resolve().parents[2]
LONGEST_WAIT_SEC = 5            # the lines come in well under a second; this only stops a stuck test
STOP_WAIT_SEC = 0.5
SH = ['sh', '-c']               # "ssh" here: runs the remote command on this computer
UNREACHABLE = ['sh', '-c', 'echo cannot reach the Pi >&2; exit 255', 'sh']     # fails before the remote command
STARTS = {
    'first': "printf 'one\\ntwo\\nthree\\n'; sleep 30",
    'second': "printf 'four\\n'; sleep 30",
}


@functools.cache
def app_config() -> AppConfig:
    """The real config files, without anyone's settings.local.yaml or .env."""
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        (root / 'config').mkdir()
        for name in ('settings.yaml', 'agents.yaml', 'protocols.yaml', 'prompts.yaml'):
            shutil.copy(ROOT / 'config' / name, root / 'config' / name)
        return load_config(root=root, environ={}, dotenv=root / '.env')


def make_settings(*, ssh=None, sources=None, **changes) -> DevPiLogSettings:
    real = app_config().dev.pi_log
    found = sources or {name: (start, start) for name, start in STARTS.items()}
    return DevPiLogSettings.model_validate({
        **real.model_dump(),
        'ssh': ssh or SH,
        'sources': [{'name': name, 'label': name.title(), 'start': start, 'resume': resume}
                    for name, (start, resume) in found.items()],
        **changes,
    })


def make_pi_log(clock=None, **changes) -> SshPiLog:
    return SshPiLog(make_settings(**changes), app_config().prompts.dev.pi_log, clock or FakeClock(),
                    stop_wait_sec=STOP_WAIT_SEC)


@pytest.fixture
def made():
    """Makes SshPiLogs and closes each at the end, so no child process outlives its test."""
    logs: list[SshPiLog] = []

    def make(clock=None, **changes) -> SshPiLog:
        logs.append(make_pi_log(clock, **changes))
        return logs[-1]

    yield make
    for pi_log in logs:
        pi_log.close()


def wait_for(ask, done) -> PiLogPage:
    """Call `ask()` until `done(page)` is true."""
    give_up = time.monotonic() + LONGEST_WAIT_SEC
    while True:
        page = ask()
        if done(page):
            return page
        assert time.monotonic() < give_up, f'still waiting, got {page}'
        time.sleep(0.01)


def texts(page: PiLogPage, kind: str = 'line') -> list[str]:
    return [line.text for line in page.lines if line.kind == kind]


def gone(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


def wait_until_gone(pid: int) -> None:
    give_up = time.monotonic() + LONGEST_WAIT_SEC
    while not gone(pid):       # an orphan is cleared away by the system a moment after it ends
        assert time.monotonic() < give_up, f'process {pid} is still there'
        time.sleep(0.01)


class TestSshPiLog(PiLogContract):
    @pytest.fixture
    def pi_log(self, made):
        return made()


# ── connecting ───────────────────────────────────────────────────────
def test_it_says_what_it_runs_and_hides_the_marker(made):
    words = app_config().prompts.dev.pi_log
    pi_log = made()
    page = wait_for(lambda: pi_log.read('first', 0), lambda p: 'three' in texts(p))
    notes = texts(page, 'note')
    assert notes[0] == words.connecting.format(command=shlex.join([*SH, STARTS['first']]))     # no marker in it
    assert words.connected in notes
    assert all(MARKER not in line.text for line in page.lines)


def test_the_pi_lines_get_the_time_they_came(made):
    clock = FakeClock()
    pi_log = made(clock)
    page = wait_for(lambda: pi_log.read('first', 0), lambda p: 'three' in texts(p))
    assert all(line.at == clock.now() for line in page.lines)


def test_a_missing_ssh_program_is_said_and_tried_again_later(made):
    clock = FakeClock()
    words = app_config().prompts.dev.pi_log
    pi_log = made(clock, ssh=['/no/such/ssh-program'])
    page = pi_log.read('first', 0)
    assert page.state == 'waiting'
    assert page.retry_at == clock.now() + make_settings().retry_start_sec
    notes = texts(page, 'note')
    assert any(note.startswith(words.cannot_start.split('{')[0]) and 'no/such/ssh-program' in note for note in notes)
    assert page.problem


def test_the_errors_become_problem_lines(made):
    pi_log = made(sources={'first': ("echo oops >&2; sleep 30", "sleep 30"), 'second': ('true', 'true')})
    page = wait_for(lambda: pi_log.read('first', 0), lambda p: 'oops' in texts(p, 'problem'))
    assert page.problem == 'oops'
    assert page.state == 'connected'


def test_long_lines_are_cut(made):
    pi_log = made(line_max_chars=10, sources={'first': ("printf 'abcdefghijklmnop\\r\\n'; sleep 30", 'sleep 30'),
                                              'second': ('true', 'true')})
    page = wait_for(lambda: pi_log.read('first', 0), lambda p: bool(texts(p)))
    assert texts(page) == ['abcdefghij']


# "ssh" whose lines end in \r\n, as they do through ssh -tt
CRLF_SSH = ['sh', '-c', 'sh -c "$0" | awk \'{printf "%s\\r\\n", $0; fflush()}\'']


def test_lines_ending_in_cr_lf_are_read_and_the_marker_still_works(made):
    words = app_config().prompts.dev.pi_log
    pi_log = made(ssh=CRLF_SSH)
    page = wait_for(lambda: pi_log.read('first', 0), lambda p: 'three' in texts(p))
    assert texts(page) == ['one', 'two', 'three']
    assert words.connected in texts(page, 'note')
    assert page.state == 'connected'


def test_a_line_without_its_newline_still_comes_at_the_end(made):
    pi_log = made(sources={'first': ("printf 'last'", 'sleep 30'), 'second': ('true', 'true')})
    page = wait_for(lambda: pi_log.read('first', 0), lambda p: p.state == 'waiting')
    assert texts(page) == ['last']


# ── losing the connection and picking up again ───────────────────────
STAMPED_START = ("printf 'camera-1  | 2026-11-11T10:00:00.123456789Z one\\n"
                 "camera-1  | 2026-11-11T10:00:01.000000000Z two\\n'; exit 3")
STAMPED_RESUME = ("printf 'since=%s\\n' {since}; printf 'camera-1  | 2026-11-11T10:00:01.000000000Z two\\n"
                  "camera-1  | 2026-11-11T10:00:02.5Z three\\n'; sleep 30")


def test_a_lost_connection_waits_and_says_why(made):
    clock = FakeClock()
    words = app_config().prompts.dev.pi_log
    pi_log = made(clock, sources={'first': (STAMPED_START, STAMPED_RESUME), 'second': ('true', 'true')})
    page = wait_for(lambda: pi_log.read('first', 0), lambda p: p.state == 'waiting')
    start = make_settings().retry_start_sec
    assert page.retry_at == clock.now() + start
    assert texts(page)[-1] == 'camera-1  | 2026-11-11T10:00:01.000000000Z two'
    lost = words.lost.format(reason=words.exit_code.format(code=3), seconds=f'{start:g}')
    assert texts(page, 'note')[-1] == lost
    assert page.problem == words.exit_code.format(code=3)


def test_it_picks_up_after_the_last_time_without_repeating_lines(made):
    clock = FakeClock()
    words = app_config().prompts.dev.pi_log
    pi_log = made(clock, sources={'first': (STAMPED_START, STAMPED_RESUME), 'second': ('true', 'true')})
    page = wait_for(lambda: pi_log.read('first', 0), lambda p: p.state == 'waiting')
    pi_log.tick(clock.now())            # not yet
    assert pi_log.read('first', page.end).lines == ()
    clock.advance(make_settings().retry_start_sec)
    pi_log.tick(clock.now())
    page = wait_for(lambda: pi_log.read('first', 0), lambda p: any('three' in text for text in texts(p)))
    assert texts(page) == ['camera-1  | 2026-11-11T10:00:00.123456789Z one',
                           'camera-1  | 2026-11-11T10:00:01.000000000Z two',
                           'since=1794391201.000000000',
                           'camera-1  | 2026-11-11T10:00:02.5Z three']
    resume = STAMPED_RESUME.replace('{since}', '1794391201.000000000')
    again = words.reconnecting.format(since='2026-11-11T10:00:01.000000000Z',
                                      command=f"sh -c {shlex.quote(resume)}")
    assert again in texts(page, 'note')
    assert page.state == 'connected' and page.problem == '' and page.retry_at is None


def test_with_no_time_seen_it_starts_again(made):
    clock = FakeClock()
    words = app_config().prompts.dev.pi_log
    pi_log = made(clock, sources={'first': ("printf 'plain\\n'; exit 1", 'echo resumed; sleep 30'),
                                  'second': ('true', 'true')})
    page = wait_for(lambda: pi_log.read('first', 0), lambda p: p.state == 'waiting')
    clock.advance(make_settings().retry_start_sec)
    pi_log.tick(clock.now())
    page = wait_for(lambda: pi_log.read('first', page.end), lambda p: 'plain' in texts(p))
    assert 'resumed' not in texts(page)
    assert texts(page, 'note')[0].startswith(words.connecting.split('{')[0])


def test_the_wait_doubles_up_to_the_most(made):
    clock = FakeClock()
    pi_log = made(clock, ssh=UNREACHABLE, retry_start_sec=2, retry_max_sec=5)
    waits = []
    after = 0
    for _ in range(4):
        page = wait_for(lambda after=after: pi_log.read('first', after), lambda p: p.state == 'waiting')
        after = page.end
        assert page.retry_at is not None
        waits.append(page.retry_at - clock.now())
        assert page.problem == 'cannot reach the Pi'
        clock.advance(waits[-1])
        pi_log.tick(clock.now())
    assert waits == [2, 4, 5, 5]


def test_the_wait_keeps_growing_when_it_ends_right_after_connecting(made):
    clock = FakeClock()
    pi_log = made(clock, retry_start_sec=2, retry_max_sec=30, connected_ok_sec=10,
                  sources={'first': ("echo hi; exit 1", "echo hi; exit 1"), 'second': ('true', 'true')})
    waits = []
    for _ in range(3):
        page = wait_for(lambda: pi_log.read('first', 0), lambda p: p.state == 'waiting')
        assert page.retry_at is not None
        waits.append(page.retry_at - clock.now())
        clock.advance(waits[-1])
        pi_log.tick(clock.now())
    assert waits == [2, 4, 8]         # it got through each time, but the command ended at once (folder missing...)


def test_the_wait_starts_small_again_after_a_connection_that_lasted(made, tmp_path):
    clock = FakeClock()
    gate = shlex.quote(str(tmp_path / 'end'))        # the command ends once this file is there
    ends = f"echo hi; while [ ! -e {gate} ]; do sleep 0.01; done; rm {gate}; exit 1"
    pi_log = made(clock, retry_start_sec=2, retry_max_sec=30, connected_ok_sec=10,
                  sources={'first': (ends, ends), 'second': ('true', 'true')})
    waits = []
    for lasted in (0, 0, 10):
        wait_for(lambda: pi_log.read('first', 0), lambda p: p.state == 'connected')
        clock.advance(lasted)
        (tmp_path / 'end').touch()
        page = wait_for(lambda: pi_log.read('first', 0), lambda p: p.state == 'waiting')
        assert page.retry_at is not None
        waits.append(page.retry_at - clock.now())
        clock.advance(waits[-1])
        pi_log.tick(clock.now())
    assert waits == [2, 4, 2]


# ── stopping ─────────────────────────────────────────────────────────
PID_START = 'sleep 30 & echo pid=$!; wait'


def child_pid(page: PiLogPage) -> int:
    return int(next(text for text in texts(page) if text.startswith('pid='))[4:])


def test_it_stops_once_the_tab_stops_asking(made):
    clock = FakeClock()
    words = app_config().prompts.dev.pi_log
    pi_log = made(clock, sources={'first': (PID_START, PID_START), 'second': ('true', 'true')})
    page = wait_for(lambda: pi_log.read('first', 0), lambda p: any(t.startswith('pid=') for t in texts(p)))
    idle = make_settings().stop_after_idle_sec
    clock.advance(idle)
    assert pi_log.tick(clock.now()) == make_settings().check_every_sec
    assert pi_log._page(0).state == 'connected'           # not quite yet
    clock.advance(1)
    pi_log.tick(clock.now())
    stopped = pi_log._page(page.end)
    assert stopped.state == 'stopped'
    assert texts(stopped, 'note') == [words.stopped.format(seconds=f'{idle:g}')]
    wait_until_gone(child_pid(page))


STAMPED_STAYS = STAMPED_START.replace('exit 3', 'sleep 30')
STAMPED_TEXTS = ['camera-1  | 2026-11-11T10:00:00.123456789Z one', 'camera-1  | 2026-11-11T10:00:01.000000000Z two']


def test_asking_again_after_stopping_picks_up_from_the_last_time(made):
    clock = FakeClock()
    words = app_config().prompts.dev.pi_log
    pi_log = made(clock, sources={'first': (STAMPED_STAYS, STAMPED_RESUME), 'second': ('true', 'true')})
    page = wait_for(lambda: pi_log.read('first', 0), lambda p: texts(p) == STAMPED_TEXTS)
    clock.advance(make_settings().stop_after_idle_sec + 1)
    pi_log.tick(clock.now())
    again = wait_for(lambda: pi_log.read('first', page.end), lambda p: any('three' in t for t in texts(p)))
    assert texts(again) == ['since=1794391201.000000000', 'camera-1  | 2026-11-11T10:00:02.5Z three']   # no repeats
    stopped, reconnecting = texts(again, 'note')[:2]
    assert stopped == words.stopped.format(seconds=f'{make_settings().stop_after_idle_sec:g}')
    assert reconnecting.startswith(words.reconnecting.split('{')[0])
    assert all(line.number > page.end for line in again.lines)
    assert texts(pi_log.read('first', 0))[:2] == STAMPED_TEXTS     # what came before is still kept
    assert not again.dropped


def test_asking_again_after_close_picks_up_too(made):
    pi_log = made(sources={'first': (STAMPED_STAYS, STAMPED_RESUME), 'second': ('true', 'true')})
    page = wait_for(lambda: pi_log.read('first', 0), lambda p: texts(p) == STAMPED_TEXTS)
    pi_log.close()
    again = wait_for(lambda: pi_log.read('first', page.end), lambda p: any('three' in t for t in texts(p)))
    assert texts(again) == ['since=1794391201.000000000', 'camera-1  | 2026-11-11T10:00:02.5Z three']


def test_another_source_after_stopping_starts_fresh(made):
    clock = FakeClock()
    pi_log = made(clock, sources={'first': (STAMPED_STAYS, STAMPED_RESUME),
                                  'second': ("printf 'four\\n'; sleep 30", 'echo resumed; sleep 30')})
    page = wait_for(lambda: pi_log.read('first', 0), lambda p: texts(p) == STAMPED_TEXTS)
    clock.advance(make_settings().stop_after_idle_sec + 1)
    pi_log.tick(clock.now())
    wait_for(lambda: pi_log.read('second', page.end), lambda p: 'four' in texts(p))
    assert texts(pi_log.read('second', 0)) == ['four']


def test_close_ends_the_sleeping_child(made):
    pi_log = made(sources={'first': (PID_START, PID_START), 'second': ('true', 'true')})
    page = wait_for(lambda: pi_log.read('first', 0), lambda p: any(t.startswith('pid=') for t in texts(p)))
    started = time.monotonic()
    pi_log.close()
    assert time.monotonic() - started < STOP_WAIT_SEC       # it ended when asked, without being killed
    assert pi_log._page(page.end).state == 'stopped'
    wait_until_gone(child_pid(page))


def test_switching_sources_ends_the_old_child(made):
    pi_log = made(sources={'first': (PID_START, PID_START), 'second': ("printf 'four\\n'; sleep 30", 'sleep 30')})
    page = wait_for(lambda: pi_log.read('first', 0), lambda p: any(t.startswith('pid=') for t in texts(p)))
    wait_for(lambda: pi_log.read('second', page.end), lambda p: 'four' in texts(p))
    wait_until_gone(child_pid(page))


def test_the_old_connection_never_writes_into_the_new_one(made):
    pi_log = made(sources={'first': ("trap '' TERM; sleep 0.2; echo late; sleep 30", 'sleep 30'),
                           'second': ("printf 'four\\n'; sleep 30", 'sleep 30')})
    wait_for(lambda: pi_log.read('first', 0), lambda p: p.state == 'connected')
    page = wait_for(lambda: pi_log.read('second', 0), lambda p: 'four' in texts(p))
    time.sleep(0.05)
    assert 'late' not in texts(pi_log.read('second', page.end)) + texts(pi_log.read('second', 0))


# ── keeping lines ────────────────────────────────────────────────────
def test_only_the_newest_lines_are_kept(made):
    many = "printf 'l1\\nl2\\nl3\\nl4\\nl5\\nl6\\nl7\\nl8\\nl9\\nl10\\n'; sleep 30"
    pi_log = made(first_lines=3, keep_lines=5, sources={'first': (many, many), 'second': ('true', 'true')})
    page = wait_for(lambda: pi_log.read('first', 0), lambda p: 'l10' in texts(p))
    assert texts(page) == ['l6', 'l7', 'l8', 'l9', 'l10']
    assert page.dropped
    assert not pi_log.read('first', page.lines[0].number - 1).dropped
    assert pi_log.read('first', page.lines[0].number - 2).dropped


def test_only_the_newest_times_are_remembered_for_leaving_out_repeats(made):
    lines = ''.join(f'2026-11-11T10:00:0{n}Z l{n}\\n' for n in range(1, 7))
    pi_log = made(first_lines=3, keep_lines=3,
                  sources={'first': (f"printf '{lines}'; sleep 30", 'sleep 30'), 'second': ('true', 'true')})
    wait_for(lambda: pi_log.read('first', 0), lambda p: any('l6' in t for t in texts(p)))
    assert [key[1] for key in pi_log._recent] == ['2026-11-11T10:00:04Z l4', '2026-11-11T10:00:05Z l5',
                                                  '2026-11-11T10:00:06Z l6']


def test_the_first_lines_go_into_the_start_command(made):
    pi_log = made(first_lines=7, sources={'first': ("echo tail={first_lines}; sleep 30", 'sleep 30'),
                                          'second': ('true', 'true')})
    page = wait_for(lambda: pi_log.read('first', 0), lambda p: bool(texts(p)))
    assert texts(page) == ['tail=7']


# ── the Pi's time ────────────────────────────────────────────────────
@pytest.mark.parametrize(('text', 'expected'), [
    ('1970-01-01T00:00:00Z', (0, 0)),
    ('2026-11-11T10:00:00.123456789Z', (1_794_391_200, 123_456_789)),
    ('2026-11-11T10:00:00.5Z', (1_794_391_200, 500_000_000)),
    ('2026-11-11T12:00:00+02:00', (1_794_391_200, 0)),
    ('2026-11-11T05:30:00.000001-0430', (1_794_391_200, 1_000)),
])
def test_the_pi_time_is_read_as_a_real_time(text, expected):
    assert time_in(text) == (expected, text)


@pytest.mark.parametrize(('line', 'expected'), [
    ('camera-1  | 2026-11-11T10:00:00.5Z hello', ((1_794_391_200, 500_000_000), '2026-11-11T10:00:00.5Z')),
    ('2026-11-11T10:00:00.123456+0000 pi desk-talk-camera[42]: hello',
     ((1_794_391_200, 123_456_000), '2026-11-11T10:00:00.123456+0000')),
    ('no time here', None),
])
def test_the_pi_time_is_found_in_a_line(line, expected):
    assert time_in(line) == expected


@pytest.mark.parametrize(('when', 'expected'), [
    ((1_794_391_201, 0), '1794391201.000000000'),
    ((1_794_391_200, 123_456_789), '1794391200.123456789'),
    ((1_794_391_200, 5), '1794391200.000000005'),
])
def test_since_is_seconds_since_1970_without_rounding(when, expected):
    assert unix_seconds(when) == expected


def test_a_time_that_makes_no_sense_is_none():
    assert time_in('2026-13-45T99:00:00Z') is None
    assert time_in('not a time') is None
