"""The plain data the cloud server's parts pass to each other. Times are seconds since 1970; days are 'YYYY-MM-DD'."""

from __future__ import annotations

from dataclasses import dataclass

PENDING, DONE, NO_DATA, FAILED = 'pending', 'done', 'no_data', 'failed'      # a report's status
REPORT_STATUSES = (PENDING, DONE, NO_DATA, FAILED)


@dataclass(frozen=True, kw_only=True)
class User:
    id: str
    google_sub: str
    email: str
    name: str
    created_at: float


@dataclass(frozen=True, kw_only=True)
class Admin:
    username: str
    password_hash: str


@dataclass(frozen=True, kw_only=True)
class Device:
    """A linked Pi. `removed_at` is set once the person removes it: its token stops working but its history stays."""

    id: str
    user_id: str
    name: str
    time_zone: str
    created_at: float
    last_seen_at: float | None = None
    caught_up_at: float | None = None
    removed_at: float | None = None


@dataclass(frozen=True)
class StateChange:
    """An object's state as of `since` (the time of the event that said so)."""

    label: str
    state: str
    since: float


@dataclass(frozen=True, kw_only=True)
class ObjectState:
    """An object's newest known state; `updated_at` is when the server learned it."""

    label: str
    state: str
    since: float
    updated_at: float


@dataclass(frozen=True, kw_only=True)
class Report:
    """One Pi's report for one week. `week_start` is the first day of the week. `tries` and `next_try_at` are how
    many times it was tried and when to try again (while it is pending)."""

    id: str
    device_id: str
    week_start: str
    status: str
    markdown: str | None
    model: str | None
    error: str | None
    created_at: float
    tries: int = 0
    next_try_at: float | None = None


@dataclass(frozen=True, kw_only=True)
class GoogleUser:
    """Who Google says just logged in."""

    sub: str
    email: str
    name: str
    email_verified: bool
