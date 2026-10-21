"""
The interfaces (ports) between the parts of Desk Talk.

Every part is written against these small interfaces, never against another part's class.
desktalk/bootstrap.py builds the real parts and connects them; tests build the same parts
with the fakes in tests/fakes/. Each interface has a contract test suite in tests/contracts/
that the real version and the fake both pass.

Change this file only on purpose: every part depends on it.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    """The time. Code asks the clock instead of calling time.time(), so tests can move time by hand."""

    def now(self) -> float:
        """Seconds since 1970 (wall clock): for timestamps and "what day is it"."""
        ...

    def monotonic(self) -> float:
        """Seconds that only ever go up: for measuring how long something took."""
        ...

    def sleep(self, seconds: float) -> None:
        ...


@runtime_checkable
class PeriodicWorker(Protocol):
    """A background job (reminders, Lens, idle chat, the scheduler, the camera producer ...).

    The Supervisor (desktalk/runtime/) calls tick() over and over in the job's own thread.
    Tests call tick() directly with a FakeClock: no threads, no waiting.
    """

    name: str

    def tick(self, now: float) -> float:
        """Do one round of work. Returns how many seconds to wait before the next round.

        Raising means this round failed: the Supervisor logs it and tries again after a
        growing wait (settings → supervisor), so one bad round never switches a feature off."""
        ...
