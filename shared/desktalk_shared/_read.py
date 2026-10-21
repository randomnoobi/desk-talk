"""Small helpers for reading JSON objects with clear error messages. Standard library only."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any


class WireError(ValueError):
    """A message between the Pi and this computer (or the detector) is broken or from another version.
    The text says what's wrong in plain words, and what to do when there's something to do."""


def mapping(data: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(data, Mapping):
        raise WireError(f'{where} must be a JSON object (got {type(data).__name__})')
    return data


def text(data: Mapping[str, Any], key: str, where: str, *, required: bool = False, default: str = '') -> str:
    if key not in data or data[key] is None:
        if required:
            raise WireError(f'{where}{key} is missing')
        return default
    value = data[key]
    if not isinstance(value, str):
        raise WireError(f'{where}{key} must be text (got {value!r})')
    return value


def number(data: Mapping[str, Any], key: str, where: str, *, required: bool = False) -> float | None:
    value = data.get(key)
    if value is None:
        if required:
            raise WireError(f'{where}{key} is missing')
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise WireError(f'{where}{key} must be a number (got {value!r})')
    return value


def whole(data: Mapping[str, Any], key: str, where: str, *, required: bool = False) -> int | None:
    value = number(data, key, where, required=required)
    if value is not None and value != int(value):
        raise WireError(f'{where}{key} must be a whole number (got {value!r})')
    return None if value is None else int(value)


def flag(data: Mapping[str, Any], key: str, where: str, *, default: bool = False) -> bool:
    value = data.get(key, default)
    if not isinstance(value, bool):
        raise WireError(f'{where}{key} must be true or false (got {value!r})')
    return value
