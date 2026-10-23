"""
The detector service's own settings, read from the environment. In Docker they come from
raspberry_pi/detector_service/.env (through compose.yaml) and the Dockerfile. Which model to run
is not here: the camera program sends it with PUT /model.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

BYTES_PER_MB = 1_000_000
WHERE = 'raspberry_pi/detector_service/.env'


class SettingsError(ValueError):
    """A setting is missing or wrong. The text names it."""


@dataclass(frozen=True)
class ServiceSettings:
    host: str                  # where the web server listens (0.0.0.0 inside the container)
    port: int
    models_dir: Path           # .pt models and their ncnn versions; a Docker volume
    max_picture_bytes: int     # bigger pictures get a 413 reply
    retry_first_sec: float     # first wait after a model fails to load; doubles each time ...
    retry_max_sec: float       # ... up to this

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> ServiceSettings:
        def get(name: str) -> str:
            value = env.get(name, '').strip()
            if not value:
                raise SettingsError(f'{name} is not set (the values are in {WHERE})')
            return value

        def positive(name: str, kind: Callable[[str], float]) -> float:
            text = get(name)
            try:
                value = kind(text)
            except ValueError:
                raise SettingsError(f'{name} must be a {"whole " if kind is int else ""}number '
                                    f'(got {text!r})') from None
            if not math.isfinite(value) or value <= 0:
                raise SettingsError(f'{name} must be more than 0 (got {text!r})')
            return value

        first, longest = positive('DETECTOR_RETRY_FIRST_SEC', float), positive('DETECTOR_RETRY_MAX_SEC', float)
        if longest < first:
            raise SettingsError(f'DETECTOR_RETRY_MAX_SEC ({longest:g}) must be at least '
                                f'DETECTOR_RETRY_FIRST_SEC ({first:g})')
        return cls(host=get('DETECTOR_HOST'), port=int(positive('DETECTOR_PORT', int)),
                   models_dir=Path(get('DETECTOR_MODELS_DIR')),
                   max_picture_bytes=int(positive('DETECTOR_MAX_PICTURE_MB', float) * BYTES_PER_MB),
                   retry_first_sec=first, retry_max_sec=longest)
