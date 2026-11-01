"""The Store kept in memory: for tests and for trying the server with no database. It passes the same contract suite
(tests/contracts/cloud_store_contract.py) as the Postgres store. Everything happens under one lock."""

from __future__ import annotations

import copy
import threading
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from desktalk_cloud.domain import (
    DONE,
    FAILED,
    NO_DATA,
    PENDING,
    Admin,
    Device,
    ObjectState,
    Report,
    StateChange,
    User,
)

_FINAL = frozenset({DONE, NO_DATA, FAILED})


@dataclass
class _Code:
    user_id: str
    device_id: str | None
    time_zone: str
    expires_at: float
    used: bool = False


@dataclass
class _Token:
    device_id: str
    revoked_at: float | None = None
    last_used_at: float | None = None


def _order(event: Mapping[str, Any]) -> tuple[float, str]:
    return event['ts'], event['id']


class MemoryStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._users: dict[str, User] = {}
        self._admins: dict[str, Admin] = {}
        self._codes: dict[str, _Code] = {}
        self._devices: dict[str, Device] = {}
        self._tokens: dict[str, _Token] = {}
        self._events: dict[str, dict[str, dict[str, Any]]] = {}        # device → event id → event
        self._totals: dict[str, dict[str, dict[str, Any]]] = {}        # device → day → daily_totals event
        self._states: dict[str, dict[str, ObjectState]] = {}           # device → label → state
        self._reports: dict[str, Report] = {}                          # report id → report

    # ── people and admins ─────────────────────────────────────────────────
    def upsert_user(self, google_sub: str, email: str, name: str, now: float) -> User:
        with self._lock:
            for user in self._users.values():
                if user.google_sub == google_sub:
                    updated = replace(user, email=email, name=name)
                    self._users[user.id] = updated
                    return updated
            user = User(id=str(uuid.uuid4()), google_sub=google_sub, email=email, name=name, created_at=now)
            self._users[user.id] = user
            return user

    def get_user(self, user_id: str) -> User | None:
        with self._lock:
            return self._users.get(user_id)

    def delete_user(self, user_id: str) -> bool:
        with self._lock:
            if user_id not in self._users:
                return False
            for device in self.list_devices(user_id):
                self.delete_device(device.id)
            for code_hash in [h for h, c in self._codes.items() if c.user_id == user_id]:
                del self._codes[code_hash]
            del self._users[user_id]
            return True

    def list_users(self) -> list[User]:
        with self._lock:
            return sorted(self._users.values(), key=lambda u: (u.created_at, u.id))

    def get_admin(self, username: str) -> Admin | None:
        with self._lock:
            return self._admins.get(username)

    def create_admin_if_missing(self, username: str, password_hash: str) -> bool:
        with self._lock:
            if username in self._admins:
                return False
            self._admins[username] = Admin(username=username, password_hash=password_hash)
            return True

    # ── linking ───────────────────────────────────────────────────────────
    def add_code(self, code_hash: str, user_id: str, device_id: str | None, time_zone: str, expires_at: float) -> None:
        with self._lock:
            if code_hash in self._codes:
                raise ValueError('that code already exists')
            self._codes[code_hash] = _Code(user_id, device_id, time_zone, expires_at)

    def claim_code(self, code_hash: str, name: str, token_hash: str, now: float) -> Device | None:
        with self._lock:
            code = self._codes.get(code_hash)
            if code is None or code.used or code.expires_at <= now:
                return None
            code.used = True
            if code.device_id is None:
                device = Device(id=str(uuid.uuid4()), user_id=code.user_id, name=name, time_zone=code.time_zone,
                                created_at=now)
            else:
                known = self._devices.get(code.device_id)
                if known is None or known.user_id != code.user_id:
                    return None
                device = replace(known, removed_at=None, time_zone=code.time_zone)
                self._cancel_tokens(device.id, now)
            self._devices[device.id] = device
            self._tokens[token_hash] = _Token(device.id)
            return device

    def _cancel_tokens(self, device_id: str, now: float) -> None:
        for token in self._tokens.values():
            if token.device_id == device_id and token.revoked_at is None:
                token.revoked_at = now

    # ── Pis ───────────────────────────────────────────────────────────────
    def get_device(self, device_id: str) -> Device | None:
        with self._lock:
            return self._devices.get(device_id)

    def list_devices(self, user_id: str) -> list[Device]:
        with self._lock:
            return [d for d in self.list_all_devices() if d.user_id == user_id]

    def list_all_devices(self) -> list[Device]:
        with self._lock:
            return sorted(self._devices.values(), key=lambda d: (d.created_at, d.id))

    def rename_device(self, device_id: str, name: str) -> Device | None:
        with self._lock:
            device = self._devices.get(device_id)
            if device is None:
                return None
            self._devices[device_id] = renamed = replace(device, name=name)
            return renamed

    def remove_device(self, device_id: str, now: float) -> None:
        with self._lock:
            device = self._devices.get(device_id)
            if device is None:
                return
            self._cancel_tokens(device_id, now)
            for code_hash in [h for h, c in self._codes.items() if c.device_id == device_id and not c.used]:
                del self._codes[code_hash]
            if device.removed_at is None:
                self._devices[device_id] = replace(device, removed_at=now)

    def delete_device(self, device_id: str) -> bool:
        with self._lock:
            if device_id not in self._devices:
                return False
            del self._devices[device_id]
            for token_hash in [h for h, t in self._tokens.items() if t.device_id == device_id]:
                del self._tokens[token_hash]
            for code_hash in [h for h, c in self._codes.items() if c.device_id == device_id]:
                del self._codes[code_hash]
            for report_id in [i for i, r in self._reports.items() if r.device_id == device_id]:
                del self._reports[report_id]
            for table in (self._events, self._totals, self._states):
                table.pop(device_id, None)
            return True

    def device_for_token(self, token_hash: str, now: float) -> Device | None:
        with self._lock:
            token = self._tokens.get(token_hash)
            if token is None or token.revoked_at is not None:
                return None
            device = self._devices.get(token.device_id)
            if device is None or device.removed_at is not None:
                return None
            token.last_used_at = now
            return device

    # ── events ────────────────────────────────────────────────────────────
    def save_batch(self, device_id: str, events: Sequence[Mapping[str, Any]], totals: Sequence[Mapping[str, Any]],
                   state_changes: Sequence[StateChange], caught_up: bool, now: float) -> tuple[int, int]:
        with self._lock:
            device = self._devices.get(device_id)
            if device is None:
                raise LookupError(f'no such Pi: {device_id}')
            accepted = repeats = 0
            kept = self._events.setdefault(device_id, {})
            newly_kept: set[str] = set()
            for event in events:
                if event['id'] in kept:
                    repeats += 1
                else:
                    kept[event['id']] = copy.deepcopy(dict(event))
                    newly_kept.add(event['id'])
                    accepted += 1
            days = self._totals.setdefault(device_id, {})
            for event in totals:
                old = days.get(event['day'])
                if old is not None and old['ts'] >= event['ts']:
                    repeats += 1
                else:
                    days[event['day']] = copy.deepcopy(dict(event))
                    accepted += 1
            states = self._states.setdefault(device_id, {})
            for change in state_changes:
                if change.event_id not in newly_kept:
                    continue                                    # a repeat of an event changes nothing
                old_state = states.get(change.label)
                if old_state is None or change.since > old_state.since:
                    states[change.label] = ObjectState(label=change.label, state=change.state, since=change.since,
                                                       updated_at=now)
            self._devices[device_id] = replace(device, last_seen_at=now,
                                               caught_up_at=now if caught_up else device.caught_up_at)
            return accepted, repeats

    def _sorted_events(self, device_id: str) -> list[dict[str, Any]]:
        return sorted(self._events.get(device_id, {}).values(), key=_order)

    def events_after(self, device_id: str, since: float, after_id: str | None, limit: int) -> list[dict[str, Any]]:
        with self._lock:
            found = [e for e in self._sorted_events(device_id)
                     if e['ts'] > since or (after_id is not None and e['ts'] == since and e['id'] > after_id)]
            return copy.deepcopy(found[:limit])

    def events_between(self, device_id: str, start: float, end: float) -> list[dict[str, Any]]:
        with self._lock:
            return copy.deepcopy([e for e in self._sorted_events(device_id) if start <= e['ts'] < end])

    def latest_event(self, device_id: str, event_type: str) -> dict[str, Any] | None:
        with self._lock:
            found = [e for e in self._sorted_events(device_id) if e['type'] == event_type]
            return copy.deepcopy(found[-1]) if found else None

    def object_states(self, device_id: str) -> list[ObjectState]:
        with self._lock:
            return sorted(self._states.get(device_id, {}).values(), key=lambda s: s.label)

    def totals_between(self, device_id: str, first_day: str, last_day: str) -> list[dict[str, Any]]:
        with self._lock:
            days = self._totals.get(device_id, {})
            return copy.deepcopy([days[d] for d in sorted(days) if first_day <= d <= last_day])

    def delete_events_before(self, ts: float) -> int:
        with self._lock:
            deleted = 0
            for kept in self._events.values():
                for event_id in [i for i, e in kept.items() if e['ts'] < ts]:
                    del kept[event_id]
                    deleted += 1
            return deleted

    # ── reports ───────────────────────────────────────────────────────────
    def create_report(self, device_id: str, week_start: str, now: float) -> Report | None:
        with self._lock:
            if self.get_report(device_id, week_start) is not None:
                return None
            report = Report(id=str(uuid.uuid4()), device_id=device_id, week_start=week_start, status=PENDING,
                            markdown=None, model=None, error=None, created_at=now)
            self._reports[report.id] = report
            return report

    def get_report(self, device_id: str, week_start: str) -> Report | None:
        with self._lock:
            for report in self._reports.values():
                if report.device_id == device_id and report.week_start == week_start:
                    return report
            return None

    def get_report_by_id(self, report_id: str) -> Report | None:
        with self._lock:
            return self._reports.get(report_id)

    def list_reports(self, device_id: str) -> list[Report]:
        with self._lock:
            return sorted((r for r in self._reports.values() if r.device_id == device_id),
                          key=lambda r: r.week_start, reverse=True)

    def reports_to_write(self, now: float) -> list[Report]:
        with self._lock:
            due = [r for r in self._reports.values()
                   if r.status == PENDING and (r.next_try_at is None or r.next_try_at <= now)]
            return sorted(due, key=lambda r: (r.created_at, r.id))

    def _change(self, report_id: str, **changes: Any) -> Report | None:
        with self._lock:
            report = self._reports.get(report_id)
            if report is None:
                return None
            self._reports[report_id] = changed = replace(report, **changes)
            return changed

    def finish_report(self, report_id: str, status: str, markdown: str | None, model: str | None, error: str | None,
                      now: float) -> Report | None:
        if status not in _FINAL:
            raise ValueError(f'a finished report is done, no_data or failed, not {status!r}')
        return self._change(report_id, status=status, markdown=markdown, model=model, error=error, next_try_at=None)

    def retry_report(self, report_id: str, error: str, next_try_at: float) -> Report | None:
        with self._lock:
            report = self._reports.get(report_id)
            if report is None:
                return None
            return self._change(report_id, status=PENDING, error=error, next_try_at=next_try_at,
                                tries=report.tries + 1)

    def list_failed_reports(self) -> list[Report]:
        with self._lock:
            failed = [r for r in self._reports.values() if r.status == FAILED]
            return sorted(failed, key=lambda r: (r.week_start, r.id), reverse=True)

    def reset_report(self, report_id: str, now: float) -> Report | None:
        return self._change(report_id, status=PENDING, markdown=None, model=None, error=None, tries=0,
                            next_try_at=None, created_at=now)
