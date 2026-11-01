"""
Runs the cloud server's Store contract suite against a real Postgres, and checks the migrations.

Opt-in: it needs Docker and the postgres:17-alpine image.

    uv run pytest -m docker tests/docker/test_cloud_postgres_store.py

It starts one throwaway Postgres on a random port on this computer and removes it at the end, even when a test
fails. Each test gets its own empty database inside it.
"""

from __future__ import annotations

import secrets
import shutil
import subprocess
import threading
import time
import uuid
from collections.abc import Iterator

import pytest
import sqlalchemy as sa
from sqlalchemy import exc as sa_exc

from desktalk_cloud.store_postgres import MIGRATION_LOCK_NAME, PostgresStore, run_migrations
from tests.contracts.cloud_store_contract import CloudStoreContract, habit, link, make_user, save, state, totals

pytestmark = pytest.mark.docker

IMAGE = 'postgres:17-alpine'
READY_SEC = 60
LOCK_WAIT_SEC = 1.5            # how long we wait to see that a migration is stuck behind a held lock
FINISH_SEC = 30


def docker(*args: str, check: bool = True) -> str:
    done = subprocess.run(['docker', *args], capture_output=True, text=True, timeout=120, check=False)
    if check and done.returncode != 0:
        raise AssertionError(f'docker {" ".join(args)} failed:\n{done.stdout}\n{done.stderr}')
    return done.stdout.strip()


def wait_until_ready(url: str) -> None:
    """Postgres restarts once while the image sets itself up; only the final server listens on the network."""
    engine = sa.create_engine(url)
    deadline = time.monotonic() + READY_SEC
    try:
        while True:
            try:
                with engine.connect() as connection:
                    connection.execute(sa.text('SELECT 1'))
                return
            except sa_exc.OperationalError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.3)
    finally:
        engine.dispose()


@pytest.fixture(scope='module')
def server_url(request) -> Iterator[str]:
    """The address of a Postgres (no database name yet) that lives only as long as these tests."""
    if shutil.which('docker') is None:
        pytest.skip("Docker isn't installed")
    marks = request.config.getoption('markexpr') or ''
    if 'docker' not in marks or 'not docker' in marks:
        pytest.skip('opt-in: uv run pytest -m docker tests/docker/test_cloud_postgres_store.py')
    if subprocess.run(['docker', 'info'], capture_output=True, check=False).returncode != 0:
        pytest.skip("Docker isn't running")
    password = secrets.token_hex(8)
    container = docker('run', '--detach', '--rm', '--publish', '127.0.0.1::5432',
                       '--env', f'POSTGRES_PASSWORD={password}', IMAGE)
    try:
        port = docker('port', container, '5432/tcp').splitlines()[0].rsplit(':', 1)[1]
        url = f'postgresql+psycopg://postgres:{password}@127.0.0.1:{port}'
        wait_until_ready(f'{url}/postgres')
        yield url
    finally:
        docker('rm', '--force', '--volumes', container, check=False)


@pytest.fixture
def database_url(server_url) -> Iterator[str]:
    """A new empty database, dropped after the test."""
    name = f'test_{uuid.uuid4().hex}'
    admin = sa.create_engine(f'{server_url}/postgres', isolation_level='AUTOCOMMIT')
    with admin.connect() as connection:
        connection.execute(sa.text(f'CREATE DATABASE {name}'))
    try:
        yield f'{server_url}/{name}'
    finally:
        with admin.connect() as connection:
            connection.execute(sa.text(f'DROP DATABASE IF EXISTS {name} WITH (FORCE)'))
        admin.dispose()


class TestPostgresStore(CloudStoreContract):
    @pytest.fixture
    def store(self, database_url) -> Iterator[PostgresStore]:
        run_migrations(database_url)
        store = PostgresStore(database_url)
        try:
            yield store
        finally:
            store.dispose()

    # ── what only a real database can get wrong ───────────────────────────
    def test_events_come_back_exactly_as_they_went_in(self, store):
        device, _ = link(store, make_user(store))
        event = {'id': 'e1', 'ts': 1730000000.123456789, 'type': 'habit', 'big': 10 ** 22, 'tiny': 1e-7, 'whole': 3.0,
                 'neg': -0.1, 'flag': True, 'none': None, 'text': 'café \U0001f4a7 "quoted" \\ back',
                 'nested': {'z': [1, 2.5, {'a': None}], 'a': 'x'}, 'empty': {}, 'list': []}
        save(store, device, [event])
        [back] = store.events_after(device.id, 0.0, None, 10)
        assert back == event
        assert back['ts'] == event['ts']                       # the time is a double, so it is exact
        assert type(back['whole']) is float and type(back['big']) is int

    def test_a_huge_float_comes_back_as_a_whole_number_with_the_same_value(self, store):
        """Known limit of JSONB: a float Python writes as 1e+22 is kept as a number with no point and read back as
        an int. No real event holds a number that large, but a reader should call float() on its numbers."""
        device, _ = link(store, make_user(store))
        save(store, device, [{**habit('e1', 1.0), 'x': 1e22, 'y': 1.5e300}])
        [back] = store.events_after(device.id, 0.0, None, 10)
        assert float(back['x']) == 1e22 and float(back['y']) == 1.5e300

    def test_totals_come_back_exactly(self, store):
        device, _ = link(store, make_user(store))
        row = totals('2026-10-20', 1730000000.123456789, 250.5)
        save(store, device, totals_=[row])
        assert store.totals_between(device.id, '2026-10-20', '2026-10-20') == [row]

    def test_event_ids_are_ordered_as_plain_bytes(self, store):
        device, _ = link(store, make_user(store))
        ids = ['b', 'B', 'a', 'A', '_', '-', 'é', 'aa', 'a-b', 'ab']
        save(store, device, [habit(i, 10.0) for i in ids])
        got = [e['id'] for e in store.events_after(device.id, 0.0, None, 100)]
        assert got == sorted(ids, key=lambda i: i.encode('utf-8'))
        assert [e['id'] for e in store.events_after(device.id, 10.0, 'B', 100)] == got[got.index('B') + 1:]

    def test_labels_are_ordered_as_plain_bytes(self, store):
        device, _ = link(store, make_user(store))
        labels = ['cup', 'Cup', 'phone', '_x', 'zebra', 'apple']
        save(store, device, states=[state(label, 'on', 1.0) for label in labels])
        assert [s.label for s in store.object_states(device.id)] == sorted(labels)

    def test_a_batch_is_all_or_nothing(self, store):
        device, _ = link(store, make_user(store))
        bad = {**habit('e2', 11.0), 'ml': float('nan')}          # Postgres refuses NaN in JSON
        with pytest.raises(sa_exc.DBAPIError):
            store.save_batch(device.id, [habit('e1', 10.0), bad], [], [], True, 2000.0)
        assert store.events_after(device.id, 0.0, None, 10) == []
        assert store.get_device(device.id).last_seen_at is None

    def test_a_missing_device_changes_nothing(self, store):
        with pytest.raises(LookupError):
            store.save_batch('nobody', [habit('e1', 10.0)], [totals('2026-10-20', 1.0, 1)], [], False, 1.0)
        assert store.list_all_devices() == []

    def test_many_events_in_one_batch(self, store):
        device, _ = link(store, make_user(store))
        batch = [habit(f'e{i:05d}', float(i)) for i in range(5000)]
        assert save(store, device, batch) == (5000, 0)
        assert save(store, device, batch) == (0, 5000)

    def test_two_batches_at_once_for_one_device(self, store):
        device, _ = link(store, make_user(store))
        barrier = threading.Barrier(6)
        results: list[tuple[int, int]] = []

        def run(i: int) -> None:
            barrier.wait()
            same = [habit(f'same-{k}', float(k)) for k in range(20)]
            own = [habit(f'own-{i}-{k}', 100.0 + k) for k in range(20)]
            results.append(save(store, device, same + own, [totals('2026-10-20', float(i), i)],
                                [state('cup', f'v{i}', float(i))]))

        threads = [threading.Thread(target=run, args=(i,)) for i in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert len(results) == 6
        assert len(store.events_after(device.id, -1.0, None, 1000)) == 20 + 6 * 20 + 6
        [cup] = store.object_states(device.id)
        assert (cup.state, cup.since) == ('v5', 5.0)             # the newest wins whatever order they ran in
        [day] = store.totals_between(device.id, '2026-10-20', '2026-10-20')
        assert day['ts'] == 5.0

    def test_deleting_a_person_takes_every_row_of_theirs(self, store, database_url):
        user = make_user(store)
        device, _ = link(store, user)
        save(store, device, [habit('e1', 10.0)], [totals('2026-10-20', 1.0, 1)], [state('cup', 'full', 1.0)])
        store.create_report(device.id, '2026-10-19', 1.0)
        store.add_code('spare', user.id, device.id, 'UTC', 9999.0)
        store.delete_user(user.id)
        engine = sa.create_engine(database_url)
        with engine.connect() as connection:
            tables = connection.execute(sa.text(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' "
                "AND table_name NOT IN ('alembic_version', 'admins')")).scalars().all()
            counts = {t: connection.execute(sa.text(f'SELECT count(*) FROM {t}')).scalar_one() for t in tables}
        engine.dispose()
        assert counts and set(counts.values()) == {0}, counts

    def test_a_plain_postgresql_address_works_too(self, store, database_url):
        plain = database_url.replace('postgresql+psycopg://', 'postgresql://')
        other = PostgresStore(plain)
        try:
            assert other.list_users() == []
        finally:
            other.dispose()


class TestMigrations:
    def test_migrations_run_twice_safely(self, database_url):
        run_migrations(database_url)
        store = PostgresStore(database_url)
        user = make_user(store)
        run_migrations(database_url)                              # nothing is lost or made twice
        assert [u.id for u in store.list_users()] == [user.id]
        store.dispose()
        engine = sa.create_engine(database_url)
        with engine.connect() as connection:
            versions = connection.execute(sa.text('SELECT count(*) FROM alembic_version')).scalar_one()
        engine.dispose()
        assert versions == 1

    def test_the_tables_and_columns_the_spec_names_are_there(self, database_url):
        run_migrations(database_url)
        engine = sa.create_engine(database_url)
        found = sa.inspect(engine)
        columns = {t: {c['name'] for c in found.get_columns(t)} for t in found.get_table_names()}
        assert columns['reports'] >= {'tries', 'next_try_at', 'week_start', 'status', 'markdown', 'model', 'error'}
        assert columns['pairing_codes'] >= {'code_hash', 'user_id', 'device_id', 'time_zone', 'expires_at', 'used_at'}
        assert columns['devices'] >= {'time_zone', 'last_seen_at', 'caught_up_at', 'removed_at'}
        assert columns['events'] >= {'device_id', 'event_id', 'ts', 'type', 'payload', 'received_at'}
        assert columns['daily_totals'] >= {'device_id', 'day', 'payload', 'updated_at'}
        assert columns['device_state'] >= {'device_id', 'label', 'state', 'since', 'updated_at'}
        assert columns['device_tokens'] >= {'device_id', 'token_hash', 'revoked_at', 'last_used_at'}
        uniques = {t: [set(u['column_names']) for u in found.get_unique_constraints(t)] for t in columns}
        assert {'google_sub'} in uniques['users']
        assert {'device_id', 'week_start'} in uniques['reports']
        keys = {t: set(found.get_pk_constraint(t)['constrained_columns']) for t in ('events', 'daily_totals')}
        assert keys == {'events': {'device_id', 'event_id'}, 'daily_totals': {'device_id', 'day'}}
        assert any(set(i['column_names']) >= {'device_id', 'ts'} for i in found.get_indexes('events'))
        engine.dispose()

    def test_two_programs_starting_at_once_both_succeed(self, database_url):
        errors: list[BaseException] = []
        barrier = threading.Barrier(6)

        def start() -> None:
            barrier.wait()
            try:
                run_migrations(database_url)
            except BaseException as error:          # noqa: BLE001 - the test reports whatever went wrong
                errors.append(error)

        threads = [threading.Thread(target=start) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(FINISH_SEC)
        assert errors == []
        store = PostgresStore(database_url)
        assert store.list_users() == []
        store.dispose()

    def test_a_migration_waits_for_the_advisory_lock(self, database_url):
        """The lock is what makes two programs starting together safe, so pin that it is taken (and by this name)."""
        engine = sa.create_engine(database_url)
        done = threading.Event()
        errors: list[BaseException] = []

        def migrate() -> None:
            try:
                run_migrations(database_url)
            except BaseException as error:          # noqa: BLE001
                errors.append(error)
            finally:
                done.set()

        with engine.connect() as holder:
            holder.execute(sa.text('SELECT pg_advisory_lock(hashtext(:name))'), {'name': MIGRATION_LOCK_NAME})
            thread = threading.Thread(target=migrate)
            thread.start()
            assert not done.wait(LOCK_WAIT_SEC), 'the migration ran although the lock was held'
            holder.execute(sa.text('SELECT pg_advisory_unlock(hashtext(:name))'), {'name': MIGRATION_LOCK_NAME})
        assert done.wait(FINISH_SEC), 'the migration did not go on after the lock was let go'
        thread.join()
        assert errors == []
        assert 'users' in sa.inspect(engine).get_table_names()
        engine.dispose()
