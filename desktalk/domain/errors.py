"""Errors that say a part can't do its job right now, or was asked for something that isn't there. The text
is plain words for the app to show."""

from __future__ import annotations

from desktalk_shared.detection import DetectorUnavailable

__all__ = ['AIUnavailable', 'CloudLinkRefused', 'CloudNotLinked', 'CloudRefused', 'CloudUnavailable',
           'DetectorUnavailable', 'UnknownChat', 'plain_error']


class AIUnavailable(RuntimeError):
    """The AI can't answer: no key, out of quota, down, or too slow."""

    def __init__(self, reason: str, *, rate_limited: bool = False):
        super().__init__(reason)
        self.reason = reason
        self.rate_limited = rate_limited


class UnknownChat(ValueError):
    """The chat was asked to reply in a 1-on-1 chat with an agent that doesn't exist."""


class CloudUnavailable(RuntimeError):
    """The cloud server didn't answer well: it's off, unreachable, too slow, busy (429) or broken (5xx). The
    sender waits and tries again; nothing else in Desk Talk notices."""


class CloudRefused(CloudUnavailable):
    """The cloud server answered, but said no to this upload (400, 404, 413 ...). That will not fix itself like a
    server that is off, so the sender logs it apart from "unavailable" (and still waits and tries again)."""


class CloudNotLinked(RuntimeError):
    """The cloud server says it doesn't know this computer (401), or it didn't accept the pairing code. The sender
    stops until the computer is linked again."""



class CloudLinkRefused(RuntimeError):
    """The Pi said no to a link the cloud website offered. `kind` says why, for the route's status:
    'bad_request' (not a web address and a code), 'not_caller' (the server isn't the home network computer asking),
    'already_linked', 'busy' (another offer is being worked on), 'not_saved' (saving is off, so it wouldn't last),
    'refused' (the server didn't accept the code) or 'unavailable' (the server couldn't be reached). `text` is the
    plain words to show; never the code."""

    def __init__(self, kind: str, text: str):
        super().__init__(text)
        self.kind = kind
        self.text = text


def plain_error(exc: BaseException) -> str:
    """'ValueError: the camera is off', or just 'ValueError' when there's no message (for logs and /api/health)."""
    text = str(exc)
    return f'{type(exc).__name__}: {text}' if text else type(exc).__name__
