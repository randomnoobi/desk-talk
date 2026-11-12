"""
The computer's line to the Pi's numbers (ports.PiStatsLink), for the developer page's Pi tab, with the standard
library only:

    link = HttpPiStatsLink('desktalk.local:5000', 3)     # settings → dev.pi_address, dev.pi_stats_timeout_sec
    reply = link.fetch(since)                             # the Pi's GET /api/system/stats reply, as it is

The developer page runs on the computer and the app lets no other web site read it, so the computer's server
asks the Pi and passes the reply on. When the Pi doesn't answer in time, can't be found, answers with an error,
or sends something that isn't its numbers, fetch() raises StatsUnavailable with a short problem in plain words
(no address: the route puts it in its own sentence, prompts.yaml → dev.pi_stats_unreachable).
"""

from __future__ import annotations

import http.client
import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

from desktalk.domain import StatsUnavailable

NOT_THEIR_NUMBERS = "it didn't send its numbers"
BAD_ADDRESS = "that address can't be used"


def _open(url: str, timeout_sec: float) -> bytes:
    """The body of the Pi's reply. Error statuses come as urllib.error.HTTPError."""
    try:
        with urllib.request.urlopen(url, timeout=timeout_sec) as reply:
            return reply.read()
    except urllib.error.HTTPError as exc:
        exc.close()                    # let go of the connection: the status is all fetch() needs
        raise


def _why(exc: BaseException, timeout_sec: float) -> str:
    """Plain words for a request that got no good answer."""
    if isinstance(exc, urllib.error.HTTPError):
        return f'it answered {exc.code}'
    reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    if isinstance(reason, TimeoutError):
        return f'no answer in {timeout_sec:g} s'
    if isinstance(reason, ConnectionRefusedError):
        return 'nothing is listening there (connection refused)'
    if isinstance(reason, http.client.InvalidURL):            # a bad port, a space ... (an HTTPException)
        return BAD_ADDRESS
    if isinstance(reason, socket.gaierror):
        return "its name couldn't be found"
    if isinstance(reason, (ConnectionResetError, http.client.HTTPException)):
        return 'it hung up without answering'
    return str(reason) or type(reason).__name__


class HttpPiStatsLink:
    """The PiStatsLink (desktalk/ports.py) for the Pi at `address` (its name and port, like desktalk.local:5000).
    `opener(url, timeout_sec)` gets a reply's body; the usual one uses urllib."""

    def __init__(self, address: str, timeout_sec: float, *, opener: Callable[[str, float], bytes] | None = None):
        self._url = f"http://{address.strip().removeprefix('http://').rstrip('/')}/api/system/stats"
        self._timeout = timeout_sec
        self._open = opener or _open

    def fetch(self, since: float | None) -> dict[str, Any]:
        url = self._url if since is None else f'{self._url}?{urllib.parse.urlencode({"since": since})}'
        try:
            body = self._open(url, self._timeout)
        except (OSError, http.client.HTTPException) as exc:       # URLError and timeouts are OSErrors too
            raise StatsUnavailable(_why(exc, self._timeout)) from None
        except ValueError:                                         # an address urllib can't make sense of
            raise StatsUnavailable(BAD_ADDRESS) from None
        try:
            reply = json.loads(body)
        except ValueError:                                         # not JSON, or not even text
            raise StatsUnavailable(f"{NOT_THEIR_NUMBERS} (the answer isn't JSON)") from None
        if not isinstance(reply, dict) or not isinstance(reply.get('samples'), list):
            raise StatsUnavailable(NOT_THEIR_NUMBERS)
        return reply
