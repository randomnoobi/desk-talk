"""The Store kept in Postgres (SQLAlchemy Core, no ORM), and run_migrations() that makes its tables.

It passes the same contract suite as the in-memory store (tests/contracts/cloud_store_contract.py), run against a real
Postgres by tests/docker/test_cloud_postgres_store.py. The methods block, and the routes call them off the event loop.
The connection pool uses SQLAlchemy's own sizes: config.py has no setting for them yet.

How it keeps to the contract:
- Event ids and object labels are `COLLATE "C"` columns, so Postgres orders them as plain bytes, as Python does.
- A batch is one transaction. It starts by updating the Pi's row, which makes a second batch for the same Pi wait.
- Every write that touches a code, a Pi and its tokens takes them in that one order (code, then Pi, then tokens), so
  two calls that meet (using a code, removing or deleting a Pi or a person) wait for each other and never deadlock.
- A repeat event is `INSERT ... ON CONFLICT DO NOTHING`; a state or a day's totals is `ON CONFLICT DO UPDATE ... WHERE`
  the new time is later. Using a code is one `UPDATE ... RETURNING`, so only one of two at once wins.
- Times are `double precision` and an event's payload is JSONB. JSONB does not keep the order of keys.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy import exc as sa_exc
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Connection, Engine, RowMapping

from desktalk_cloud.domain import DONE, FAILED, NO_DATA, PENDING, Admin, Device, ObjectState, Report, StateChange, User

# The migrations take this advisory lock (by name), so two programs starting together (the website and the report
# writer) don't both try to make the tables.
MIGRATION_LOCK_NAME = 'desktalk_cloud_migrations'

_FINAL = frozenset({DONE, NO_DATA, FAILED})
_TIME = postgresql.DOUBLE_PRECISION
_BYTES_ORDER = 'C'
_FOREIGN_KEY_REFUSED = '23503'          # Postgres' error code for a row that names somebody who isn't there

_meta = sa.MetaData()


def _table(name: str, *columns: sa.Column | sa.PrimaryKeyConstraint) -> sa.Table:
    return sa.Table(name, _meta, *columns)


_users = _table('users', sa.Column('id', sa.Text), sa.Column('google_sub', sa.Text), sa.Column('email', sa.Text),
                sa.Column('name', sa.Text), sa.Column('created_at', _TIME))
_admins = _table('admins', sa.Column('username', sa.Text), sa.Column('password_hash', sa.Text))
_devices = _table('devices', sa.Column('id', sa.Text), sa.Column('user_id', sa.Text), sa.Column('name', sa.Text),
                  sa.Column('time_zone', sa.Text), sa.Column('created_at', _TIME), sa.Column('last_seen_at', _TIME),
                  sa.Column('caught_up_at', _TIME), sa.Column('removed_at', _TIME))
_tokens = _table('device_tokens', sa.Column('id', sa.Text), sa.Column('device_id', sa.Text),
                 sa.Column('token_hash', sa.Text), sa.Column('created_at', _TIME), sa.Column('last_used_at', _TIME),
                 sa.Column('revoked_at', _TIME))
_codes = _table('pairing_codes', sa.Column('code_hash', sa.Text), sa.Column('user_id', sa.Text),
                sa.Column('device_id', sa.Text), sa.Column('time_zone', sa.Text), sa.Column('expires_at', _TIME),
                sa.Column('used_at', _TIME))
_events = _table('events', sa.Column('device_id', sa.Text), sa.Column('event_id', sa.Text(collation=_BYTES_ORDER)),
                 sa.Column('ts', _TIME), sa.Column('type', sa.Text), sa.Column('payload', postgresql.JSONB),
                 sa.Column('received_at', _TIME), sa.PrimaryKeyConstraint('device_id', 'event_id'))
_totals = _table('daily_totals', sa.Column('device_id', sa.Text), sa.Column('day', sa.Text), sa.Column('ts', _TIME),
                 sa.Column('payload', postgresql.JSONB), sa.Column('updated_at', _TIME),
                 sa.PrimaryKeyConstraint('device_id', 'day'))
_states = _table('device_state', sa.Column('device_id', sa.Text), sa.Column('label', sa.Text(collation=_BYTES_ORDER)),
                 sa.Column('state', sa.Text), sa.Column('since', _TIME), sa.Column('updated_at', _TIME),
                 sa.PrimaryKeyConstraint('device_id', 'label'))
_reports = _table('reports', sa.Column('id', sa.Text), sa.Column('device_id', sa.Text),
                  sa.Column('week_start', sa.Text), sa.Column('status', sa.Text), sa.Column('markdown', sa.Text),
                  sa.Column('model', sa.Text),
                  sa.Column('error', sa.Text), sa.Column('created_at', _TIME), sa.Column('tries', sa.Integer),
                  sa.Column('next_try_at', _TIME))


def _driver_url(database_url: str) -> str:
    """The address as SQLAlchemy wants it: a plain `postgresql://` or `postgres://` one uses the psycopg 3 driver."""
    for plain in ('postgresql://', 'postgres://'):
        if database_url.startswith(plain):
            return 'postgresql+psycopg://' + database_url[len(plain):]
    return database_url


def run_migrations(database_url: str) -> None:
    """Bring the database up to the newest tables. Safe to run again, and safe when two programs run it at once: each
    takes a Postgres advisory lock first, so the second waits and then finds nothing left to do."""
    engine = sa.create_engine(_driver_url(database_url))
    config = Config()
    config.set_main_option('script_location', str(Path(__file__).parent / 'migrations'))
    try:
        with engine.begin() as connection:
            # held until this transaction ends, which is after the tables are made
            connection.execute(sa.text('SELECT pg_advisory_xact_lock(hashtext(:name))'), {'name': MIGRATION_LOCK_NAME})
            config.attributes['connection'] = connection
            command.upgrade(config, 'head')
    finally:
        engine.dispose()


def _lookup_error(error: sa_exc.IntegrityError, message: str) -> Exception:
    """A missing person or Pi (a foreign key refused) is a LookupError, as in the in-memory store; any other
    refusal is passed on as it is."""
    if getattr(error.orig, 'sqlstate', None) == _FOREIGN_KEY_REFUSED:
        return LookupError(message)
    return error


def _bytes_order(column: sa.ColumnElement[str]) -> sa.ColumnElement[str]:
    return sa.collate(column, _BYTES_ORDER)


def _user(row: RowMapping) -> User:
    return User(id=row['id'], google_sub=row['google_sub'], email=row['email'], name=row['name'],
                created_at=row['created_at'])


def _device(row: RowMapping) -> Device:
    return Device(id=row['id'], user_id=row['user_id'], name=row['name'], time_zone=row['time_zone'],
                  created_at=row['created_at'], last_seen_at=row['last_seen_at'], caught_up_at=row['caught_up_at'],
                  removed_at=row['removed_at'])


def _report(row: RowMapping) -> Report:
    return Report(id=row['id'], device_id=row['device_id'], week_start=row['week_start'], status=row['status'],
                  markdown=row['markdown'], model=row['model'], error=row['error'], created_at=row['created_at'],
                  tries=row['tries'], next_try_at=row['next_try_at'])


def _is_change_of(change: StateChange, event: Mapping[str, Any]) -> bool:
    """True when `change` says what the kept `event` says (so it belongs to the instance that was kept)."""
    same_words = event.get('object') == change.label and event.get('new_state') == change.state
    return same_words and event['ts'] == change.since


class PostgresStore:
    def __init__(self, database_url: str) -> None:
        # hide_parameters: a database error's text must not carry the values sent (event text, token and password
        # hashes), because it ends up in the logs
        self._engine: Engine = sa.create_engine(_driver_url(database_url), pool_pre_ping=True, hide_parameters=True)

    def dispose(self) -> None:
        """Close every connection in the pool."""
        self._engine.dispose()

    # ── people and admins ─────────────────────────────────────────────────
    def upsert_user(self, google_sub: str, email: str, name: str, now: float) -> User:
        insert = postgresql.insert(_users).values(id=str(uuid.uuid4()), google_sub=google_sub, email=email, name=name,
                                                  created_at=now)
        statement = insert.on_conflict_do_update(index_elements=[_users.c.google_sub],
                                                 set_={'email': insert.excluded.email, 'name': insert.excluded.name})
        with self._engine.begin() as connection:
            return _user(connection.execute(statement.returning(*_users.c)).mappings().one())

    def get_user(self, user_id: str) -> User | None:
        with self._engine.connect() as connection:
            row = connection.execute(sa.select(_users).where(_users.c.id == user_id)).mappings().first()
        return _user(row) if row else None

    def delete_user(self, user_id: str) -> bool:
        """Their Pis go with them, and so do their Pis' events, totals, states, reports, tokens and codes. The codes go
        first (the order is code, Pi, tokens), then the person, whose Pis and the rest follow by cascade."""
        with self._engine.begin() as connection:
            their_pis = sa.select(_devices.c.id).where(_devices.c.user_id == user_id)
            connection.execute(sa.delete(_codes).where(sa.or_(_codes.c.user_id == user_id,
                                                               _codes.c.device_id.in_(their_pis))))
            return connection.execute(sa.delete(_users).where(_users.c.id == user_id)).rowcount > 0

    def list_users(self) -> list[User]:
        query = sa.select(_users).order_by(_users.c.created_at, _bytes_order(_users.c.id))
        with self._engine.connect() as connection:
            return [_user(row) for row in connection.execute(query).mappings()]

    def get_admin(self, username: str) -> Admin | None:
        with self._engine.connect() as connection:
            row = connection.execute(sa.select(_admins).where(_admins.c.username == username)).mappings().first()
        return Admin(username=row['username'], password_hash=row['password_hash']) if row else None

    def create_admin_if_missing(self, username: str, password_hash: str) -> bool:
        statement = postgresql.insert(_admins).values(username=username, password_hash=password_hash)
        with self._engine.begin() as connection:
            made = connection.execute(statement.on_conflict_do_nothing().returning(_admins.c.username)).first()
        return made is not None

    # ── linking ───────────────────────────────────────────────────────────
    def add_code(self, code_hash: str, user_id: str, device_id: str | None, time_zone: str, expires_at: float) -> None:
        statement = postgresql.insert(_codes).values(code_hash=code_hash, user_id=user_id, device_id=device_id,
                                                     time_zone=time_zone, expires_at=expires_at)
        try:
            with self._engine.begin() as connection:
                made = connection.execute(statement.on_conflict_do_nothing().returning(_codes.c.code_hash)).first()
        except sa_exc.IntegrityError as error:
            raise _lookup_error(error, 'no such person or Pi') from error
        if made is None:
            raise ValueError('that code already exists')

    def claim_code(self, code_hash: str, name: str, token_hash: str, now: float) -> Device | None:
        with self._engine.begin() as connection:
            # one statement decides the winner: a second claim finds used_at already set and gets nothing
            used = connection.execute(
                sa.update(_codes)
                .where(_codes.c.code_hash == code_hash, _codes.c.used_at.is_(None), _codes.c.expires_at > now)
                .values(used_at=now)
                .returning(_codes.c.user_id, _codes.c.device_id, _codes.c.time_zone)).mappings().first()
            if used is None:
                return None
            if used['device_id'] is None:
                device_row = connection.execute(
                    sa.insert(_devices).values(id=str(uuid.uuid4()), user_id=used['user_id'], name=name,
                                               time_zone=used['time_zone'], created_at=now)
                    .returning(*_devices.c)).mappings().one()
            else:
                device_row = connection.execute(
                    sa.update(_devices)
                    .where(_devices.c.id == used['device_id'], _devices.c.user_id == used['user_id'])
                    .values(removed_at=None, time_zone=used['time_zone'])
                    .returning(*_devices.c)).mappings().first()
                if device_row is None:
                    return None             # the code is used up, as in the in-memory store
                self._cancel_tokens(connection, device_row['id'], now)
            connection.execute(sa.insert(_tokens).values(id=str(uuid.uuid4()), device_id=device_row['id'],
                                                         token_hash=token_hash, created_at=now))
            return _device(device_row)

    @staticmethod
    def _cancel_tokens(connection: Connection, device_id: str, now: float) -> None:
        connection.execute(sa.update(_tokens)
                           .where(_tokens.c.device_id == device_id, _tokens.c.revoked_at.is_(None))
                           .values(revoked_at=now))

    # ── Pis ───────────────────────────────────────────────────────────────
    def get_device(self, device_id: str) -> Device | None:
        with self._engine.connect() as connection:
            row = connection.execute(sa.select(_devices).where(_devices.c.id == device_id)).mappings().first()
        return _device(row) if row else None

    def list_devices(self, user_id: str) -> list[Device]:
        return self._devices(_devices.c.user_id == user_id)

    def list_all_devices(self) -> list[Device]:
        return self._devices()

    def _devices(self, *where: sa.ColumnElement[bool]) -> list[Device]:
        query = sa.select(_devices).where(*where).order_by(_devices.c.created_at, _bytes_order(_devices.c.id))
        with self._engine.connect() as connection:
            return [_device(row) for row in connection.execute(query).mappings()]

    def rename_device(self, device_id: str, name: str) -> Device | None:
        with self._engine.begin() as connection:
            row = connection.execute(sa.update(_devices).where(_devices.c.id == device_id).values(name=name)
                                     .returning(*_devices.c)).mappings().first()
        return _device(row) if row else None

    def remove_device(self, device_id: str, now: float) -> None:
        with self._engine.begin() as connection:
            # code, then Pi, then tokens. A claim in flight holds its code, so this waits for it to finish, and the
            # cancel below (a new statement) then sees the token that claim made.
            connection.execute(sa.delete(_codes).where(_codes.c.device_id == device_id, _codes.c.used_at.is_(None)))
            connection.execute(sa.update(_devices)
                               .where(_devices.c.id == device_id, _devices.c.removed_at.is_(None))
                               .values(removed_at=now))
            self._cancel_tokens(connection, device_id, now)

    def delete_device(self, device_id: str) -> bool:
        with self._engine.begin() as connection:
            # codes first, then the Pi
            connection.execute(sa.delete(_codes).where(_codes.c.device_id == device_id))
            return connection.execute(sa.delete(_devices).where(_devices.c.id == device_id)).rowcount > 0

    def device_for_token(self, token_hash: str, now: float) -> Device | None:
        live = sa.select(_devices.c.id).where(_devices.c.id == _tokens.c.device_id, _devices.c.removed_at.is_(None))
        with self._engine.begin() as connection:
            owner = connection.execute(
                sa.update(_tokens)
                .where(_tokens.c.token_hash == token_hash, _tokens.c.revoked_at.is_(None), live.exists())
                .values(last_used_at=now).returning(_tokens.c.device_id)).scalar_one_or_none()
            if owner is None:
                return None
            row = connection.execute(sa.select(_devices).where(_devices.c.id == owner)).mappings().one()
        return _device(row)

    # ── events ────────────────────────────────────────────────────────────
    def save_batch(self, device_id: str, events: Sequence[Mapping[str, Any]], totals: Sequence[Mapping[str, Any]],
                   state_changes: Sequence[StateChange], caught_up: bool, now: float) -> tuple[int, int]:
        with self._engine.begin() as connection:
            # first, so a missing Pi stops us before anything is written, and a second batch for this Pi waits
            seen = connection.execute(
                sa.update(_devices).where(_devices.c.id == device_id)
                .values(last_seen_at=now, caught_up_at=sa.case((sa.literal(caught_up), now),
                                                               else_=_devices.c.caught_up_at))
                .returning(_devices.c.id)).first()
            if seen is None:
                raise LookupError(f'no such Pi: {device_id}')
            kept = self._save_events(connection, device_id, events, now)
            accepted = len(kept)
            repeats = len(events) - accepted
            for event in totals:
                accepted_total = self._save_total(connection, device_id, event, now)
                accepted += accepted_total
                repeats += 1 - accepted_total
            for change in state_changes:
                event = kept.get(change.event_id)
                if event is not None and _is_change_of(change, event):
                    self._save_state(connection, device_id, change, now)
            return accepted, repeats

    @staticmethod
    def _save_events(connection: Connection, device_id: str, events: Sequence[Mapping[str, Any]],
                     now: float) -> dict[str, Mapping[str, Any]]:
        """Keep the new events. Gives the ones newly kept, by id; where an id comes twice in a batch, the first wins."""
        first: dict[str, Mapping[str, Any]] = {}
        for event in events:
            first.setdefault(event['id'], event)
        if not events:
            return {}
        rows = [{'device_id': device_id, 'event_id': event['id'], 'ts': event['ts'], 'type': event['type'],
                 'payload': dict(event), 'received_at': now} for event in events]
        inserted = connection.execute(
            postgresql.insert(_events).on_conflict_do_nothing().returning(_events.c.event_id), rows).scalars().all()
        return {event_id: first[event_id] for event_id in inserted}

    @staticmethod
    def _save_total(connection: Connection, device_id: str, event: Mapping[str, Any], now: float) -> int:
        insert = postgresql.insert(_totals).values(device_id=device_id, day=event['day'], ts=event['ts'],
                                                   payload=dict(event), updated_at=now)
        statement = insert.on_conflict_do_update(
            index_elements=[_totals.c.device_id, _totals.c.day],
            set_={'ts': insert.excluded.ts, 'payload': insert.excluded.payload,
                  'updated_at': insert.excluded.updated_at},
            where=_totals.c.ts < insert.excluded.ts)
        return 1 if connection.execute(statement.returning(_totals.c.day)).first() is not None else 0

    @staticmethod
    def _save_state(connection: Connection, device_id: str, change: StateChange, now: float) -> None:
        insert = postgresql.insert(_states).values(device_id=device_id, label=change.label, state=change.state,
                                                   since=change.since, updated_at=now)
        connection.execute(insert.on_conflict_do_update(
            index_elements=[_states.c.device_id, _states.c.label],
            set_={'state': insert.excluded.state, 'since': insert.excluded.since,
                  'updated_at': insert.excluded.updated_at},
            where=_states.c.since < insert.excluded.since))

    def _event_list(self, query: sa.Select[Any]) -> list[dict[str, Any]]:
        with self._engine.connect() as connection:
            return [row for row in connection.execute(query).scalars()]

    def events_after(self, device_id: str, since: float, after_id: str | None, limit: int) -> list[dict[str, Any]]:
        if after_id is None:
            later = _events.c.ts > since
        else:       # one comparison of the pair, so the index (device, ts, id) starts right after the last one seen
            later = sa.tuple_(_events.c.ts, _events.c.event_id) > sa.tuple_(since, after_id)
        return self._event_list(sa.select(_events.c.payload).where(_events.c.device_id == device_id, later)
                                .order_by(_events.c.ts, _events.c.event_id).limit(limit))

    def events_between(self, device_id: str, start: float, end: float) -> list[dict[str, Any]]:
        return self._event_list(sa.select(_events.c.payload)
                                .where(_events.c.device_id == device_id, _events.c.ts >= start, _events.c.ts < end)
                                .order_by(_events.c.ts, _events.c.event_id))

    def latest_event(self, device_id: str, event_type: str) -> dict[str, Any] | None:
        found = self._event_list(sa.select(_events.c.payload)
                                 .where(_events.c.device_id == device_id, _events.c.type == event_type)
                                 .order_by(_events.c.ts.desc(), _events.c.event_id.desc()).limit(1))
        return found[0] if found else None

    def object_states(self, device_id: str) -> list[ObjectState]:
        query = sa.select(_states).where(_states.c.device_id == device_id).order_by(_states.c.label)
        with self._engine.connect() as connection:
            return [ObjectState(label=row['label'], state=row['state'], since=row['since'],
                                updated_at=row['updated_at']) for row in connection.execute(query).mappings()]

    def totals_between(self, device_id: str, first_day: str, last_day: str) -> list[dict[str, Any]]:
        query = (sa.select(_totals.c.payload)
                 .where(_totals.c.device_id == device_id, _bytes_order(_totals.c.day) >= first_day,
                        _bytes_order(_totals.c.day) <= last_day)
                 .order_by(_bytes_order(_totals.c.day)))
        with self._engine.connect() as connection:
            return list(connection.execute(query).scalars())

    def delete_events_before(self, ts: float) -> int:
        with self._engine.begin() as connection:
            return connection.execute(sa.delete(_events).where(_events.c.ts < ts)).rowcount

    # ── reports ───────────────────────────────────────────────────────────
    def create_report(self, device_id: str, week_start: str, now: float) -> Report | None:
        statement = (postgresql.insert(_reports)
                     .values(id=str(uuid.uuid4()), device_id=device_id, week_start=week_start, status=PENDING,
                             created_at=now, tries=0)
                     .on_conflict_do_nothing().returning(*_reports.c))
        try:
            with self._engine.begin() as connection:
                row = connection.execute(statement).mappings().first()
        except sa_exc.IntegrityError as error:
            raise _lookup_error(error, f'no such Pi: {device_id}') from error
        return _report(row) if row else None

    def _one_report(self, *where: sa.ColumnElement[bool]) -> Report | None:
        with self._engine.connect() as connection:
            row = connection.execute(sa.select(_reports).where(*where)).mappings().first()
        return _report(row) if row else None

    def get_report(self, device_id: str, week_start: str) -> Report | None:
        return self._one_report(_reports.c.device_id == device_id, _reports.c.week_start == week_start)

    def get_report_by_id(self, report_id: str) -> Report | None:
        return self._one_report(_reports.c.id == report_id)

    def _reports(self, *where: sa.ColumnElement[bool], order: Sequence[sa.ColumnElement[Any]]) -> list[Report]:
        with self._engine.connect() as connection:
            return [_report(row) for row in
                    connection.execute(sa.select(_reports).where(*where).order_by(*order)).mappings()]

    def list_reports(self, device_id: str) -> list[Report]:
        return self._reports(_reports.c.device_id == device_id, order=[_bytes_order(_reports.c.week_start).desc()])

    def reports_to_write(self, now: float) -> list[Report]:
        due = sa.or_(_reports.c.next_try_at.is_(None), _reports.c.next_try_at <= now)
        return self._reports(_reports.c.status == PENDING, due,
                             order=[_reports.c.created_at, _bytes_order(_reports.c.id)])

    def _change_report(self, report_id: str, *, only_pending: bool, **values: Any) -> Report | None:
        """Change a report. With `only_pending` a report that is not pending is left as it is (and given back so)."""
        where = [_reports.c.id == report_id]
        if only_pending:
            where.append(_reports.c.status == PENDING)
        with self._engine.begin() as connection:
            row = connection.execute(sa.update(_reports).where(*where).values(**values)
                                     .returning(*_reports.c)).mappings().first()
        if row is None:
            return self.get_report_by_id(report_id) if only_pending else None
        return _report(row)

    def finish_report(self, report_id: str, status: str, markdown: str | None, model: str | None, error: str | None,
                      now: float) -> Report | None:
        if status not in _FINAL:
            raise ValueError(f'a finished report is done, no_data or failed, not {status!r}')
        return self._change_report(report_id, only_pending=True, status=status, markdown=markdown, model=model,
                                   error=error, next_try_at=None)

    def retry_report(self, report_id: str, error: str, next_try_at: float) -> Report | None:
        return self._change_report(report_id, only_pending=True, status=PENDING, error=error,
                                   next_try_at=next_try_at, tries=_reports.c.tries + 1)

    def list_failed_reports(self) -> list[Report]:
        return self._reports(_reports.c.status == FAILED,
                             order=[_bytes_order(_reports.c.week_start).desc(), _bytes_order(_reports.c.id).desc()])

    def reset_report(self, report_id: str, now: float) -> Report | None:
        return self._change_report(report_id, only_pending=False, status=PENDING, markdown=None, model=None,
                                   error=None, tries=0, next_try_at=None)
