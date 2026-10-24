"""Errors that say a part can't do its job right now. The text is plain words for the app to show."""

from __future__ import annotations

from desktalk_shared.detection import DetectorUnavailable

__all__ = ['AIUnavailable', 'DetectorUnavailable', 'plain_error']


class AIUnavailable(RuntimeError):
    """The AI can't answer: no key, out of quota, down, or too slow."""

    def __init__(self, reason: str, *, rate_limited: bool = False):
        super().__init__(reason)
        self.reason = reason
        self.rate_limited = rate_limited


def plain_error(exc: BaseException) -> str:
    """'ValueError: the camera is off', or just 'ValueError' when there's no message (for logs and /api/health)."""
    text = str(exc)
    return f'{type(exc).__name__}: {text}' if text else type(exc).__name__
