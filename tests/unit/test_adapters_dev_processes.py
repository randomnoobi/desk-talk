"""desktalk/adapters/dev/processes.py: ending a child process and everything it started, and reading its
printout as text as soon as it comes. Real child processes (`sh -c`), so these wait (briefly) for them."""

from __future__ import annotations

import os
import signal
import subprocess
import time

from desktalk.adapters.dev.processes import end_process, signal_group, text_of

STOP_WAIT_SEC = 0.5
LONGEST_WAIT_SEC = 5            # only stops a stuck test


def start(script: str) -> subprocess.Popen[bytes]:
    return subprocess.Popen(['sh', '-c', script], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            start_new_session=True)


def gone(pid: int) -> bool:
    give_up = time.monotonic() + LONGEST_WAIT_SEC
    while time.monotonic() < give_up:       # an orphan is cleared away by the system a moment after it ends
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.01)
    return False


def first_line(process: subprocess.Popen[bytes]) -> str:
    assert process.stdout is not None
    return process.stdout.readline().decode().strip()


def test_ending_a_process_ends_what_it_started_too():
    process = start('sleep 30 & echo $!; wait')
    child = int(first_line(process))
    started = time.monotonic()
    end_process(process, STOP_WAIT_SEC)
    assert time.monotonic() - started < STOP_WAIT_SEC        # it ended when asked
    assert process.returncode is not None
    assert gone(child)


def test_a_process_that_wont_end_when_asked_is_made_to():
    process = start("trap '' TERM; echo ready; while :; do sleep 0.05; done")
    assert first_line(process) == 'ready'
    started = time.monotonic()
    end_process(process, STOP_WAIT_SEC)
    assert time.monotonic() - started >= STOP_WAIT_SEC
    assert process.returncode is not None


def test_a_process_that_has_ended_is_left_alone():
    process = start('true')
    process.wait()
    signal_group(process, signal.SIGTERM)                 # no error, and nothing else is signalled
    end_process(process, STOP_WAIT_SEC)
    assert process.returncode == 0


def test_the_text_comes_whole_even_when_a_letter_is_split_between_reads():
    read, write = os.pipe()
    with os.fdopen(read, 'rb') as pipe:
        os.write(write, b'caf' + 'é'.encode()[:1])
        pieces = text_of(pipe)
        assert next(pieces) == 'caf'
        os.write(write, 'é'.encode()[1:] + b' \xff')
        os.close(write)
        assert ''.join(pieces) == 'é �'      # a byte that isn't UTF-8 becomes a stand-in, not an error
