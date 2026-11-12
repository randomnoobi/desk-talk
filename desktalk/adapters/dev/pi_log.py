"""
The developer page's Pi log tab (ports.PiLogReader): the Pi's log, read over ssh (settings → dev.pi_log).

(To be written: see the plan in the pull request.)
"""

from __future__ import annotations

from desktalk.config.app import DevPiLogSettings
from desktalk.config.prompts import PiLogWords
from desktalk.domain import PiLogPage, PiLogSource
from desktalk.ports import Clock


class SshPiLog:
    """Follows one of settings → dev.pi_log.sources on the Pi with ssh. `stop_wait_sec` is how long ssh gets to
    end on its own before it is killed (settings → supervisor.stop_wait_sec)."""

    name = 'pi log'

    def __init__(self, settings: DevPiLogSettings, words: PiLogWords, clock: Clock, *, stop_wait_sec: float):
        self._settings = settings
        self._words = words
        self._clock = clock
        self._stop_wait_sec = stop_wait_sec

    def sources(self) -> list[PiLogSource]:
        raise NotImplementedError

    def read(self, source: str, after: int) -> PiLogPage:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError

    def tick(self, now: float) -> float:
        raise NotImplementedError
