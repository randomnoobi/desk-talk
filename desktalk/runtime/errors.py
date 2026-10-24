"""Errors in plain words, for logs and /api/health. plain_error lives in desktalk/domain/errors.py now;
the runtime's own files still import it from here."""

from __future__ import annotations

from desktalk.domain.errors import plain_error

__all__ = ['plain_error']
