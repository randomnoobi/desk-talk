"""Errors in plain words, for logs and /api/health."""

from __future__ import annotations


def plain_error(exc: BaseException) -> str:
    """'ValueError: the camera is off', or just 'ValueError' when there's no message."""
    text = str(exc)
    return f'{type(exc).__name__}: {text}' if text else type(exc).__name__
