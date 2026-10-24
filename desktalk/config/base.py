"""
The building blocks every config file's settings are made of: a read-only section that refuses unknown keys,
the value types (a number above 0, an hour, a JPEG quality ...) and the error a bad setting raises.
"""

from __future__ import annotations

from typing import Annotated, ClassVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

CONFIG_FOLDER = 'config'           # under the project folder: settings.yaml, protocols.yaml, agents.yaml ...


class ConfigError(Exception):
    """A setting is missing, misspelled or has a bad value. The message says which file and setting."""


class _Section(BaseModel):
    """Every part of the config: read-only, and a key it doesn't know is an error (a typo must not be ignored)."""

    model_config = ConfigDict(frozen=True, extra='forbid')
    # (smaller, larger) pairs of settings in this section, e.g. a wait that starts at one and grows to the other
    _in_order: ClassVar[tuple[tuple[str, str], ...]] = ()

    @model_validator(mode='after')
    def _check_order(self):
        for small, large in self._in_order:
            a, b = getattr(self, small), getattr(self, large)
            if a > b:
                raise ValueError(f'{small} ({_number(a)}) must not be more than {large} ({_number(b)})')
        return self


class _Says:
    """Plain words for a value type that its limits alone can't describe (used in error messages)."""

    def __init__(self, words: str):
        self.words = words


# Value types. Their limits also make the error messages ("must be a number above 0").
PositiveNumber = Annotated[float, Field(gt=0)]
ZeroOrMore = Annotated[float, Field(ge=0)]
PositiveInt = Annotated[int, Field(gt=0)]
Count = Annotated[int, Field(ge=0)]
Fraction = Annotated[float, Field(ge=0, le=1)]
Hour = Annotated[int, Field(ge=0, le=23)]
Level = Annotated[float, Field(ge=0, le=255)]                 # picture brightness, 0-255
Port = Annotated[int, Field(ge=1, le=65535)]
JpegQuality = Annotated[int, Field(ge=1, le=100)]
ClockTime = Annotated[str, Field(pattern=r'^\d{1,2}:\d{2}$'), _Says('a time like 07:30')]
Latitude = Annotated[float, Field(ge=-90, le=90)]
Longitude = Annotated[float, Field(ge=-180, le=180)]
Words = tuple[str, ...]



def _number(x: float) -> str:
    return f'{x:g}'
