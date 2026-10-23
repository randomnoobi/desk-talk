"""
Sends pictures to the computer: one POST to /api/pi/frame per picture, in upload format 2
(desktalk_shared.wire: the JPEG plus what the Pi's detector found, and how the Pi is doing).

Only the newest picture waits to be sent, so a slow Wi-Fi skips pictures instead of falling behind.
When the computer is away it waits a little longer after each miss (pi.upload_retry_start_sec,
doubling up to pi.upload_retry_max_sec), and then sends only the newest picture: no catching up
on old ones, which would make the characters react minutes late.
"""

from __future__ import annotations

import logging
import threading

from desktalk_shared.wire import DetectorState, UploadInfo, encode_upload
from raspberry_pi.camera import net
from raspberry_pi.camera.clock import Clock

log = logging.getLogger(__name__)


class Uploader:
    """The camera's and the detector's threads call offer(); a thread of its own calls tick() over and over."""

    def __init__(self, server: str, key: str, *, timeout_sec: float, retry_start_sec: float, retry_max_sec: float,
                 clock: Clock):
        self.server = server.rstrip('/')
        self.url = self.server + '/api/pi/frame'
        self.key = key
        self.timeout_sec = timeout_sec
        self.retry_start_sec = retry_start_sec
        self.retry_max_sec = retry_max_sec
        self.clock = clock
        self.wake = threading.Event()        # set when there's a picture: the thread stops waiting
        self.sent = 0
        self.failed = 0
        self.skipped = 0                     # pictures a newer one replaced before they could be sent
        self.last_error = ''
        self._lock = threading.Lock()
        self._waiting: tuple[bytes, UploadInfo] | None = None
        self._retry_at = 0.0
        self._wait = 0.0                     # the last wait after a miss (0 = the last one got through)
        self._said = ''                      # the last thing logged, so a problem is logged once, not every picture

    def offer(self, jpeg: bytes, info: UploadInfo) -> None:
        """Send this picture next, instead of the one waiting. A busy picture (live view only) never
        replaces one that carries detections or still needs detecting: it's skipped instead."""
        with self._lock:
            if self._waiting is not None:
                self.skipped += 1
                if info.detector is DetectorState.BUSY and self._waiting[1].detector is not DetectorState.BUSY:
                    return
            self._waiting = (jpeg, info)
        self.wake.set()

    def tick(self) -> float | None:
        """Send the waiting picture. Returns the seconds until the next try (None: when a picture comes)."""
        now = self.clock.monotonic()
        if now < self._retry_at:
            return self._retry_at - now
        with self._lock:
            waiting, self._waiting = self._waiting, None
        if waiting is None:
            return None
        problem = self._send(*waiting)
        if problem is None:
            self.sent += 1
            self._wait = 0.0
            self._say(f'Connected: pictures are reaching {self.server}', logging.INFO)
            return 0.0
        self.failed += 1
        self.last_error = problem
        self._say(problem, logging.WARNING)
        self._wait = min(self._wait * 2, self.retry_max_sec) if self._wait else self.retry_start_sec
        self._retry_at = self.clock.monotonic() + self._wait
        return self._wait

    def _send(self, jpeg: bytes, info: UploadInfo) -> str | None:
        """None if the computer took the picture, else what went wrong in plain words."""
        body, content_type = encode_upload(jpeg, info)
        try:
            status, reply = net.call('POST', self.url, body=body, timeout=self.timeout_sec,
                                     headers={'Content-Type': content_type, 'X-Desk-Key': self.key})
        except net.NoAnswer as exc:
            return net.computer_away(self.server, exc)
        if status == 200:
            return None
        return net.computer_said_no(status, reply)

    def _say(self, message: str, level: int) -> None:
        if message != self._said:
            log.log(level, message)
            self._said = message
