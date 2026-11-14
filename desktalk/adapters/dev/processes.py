"""
What the developer page's tools share for their child processes (test_runner.py and pi_log.py): each child is
started in its own process group (start_new_session=True), so ending it ends everything it started too.

- end_process() asks the whole group to end, and makes it after `wait_sec` (settings → supervisor.stop_wait_sec).
- text_of() gives a pipe's printout as text as soon as each piece comes (UTF-8; a byte that isn't is replaced).
"""

from __future__ import annotations

import codecs
import contextlib
import io
import os
import signal
import subprocess
from collections.abc import Iterator
from typing import IO

ENCODING = 'utf-8'


def end_process(process: subprocess.Popen[bytes], wait_sec: float) -> None:
    """Ask the process and everything it started to end; after `wait_sec`, make them."""
    signal_group(process, signal.SIGTERM)
    try:
        process.wait(timeout=wait_sec)
    except subprocess.TimeoutExpired:
        signal_group(process, signal.SIGKILL)
        process.wait()


def signal_group(process: subprocess.Popen[bytes] | subprocess.Popen[str], which: signal.Signals) -> None:
    """Send `which` to the process's own process group (start_new_session), so its children get it too."""
    if process.returncode is not None:
        return
    with contextlib.suppress(ProcessLookupError):     # it has just ended by itself
        os.killpg(process.pid, which)


def text_of(pipe: IO[bytes]) -> Iterator[str]:
    """The text coming out of a pipe, a piece at a time, as soon as it is there (a letter split between two reads
    comes with the second), until the pipe closes."""
    decoder = codecs.getincrementaldecoder(ENCODING)(errors='replace')
    handle = pipe.fileno()
    while chunk := os.read(handle, io.DEFAULT_BUFFER_SIZE):
        if text := decoder.decode(chunk):
            yield text
    if text := decoder.decode(b'', final=True):
        yield text
