"""One HTTP request at a time, with the standard library only (the Pi has no `requests`)."""

from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.request


class NoAnswer(Exception):
    """Nothing answered: not running, unreachable, too slow, or it hung up. The text says which."""


def call(method: str, url: str, *, timeout: float, body: bytes | None = None,
         headers: dict[str, str] | None = None) -> tuple[int, bytes]:
    """The reply's status and body, error statuses included. Raises NoAnswer when there's no reply."""
    request = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as reply:
            return reply.status, reply.read()
    except urllib.error.HTTPError as exc:
        with exc:
            try:
                return exc.code, exc.read()
            except (OSError, http.client.HTTPException) as cut_off:    # it hung up (or stalled) half-way
                raise NoAnswer(_plain(cut_off, timeout)) from None
    except (OSError, http.client.HTTPException) as exc:
        raise NoAnswer(_plain(exc, timeout)) from None


def error_text(body: bytes, most_chars: int | None) -> str:
    """The reason in an error reply: FastAPI's {"detail": ...}, {"error": ...}, or the text itself
    (its first `most_chars` letters: pi.error_text_chars; None: all of it)."""
    try:
        data = json.loads(body)
    except ValueError:
        return body.decode('utf-8', 'replace').strip()[:most_chars]
    if isinstance(data, dict):
        for key in ('detail', 'error', 'message'):
            if isinstance(data.get(key), str):
                return data[key]
    return ''


def computer_away(server: str, why: NoAnswer) -> str:
    return f'Cannot reach the computer at {server} ({why}). Is server.py running, and on the same Wi-Fi?'


def computer_said_no(status: int, body: bytes, most_chars: int | None) -> str:
    return f'The computer said no ({status}): {error_text(body, most_chars) or "it gave no reason"}'


def _plain(exc: BaseException, timeout: float) -> str:
    reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    if isinstance(reason, TimeoutError):
        return f'no answer within {timeout:g} s'
    if isinstance(reason, ConnectionRefusedError):
        return "it's not running (connection refused)"
    if isinstance(reason, (ConnectionResetError, http.client.RemoteDisconnected, http.client.BadStatusLine)):
        return 'it hung up without answering'
    if isinstance(reason, http.client.IncompleteRead):
        return 'it hung up half-way through its answer'
    return str(reason) or type(reason).__name__
