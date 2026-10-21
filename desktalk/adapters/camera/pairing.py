"""
The pairing key: a short random key in the Pi's command and the phone's QR link, so other devices on
the Wi-Fi can't send pictures. It's saved (certs/pair-key.txt) and used again after a restart, so the
phone's bookmarked page and the Pi's command keep working.
"""

from __future__ import annotations

import logging
import secrets
import string
from pathlib import Path

log = logging.getLogger(__name__)

_URL_SAFE = set(string.ascii_letters + string.digits + '-_')
_ONLY_YOU = 0o600          # file permissions: only you can read or change it


def make_pair_key(path: Path | None, key_bytes: int) -> str:
    """The saved key, or a new one of `key_bytes` random bytes (saved at `path` when given).

    A saved key you wrote yourself is kept if it only uses letters, digits, - and _, and has at least
    `key_bytes` characters (shorter ones are too easy to guess)."""
    if path is not None:
        try:
            saved = Path(path).read_text().strip()
        except OSError:
            saved = ''
        if len(saved) >= key_bytes and set(saved) <= _URL_SAFE:
            return saved
    key = secrets.token_urlsafe(key_bytes)
    if path is not None:
        try:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_text(key)
            Path(path).chmod(_ONLY_YOU)
        except OSError as exc:
            log.warning('Could not save the pairing key in %s (%s): the Pi command and phone link will '
                        'change after a restart', path, exc)
    return key
