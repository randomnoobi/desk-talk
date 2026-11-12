"""
A circuit breaker: stops calling something that keeps failing, and now and then lets one call check whether it's back.

    breaker = Breaker(failures_to_open=3, pause=Backoff(first=60, most=60))
    if breaker.let_in(now):          # False while it's open, and while the check call is still out
        try:
            call()
        except Problem:
            breaker.failed(now)      # True when this failure opened it
        else:
            breaker.worked()

After `failures_to_open` failures in a row it opens for `pause.next()` seconds: the same each time when the Backoff's
first and most are the same, longer each time it opens again otherwise (with the Backoff's jitter). After that one call
is let in to check; if that fails it opens again at once. A call that works closes it and starts the pause over.
back() closes it without a call having worked (say a health check said it's ready): the count starts again, but the
pause stays as long as it was, so something that keeps coming and going waits longer each time.

It has no clock and no lock: the caller passes the time in (`now`, from a monotonic clock) and keeps it to one thread
or behind its own lock. Used by Desk Talk's AI calls and the Pi's detector client. Standard library only.
"""

from __future__ import annotations

from desktalk_shared.backoff import Backoff


class Breaker:
    def __init__(self, failures_to_open: int, pause: Backoff):
        self.failures_to_open = failures_to_open
        self.failures = 0                    # failed calls in a row
        self.open_until: float | None = None
        self.checking = False                # the one call let in after a pause is still out
        self.pause = 0.0                     # how long it opened for last time
        self._pause = pause

    def is_open(self, now: float) -> bool:
        return self.open_until is not None and (now < self.open_until or self.checking)

    def let_in(self, now: float) -> bool:
        """True if a call may go ahead now; the first call after the pause becomes the check."""
        if self.open_until is None:
            return True
        if now < self.open_until or self.checking:
            return False
        self.checking = True
        return True

    def worked(self) -> None:
        """A call worked: closed, and the next pause starts over."""
        self.back()
        self._pause.reset()

    def back(self) -> None:
        """It's back without a call having worked: closed, the count starts again, the pause length stays."""
        self.failures, self.open_until, self.checking = 0, None, False

    def failed(self, now: float) -> bool:
        """Count a failed call. True if the breaker just opened."""
        self.failures += 1
        was_checking, self.checking = self.checking, False
        if was_checking or self.failures >= self.failures_to_open:
            self.pause = self._pause.next()
            self.open_until = now + self.pause
            return True
        return False

    def seconds_left(self, now: float) -> int:
        return max(1, round((self.open_until or now) - now))
