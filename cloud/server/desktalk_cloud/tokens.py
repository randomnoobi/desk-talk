"""Tokens and one-time codes: made from random bytes, and kept only as a hash.

A Pi's token and a pairing code are long random values, so a plain SHA-256 is enough (a password needs a slow hash
because people choose weak ones; these are never chosen by a person). The format below is the shape of a secret, not
a tuning number: a Pi checks the prefix, and a code is typed by hand, so it leaves out letters that look alike."""

from __future__ import annotations

import hashlib
import re
import secrets

TOKEN_PREFIX = 'dtk_'
TOKEN_BYTES = 32                                    # 256 bits of randomness
CODE_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'  # no O, 0, I, 1
CODE_LENGTH = 8                                     # 32 ** 8 is about 10 ** 12 codes


def new_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(TOKEN_BYTES)


def new_code() -> str:
    return ''.join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


def normal_code(text: str) -> str:
    """A code as a person types it (small letters, a dash or spaces) turned into the form new_code() makes."""
    return re.sub(r'[\s-]', '', text).upper()


def hash_secret(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()
