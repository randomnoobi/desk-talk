"""
The interfaces between the cloud server's parts. Every part is written against these, never against another part's
class; bootstrap (Task 9) builds the real ones and tests use the fakes in tests/fakes/cloud.py. Each has a contract
suite in tests/contracts/. Change this file only on purpose: every part depends on it.

Times are seconds since 1970 and are always passed in (`now`), never read from the clock inside a store.
Events are the plain dicts shared/desktalk_shared/cloud_api.py:to_cloud_event gives.

A store only receives clean text: no NUL character and nothing that can't be written as UTF-8, in any string or
name (domain.is_clean_text). ingest.py and the routes refuse anything else first, because Postgres would refuse it.
A store raises LookupError for an unknown Pi (also for a code made for a Pi or person who isn't there, and a report
for a Pi who isn't there) and ValueError for a code that already exists or a report status that isn't allowed;
a real store must do the same, and the contract suite checks it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol, runtime_checkable

from desktalk_cloud.domain import Admin, Device, GoogleUser, ObjectState, Report, StateChange, User


class ReportFailed(Exception):
    """The report could not be written. The message is plain words that are safe to show and keep."""


class ChatFailed(Exception):
    """The AI company would not answer a chat request. The message is plain words and never holds the chat text."""


class GoogleFailed(Exception):
    """Google would not log the person in. The message is plain words and never holds a secret."""


@runtime_checkable
class Clock(Protocol):
    def now(self) -> float:
        """Seconds since 1970."""
        ...

    def monotonic(self) -> float:
        """Seconds that only go up: for measuring how long something took."""
        ...


@runtime_checkable
class Store(Protocol):
    """Everything the server keeps. Contract: tests/contracts/cloud_store_contract.py."""

    # ── people and admins ─────────────────────────────────────────────────
    def upsert_user(self, google_sub: str, email: str, name: str, now: float) -> User:
        """The user for this Google account (made if new; its email and name are updated if not)."""
        ...

    def get_user(self, user_id: str) -> User | None: ...

    def delete_user(self, user_id: str) -> bool:
        """Remove the person, their Pis and everything kept for them. False when there was no such person."""
        ...

    def list_users(self) -> list[User]:
        """Oldest first."""
        ...

    def get_admin(self, username: str) -> Admin | None: ...

    def create_admin_if_missing(self, username: str, password_hash: str) -> bool:
        """True when it made the admin; False when that username was already there (nothing changes)."""
        ...

    # ── linking ───────────────────────────────────────────────────────────
    def add_code(self, code_hash: str, user_id: str, device_id: str | None, time_zone: str, expires_at: float) -> None:
        """A one-time code. With a `device_id` it links that Pi again; without one it makes a new Pi.
        Raises LookupError when the person or the Pi isn't there, ValueError when the code already exists."""
        ...

    def claim_code(self, code_hash: str, name: str, token_hash: str, now: float) -> Device | None:
        """Use a code: in one step (so one winner when two ask at once) it marks the code used and either makes a new
        Pi (named `name`, in the code's time zone) or, for a "link again" code, keeps that Pi (and its name), brings it
        back if it was removed, takes the code's time zone (the website sends one every time) and cancels its old
        tokens. The new token is `token_hash`.
        None when the code is unknown, used or expired (all alike). It expires at `expires_at` exactly."""
        ...

    # ── Pis ───────────────────────────────────────────────────────────────
    def get_device(self, device_id: str) -> Device | None:
        """Also a removed Pi."""
        ...

    def list_devices(self, user_id: str) -> list[Device]:
        """This person's Pis, removed ones too, oldest first."""
        ...

    def list_all_devices(self) -> list[Device]:
        """Everyone's, oldest first."""
        ...

    def rename_device(self, device_id: str, name: str) -> Device | None: ...

    def remove_device(self, device_id: str, now: float) -> None:
        """Cancel its tokens and its unused codes, and mark it removed. Its history stays. (A new code made after
        this works again.)"""
        ...

    def delete_device(self, device_id: str) -> bool:
        """Delete the Pi and all its data (events, totals, states, reports, tokens). False when it wasn't there."""
        ...

    def device_for_token(self, token_hash: str, now: float) -> Device | None:
        """The Pi this token belongs to; None when the token is unknown, cancelled or its Pi is removed."""
        ...

    # ── events ────────────────────────────────────────────────────────────
    def save_batch(self, device_id: str, events: Sequence[Mapping[str, Any]], totals: Sequence[Mapping[str, Any]],
                   state_changes: Sequence[StateChange], caught_up: bool, now: float) -> tuple[int, int]:
        """Keep a batch in one step and give (accepted, repeats).
        events: new ones are kept; one with an id the Pi already has changes nothing (a repeat).
        totals: daily_totals events, one row per day; the one with the newer `ts` replaces the older (the same or an
          older one counts as a repeat).
        state_changes: an object's state; one with an older `since` never replaces a newer one. A change is applied
          only if the event it names (event_id) was newly kept in this call and the change says what that kept event
          says (object, new_state, ts). So a repeat changes nothing, and neither does the change of a later event that
          has the same id as an earlier one in the same batch: the first one with an id wins.
        Raises LookupError when there is no such Pi.
        The Pi's last_seen_at is set to `now`, and caught_up_at too when `caught_up`."""
        ...

    def events_after(self, device_id: str, since: float, after_id: str | None, limit: int) -> list[dict[str, Any]]:
        """Oldest first, ordered by (ts, id). With no `after_id`: those with ts > since. With one: those after
        (since, after_id), so events with the same time are never skipped."""
        ...

    def events_between(self, device_id: str, start: float, end: float) -> list[dict[str, Any]]:
        """start <= ts < end, ordered by (ts, id)."""
        ...

    def latest_event(self, device_id: str, event_type: str) -> dict[str, Any] | None:
        """The newest event of this type (by ts, then id)."""
        ...

    def object_states(self, device_id: str) -> list[ObjectState]:
        """Each object's newest state, ordered by label."""
        ...

    def totals_between(self, device_id: str, first_day: str, last_day: str) -> list[dict[str, Any]]:
        """The daily_totals events for these days (both ends included), ordered by day."""
        ...

    def delete_events_before(self, ts: float) -> int:
        """Delete every Pi's events with ts below this. Gives how many."""
        ...

    # ── reports ───────────────────────────────────────────────────────────
    def create_report(self, device_id: str, week_start: str, now: float) -> Report | None:
        """A pending report; None when that week already has one. Raises LookupError when the Pi isn't there."""
        ...

    def get_report(self, device_id: str, week_start: str) -> Report | None: ...

    def get_report_by_id(self, report_id: str) -> Report | None: ...

    def list_reports(self, device_id: str, *, with_text: bool = True) -> list[Report]:
        """Newest week first."""
        ...

    def reports_to_write(self, now: float) -> list[Report]:
        """Pending reports whose next_try_at is empty or has come, oldest first."""
        ...

    def finish_report(self, report_id: str, status: str, markdown: str | None, model: str | None, error: str | None,
                      now: float) -> Report | None:
        """Set the final status (done, no_data or failed) and what goes with it. Only a pending report changes: any
        other is given back as it is, so a late call can't undo a finished report. (`now` is not kept: there is no
        column for it. It stays so a store that wants it later has it.)"""
        ...

    def retry_report(self, report_id: str, error: str, next_try_at: float) -> Report | None:
        """It stays pending with one more try counted, the error kept and the next try set. Only a pending report
        changes: any other is given back as it is."""
        ...

    def list_failed_reports(self) -> list[Report]:
        """Newest first."""
        ...

    def reset_report(self, report_id: str, now: float) -> Report | None:
        """Back to a fresh pending report (no tries, no error, no text). It keeps its `created_at`: the job counts its
        wait for a Pi from there, so a re-run after that wait is written at once."""
        ...


@runtime_checkable
class GoogleLogin(Protocol):
    def login_url(self, state: str, redirect_uri: str) -> str:
        """The Google address to send the person to."""
        ...

    def exchange(self, code: str, redirect_uri: str) -> GoogleUser:
        """Trade Google's code for who logged in. Raises GoogleFailed."""
        ...


@runtime_checkable
class ReportWriter(Protocol):
    def write(self, prompt: str, data: str) -> str:
        """The report as markdown. Raises ReportFailed."""
        ...


@runtime_checkable
class ChatForwarder(Protocol):
    async def complete(self, messages: Sequence[Mapping[str, Any]], max_tokens: int | None,
                       temperature: float | None) -> dict[str, Any]:
        """The AI company's reply in the OpenAI chat completion shape. Raises ChatFailed."""
        ...
