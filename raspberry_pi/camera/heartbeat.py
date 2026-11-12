"""
Tells the app how the Pi is doing (temperature, slow-down flags, free space, stored pictures) on a timer of its own:
one POST to /api/pi/heartbeat every pi.heartbeat_every_sec, with the pairing key, like the pictures. It runs in a
thread of its own, so the app keeps hearing from the Pi when the camera or the uploads are stuck, and can tell a Pi
that has gone quiet from a camera that stopped.

A heartbeat that doesn't get through is logged once (until it changes) and tried again next time: no catching up.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import asdict

from desktalk_shared.wire import PiHealth
from raspberry_pi.camera import net

log = logging.getLogger(__name__)


class Heartbeat:
    """A thread of its own calls tick() over and over. `read` reads how the Pi is doing right now."""

    def __init__(self, server: str, key: str, read: Callable[[], PiHealth], *, every_sec: float, timeout_sec: float,
                 error_text_chars: int):
        self.server = server.rstrip('/')
        self.url = self.server + '/api/pi/heartbeat'
        self.key = key
        self.read = read
        self.every_sec = every_sec
        self.timeout_sec = timeout_sec
        self.error_text_chars = error_text_chars     # how much of a reply it can't read goes into the log
        self.sent = 0
        self.failed = 0
        self.last_error = ''
        self._said = ''                      # the last thing logged, so a problem is logged once

    def tick(self) -> float:
        """Send one heartbeat. Returns the seconds until the next."""
        problem = self._send(self.read())
        if problem is None:
            self.sent += 1
            self._say(f'Heartbeat: reaching {self.server}', logging.INFO)
        else:
            self.failed += 1
            self.last_error = problem
            self._say(f'Heartbeat: {problem}', logging.WARNING)
        return self.every_sec

    def _send(self, health: PiHealth) -> str | None:
        """None if the app took it, else what went wrong in plain words."""
        body = json.dumps({'health': asdict(health)}).encode('utf-8')
        try:
            status, reply = net.call('POST', self.url, body=body, timeout=self.timeout_sec,
                                     headers={'Content-Type': 'application/json', 'X-Desk-Key': self.key})
        except net.NoAnswer as exc:
            return net.computer_away(self.server, exc)
        if status == 200:
            return None
        return net.computer_said_no(status, reply, self.error_text_chars)

    def _say(self, message: str, level: int) -> None:
        if message != self._said:
            log.log(level, message)
            self._said = message
