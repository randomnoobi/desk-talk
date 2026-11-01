"""
Starts the whole cloud server (cloud/compose.yaml) and checks it from the outside, through the website's port.

Opt-in: it needs Docker, and the internet only to pull images and packages the first time.

    uv run pytest -m docker tests/docker/test_cloud_stack.py

It has its own compose project name, port and network, and never reads cloud/.env, so it can't touch a server you
already run. Its containers and database volume are removed at the end, even when a test fails. Nothing in it contacts
Google or an AI company: the weekly report goes to tests/docker/cloud_fake_ai.py, one more container on the stack's own
network. The tests run in order, and the ones that stop parts of the stack come last.
"""

from __future__ import annotations

import base64
import json
import os
import random
import secrets
import shutil
import socket
import subprocess
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml
from itsdangerous import TimestampSigner

from desktalk_cloud.config import ENV_NAMES
from desktalk_cloud.report_times import latest_due, week_of, zone_of
from tests.fakes.cloud import CLOUD_FOLDER, cloud_config

pytestmark = pytest.mark.docker

PROJECT = 'desktalk-cloud-test'
FAKE_AI = Path(__file__).with_name('cloud_fake_ai.py')
FAKE_AI_PORT = 9000
BUILD_AND_START_SEC = 1800          # the first build downloads the packages and npm's
COMPOSE_SEC = 180
REPORT_WITHIN_SEC = 60
SECRET = secrets.token_hex(32)
DB_PASSWORD = secrets.token_hex(16)
AI_KEY = secrets.token_hex(16)
ADMIN, ADMIN_PASSWORD = 'admin', secrets.token_hex(16)
PLAIN_ZONE = 'UTC'
DAY_SEC = 86_400


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


@dataclass(frozen=True)
class Stack:
    files: tuple[Path, ...]
    env_file: Path
    url: str
    web_address: str

    def compose(self, *args: str, timeout: float = COMPOSE_SEC, check: bool = True, sent_in: str | None = None) -> str:
        files = [item for path in self.files for item in ('-f', str(path))]
        # Without these the names of a real server in this shell would win over the test's file.
        env = {name: value for name, value in os.environ.items() if name not in ENV_NAMES}
        done = subprocess.run(['docker', 'compose', '-p', PROJECT, '--env-file', str(self.env_file), *files, *args],
                              capture_output=True, text=True, timeout=timeout, check=False, env=env,
                              input=sent_in)
        if check and done.returncode != 0:
            raise AssertionError(f'docker compose {" ".join(args)} failed:\n{done.stdout}\n{done.stderr}')
        return done.stdout.strip()

    def logs(self, *services: str) -> str:
        return self.compose('logs', '--no-color', *services)

    def python_in(self, service: str, code: str, *args: str) -> str:
        return self.compose('exec', '-T', service, 'python', '-c', code, *args)

    def container(self, service: str) -> str:
        return self.compose('ps', '--all', '--quiet', service)

    def client(self) -> httpx.Client:
        return httpx.Client(base_url=self.url, timeout=30, follow_redirects=False)


def overrides() -> dict[str, Any]:
    """The report job with a fake AI company, and a short wait between its rounds. Their text goes into the containers
    with compose's `configs`, so nothing needs a folder shared with Docker."""
    settings = yaml.safe_load((CLOUD_FOLDER / 'settings.yaml').read_text(encoding='utf-8'))
    settings['reports'].update(provider='openai_compatible', model='fake-model', check_every_min=0.02,
                               url=f'http://fake-ai:{FAKE_AI_PORT}/chat/completions')
    python_image = 'python:3.12.14-slim-bookworm'
    return {
        'configs': {'report_settings': {'content': yaml.safe_dump(settings)},
                    'fake_ai': {'content': FAKE_AI.read_text(encoding='utf-8')}},
        'services': {
            'reports': {'configs': [{'source': 'report_settings', 'target': '/config/settings.yaml'}]},
            'fake-ai': {
                'image': python_image, 'user': 'nobody', 'command': ['python', '/fake_ai.py'],
                'environment': {'FAKE_AI_KEY': AI_KEY, 'FAKE_AI_PORT': str(FAKE_AI_PORT),
                                'PYTHONDONTWRITEBYTECODE': '1'},
                'configs': [{'source': 'fake_ai', 'target': '/fake_ai.py'}],
                'healthcheck': {'test': ['CMD', 'python', '-c', 'import urllib.request; urllib.request.urlopen('
                                         f'"http://127.0.0.1:{FAKE_AI_PORT}/")'],
                                'interval': '1s', 'retries': 30},
            },
        },
    }


@pytest.fixture(scope='module')
def stack(request, tmp_path_factory) -> Iterator[Stack]:
    if shutil.which('docker') is None:
        pytest.skip("Docker isn't installed")
    marks = request.config.getoption('markexpr') or ''
    if 'docker' not in marks or 'not docker' in marks:
        pytest.skip('opt-in: uv run pytest -m docker tests/docker/test_cloud_stack.py')
    if subprocess.run(['docker', 'info'], capture_output=True, check=False).returncode != 0:
        pytest.skip("Docker isn't running")
    port, network = free_port(), random.randint(100, 199)
    url, web_address = f'http://127.0.0.1:{port}', f'172.30.{network}.200'
    folder = tmp_path_factory.mktemp('cloud_stack')
    env_file = folder / 'test.env'
    env_file.write_text('\n'.join(f'{name}={value}' for name, value in {
        'SESSION_SECRET': SECRET, 'POSTGRES_PASSWORD': DB_PASSWORD, 'PUBLIC_URL': url, 'LOGIN_URL': url,
        'GOOGLE_CLIENT_ID': 'test-id', 'GOOGLE_CLIENT_SECRET': 'test-secret', 'ADMIN_USERNAME': ADMIN,
        'ADMIN_PASSWORD': ADMIN_PASSWORD, 'REPORT_API_KEY': AI_KEY, 'CHAT_API_KEY': '',
        'WEB_PORT': f'127.0.0.1:{port}', 'NETWORK_SUBNET': f'172.30.{network}.0/24',
        'NETWORK_ADDRESSES': f'172.30.{network}.0/25', 'WEB_CONTAINER_ADDRESS': web_address,
    }.items()) + '\n', encoding='utf-8')
    override = folder / 'override.json'
    override.write_text(json.dumps(overrides()), encoding='utf-8')
    running = Stack((CLOUD_FOLDER / 'compose.yaml', override), env_file, url, web_address)
    try:
        running.compose('up', '--detach', '--build', '--wait', timeout=BUILD_AND_START_SEC)
        yield running
    finally:
        running.compose('down', '--volumes', '--timeout', '5', check=False)


# ── helpers ────────────────────────────────────────────────────────────
SEED = '''
import json, os, sys
from desktalk_cloud.store_postgres import PostgresStore
from desktalk_cloud.tokens import hash_secret, new_code, new_token
asked = json.loads(sys.argv[1])
store = PostgresStore(os.environ['DATABASE_URL'])
now = asked['now']
user = store.upsert_user(asked['who'], asked['who'] + '@example.com', asked['who'], now)
code = new_code()
store.add_code(hash_secret(code), user.id, None, 'UTC', now + 86400)
reply = {'user_id': user.id, 'code': code}
if asked['claim']:
    token = new_token()
    device = store.claim_code(hash_secret(code), 'Old Pi', hash_secret(token), now)
    reply.update(token=token, device_id=device.id)
print(json.dumps(reply))
store.dispose()
'''


def seed(stack: Stack, who: str, *, now: float | None = None, claim: bool = False) -> dict[str, str]:
    """A person and a one-time code, made through the store inside the `api` container. With `claim`, a Pi linked at
    `now` too, so a test can have a Pi that was linked last week."""
    asked = {'who': who, 'now': now or time.time(), 'claim': claim}
    return json.loads(stack.python_in('api', SEED, json.dumps(asked)).splitlines()[-1])


def login_cookie(user_id: str) -> dict[str, str]:
    """The login cookie the server would set, signed with the stack's secret (the site's real login needs Google)."""
    data = base64.b64encode(json.dumps({'user_id': user_id}).encode())
    return {cloud_config().server.cookie_name: TimestampSigner(SECRET).sign(data).decode()}


def bearer(token: str) -> dict[str, str]:
    return {'Authorization': f'Bearer {token}'}


def sip(event_id: str, ts: float) -> dict[str, Any]:
    return {'id': event_id, 'ts': ts, 'type': 'habit', 'habit': 'water', 'action': 'sip', 'ml': 30}


def claim(stack: Stack, code: str) -> dict[str, str]:
    with stack.client() as client:
        reply = client.post('/v1/devices/claim', json={'code': code, 'name': 'Desk Pi'})
    assert reply.status_code == 200, reply.text
    return reply.json()


def wrong_admin_login(client: httpx.Client, **headers: str) -> int:
    return client.post('/auth/admin/login', json={'username': 'nobody', 'password': 'wrong'},
                       headers=headers).status_code


# ── it starts ──────────────────────────────────────────────────────────
def test_the_stack_starts_healthy_with_migrations_run(stack):
    rows = [json.loads(line) for line in stack.compose('ps', '--all', '--format', 'json').splitlines()]
    assert {row['Service'] for row in rows} == {'postgres', 'api', 'web', 'reports', 'fake-ai'}
    for row in rows:
        assert row['State'] == 'running' and row['Health'] == 'healthy', row
    version = stack.compose('exec', '-T', 'postgres', 'psql', '-U', 'desktalk', '-d', 'desktalk', '-tAc',
                            'SELECT version_num FROM alembic_version')
    assert version.strip()


def test_there_is_one_report_writer_and_nothing_runs_as_root(stack):
    assert len(stack.container('reports').splitlines()) == 1
    for service in ('api', 'reports', 'web'):
        assert stack.compose('exec', '-T', service, 'id', '-u') != '0', service


def test_postgres_and_api_ports_are_closed(stack):
    for service in ('postgres', 'api', 'reports'):
        ports = json.loads(subprocess.run(
            ['docker', 'inspect', '-f', '{{json .NetworkSettings.Ports}}', stack.container(service)],
            capture_output=True, text=True, check=True).stdout)
        assert not [bound for bound in ports.values() if bound], f'{service} publishes {ports}'
    with stack.client() as client:
        assert client.get('/healthz').status_code == 404        # nginx passes on only /api, /auth and /v1


def test_the_website_is_served_and_the_three_paths_reach_the_server(stack):
    with stack.client() as client:
        page = client.get('/')
        assert page.status_code == 200 and '<div id="root">' in page.text
        assert client.get('/api/me').status_code == 401
        assert client.get('/v1/devices/nope/state').status_code == 401
        login = client.get('/auth/google/login')
        assert login.status_code == 302
        assert login.headers['location'].startswith(cloud_config().google.auth_url)
        assert f'redirect_uri={stack.url.replace(":", "%3A").replace("/", "%2F")}' in login.headers['location']


# ── what a person and a Pi do ──────────────────────────────────────────
def test_upload_then_read_back(stack):
    now = time.time()
    linked = claim(stack, seed(stack, 'google-upload')['code'])
    with stack.client() as client:
        sent = client.post('/v1/events', headers=bearer(linked['token']), json={'events': [sip('e1', now - 60)]})
        assert sent.status_code == 200, sent.text
        read = client.get(f'/v1/devices/{linked["device_id"]}/events', headers=bearer(linked['token']))
    assert read.status_code == 200
    assert [event['id'] for event in read.json()['events']] == ['e1']


def test_a_person_can_make_a_code_for_a_time_zone(stack):
    """Fails when the image has no time zone data, because every zone is then refused."""
    user = seed(stack, 'google-zone')
    with stack.client() as client:
        client.cookies.update(login_cookie(user['user_id']))
        reply = client.post('/api/devices/code', json={'time_zone': 'Asia/Tokyo'})
    assert reply.status_code == 200, reply.text
    assert stack.url in reply.json()['command']


def test_report_from_fake_ai_appears(stack):
    settings = cloud_config().reports
    now, zone = time.time(), zone_of(PLAIN_ZONE)
    due = latest_due(now, zone, settings.weekday, settings.hour)
    start, end, _ = week_of(due, zone, settings.weekday, settings.hour)
    pi = seed(stack, 'google-report', now=start - DAY_SEC, claim=True)
    with stack.client() as client:
        sent = client.post('/v1/events', headers=bearer(pi['token']),
                           json={'events': [sip('week-1', end - 3600)], 'caught_up': True})
        assert sent.status_code == 200, sent.text
        client.cookies.update(login_cookie(pi['user_id']))
        deadline = time.monotonic() + REPORT_WITHIN_SEC
        reports: list[dict[str, Any]] = []
        while time.monotonic() < deadline:
            reports = client.get('/api/reports', params={'device_id': pi['device_id']}).json()
            if reports and reports[0]['status'] != 'pending':
                break
            time.sleep(0.5)
        assert reports and reports[0]['status'] == 'done', f'{reports}\n{stack.logs("reports")}'
        report = client.get(f'/api/reports/{reports[0]["id"]}').json()
    assert report['markdown'].startswith('# A fake week')       # the reply it gives only when the data reached it
    assert report['model'] == 'fake-model'


# ── limits ─────────────────────────────────────────────────────────────
def test_nginx_body_limit_is_the_uploads_limit(stack):
    limit = cloud_config().ingest.max_body_kb * 1024
    with stack.client() as client:
        at_limit = client.post('/v1/events', headers=bearer('wrong'), content=b'x' * limit)
        over = client.post('/v1/events', headers=bearer('wrong'), content=b'x' * (limit + 1))
    assert at_limit.status_code == 401                          # it got through nginx, to the server's token check
    assert over.status_code == 413


def test_a_spoofed_forwarded_address_does_not_change_the_bucket(stack):
    tries = cloud_config().limits.claim_tries
    with stack.client() as client:
        answers = [wrong_admin_login(client, **{'X-Forwarded-For': f'10.9.8.{number}', 'X-Real-IP': f'10.9.7.{number}'})
                   for number in range(tries)]
        assert answers == [401] * tries
        assert wrong_admin_login(client, **{'X-Forwarded-For': '10.1.1.1'}) == 429


def test_the_limit_counts_the_real_client_and_not_nginx(stack):
    """After the test above, this computer's tries are used up. Someone at another address still gets an answer, and
    the log names the client, not nginx."""
    done = subprocess.run(
        ['docker', 'exec', stack.container('web'), 'wget', '-S', '-O', '-',
         '--header', 'Content-Type: application/json', '--post-data', '{"username":"nobody","password":"wrong"}', 'http://127.0.0.1:8080/auth/admin/login'],
        capture_output=True, text=True, check=False)
    said = done.stdout + done.stderr
    assert '401' in said and '429' not in said, said
    assert 'Too many admin login tries from' in stack.logs('api')
    assert f'Too many admin login tries from {stack.web_address}' not in stack.logs('api')


def test_only_nginx_may_say_who_the_client_is(stack):
    """A container on the stack's network that talks to `api` directly and claims to be someone else stays itself."""
    tries = cloud_config().limits.claim_tries
    code = f'''
import json, urllib.error, urllib.request
for number in range({tries} + 1):
    request = urllib.request.Request('http://api:{cloud_config().server.port}/auth/admin/login', method='POST',
                                     data=json.dumps({{'username': 'nobody', 'password': 'wrong'}}).encode(),
                                     headers={{'Content-Type': 'application/json',
                                               'X-Forwarded-For': '10.9.8.' + str(number)}})
    try:
        urllib.request.urlopen(request)
    except urllib.error.HTTPError as error:
        print(error.code)
'''
    answers = stack.python_in('reports', code).split()
    assert answers == ['401'] * tries + ['429']


# ── logs ───────────────────────────────────────────────────────────────
def test_no_query_string_lands_in_a_log(stack):
    code, state, since = 'codeinaddress' + secrets.token_hex(8), 'stateinaddress' + secrets.token_hex(8), '12345.5'
    with stack.client() as client:
        assert client.get('/auth/google/callback', params={'code': code, 'state': state}).status_code == 400
        client.get('/v1/devices/nope/events', params={'since': since})
        stack.compose('stop', 'api')
        try:
            # nginx's error lines hold the whole request, so a failed callback is the one to watch
            failed = client.get('/auth/google/callback', params={'code': code, 'state': state})
            assert failed.status_code >= 500
        finally:
            stack.compose('up', '--detach', '--wait', 'api')
    logs = stack.logs('web') + stack.logs('api')
    assert '/auth/google/callback' in stack.logs('web')         # the log is on, and has the page
    assert code not in logs and state not in logs and since not in logs


def test_a_database_failure_logs_its_kind_and_no_postgres_words(stack):
    linked = seed(stack, 'google-db', now=time.time(), claim=True)
    stack.compose('stop', 'postgres')
    try:
        with stack.client() as client:
            reply = client.get(f'/v1/devices/{linked["device_id"]}/state', headers=bearer(linked['token']))
        assert reply.status_code == 500
        assert 'Error' not in reply.text.replace('Internal Server Error', '')
    finally:
        stack.compose('up', '--detach', '--wait', 'postgres')
    logs = stack.logs('api')
    assert 'OperationalError' in logs
    assert 'Traceback' not in logs and DB_PASSWORD not in logs and 'DETAIL' not in logs
    reports = stack.logs('reports')
    assert 'Traceback' not in reports and DB_PASSWORD not in reports


# ── stopping and starting again ────────────────────────────────────────
def test_the_report_program_stops_cleanly(stack):
    stack.compose('stop', 'reports')
    code = subprocess.run(['docker', 'inspect', '-f', '{{.State.ExitCode}}', stack.container('reports')],
                          capture_output=True, text=True, check=True).stdout.strip()
    stack.compose('up', '--detach', '--wait', 'reports')
    assert code == '0'


def test_data_survives_a_restart(stack):
    now = time.time()
    linked = claim(stack, seed(stack, 'google-restart')['code'])
    with stack.client() as client:
        sent = client.post('/v1/events', headers=bearer(linked['token']), json={'events': [sip('kept', now - 60)]})
        assert sent.status_code == 200, sent.text
    stack.compose('down', '--timeout', '5')
    stack.compose('up', '--detach', '--wait')
    with stack.client() as client:
        read = client.get(f'/v1/devices/{linked["device_id"]}/events', headers=bearer(linked['token']))
    assert read.status_code == 200, read.text
    assert [event['id'] for event in read.json()['events']] == ['kept']


def test_every_part_finds_the_others_after_they_all_restart_at_once(stack):
    """A reboot starts the containers in no set order and may give them new addresses."""
    stack.compose('restart', '--no-deps', 'api', 'web', 'postgres', 'reports')
    stack.compose('up', '--detach', '--wait')
    with stack.client() as client:
        assert client.get('/api/me').status_code == 401


def test_the_readmes_backup_can_be_put_back(stack):
    now = time.time()
    linked = claim(stack, seed(stack, 'google-backup')['code'])
    with stack.client() as client:
        sent = client.post('/v1/events', headers=bearer(linked['token']), json={'events': [sip('backed-up', now - 60)]})
        assert sent.status_code == 200, sent.text
    backup = stack.compose('exec', '-T', 'postgres', 'pg_dump', '-U', 'desktalk', '-d', 'desktalk')
    stack.compose('down', '--volumes', '--timeout', '5')
    stack.compose('up', '--detach', '--wait', 'postgres')
    stack.compose('exec', '-T', 'postgres', 'psql', '-q', '-U', 'desktalk', '-d', 'desktalk', sent_in=backup + '\n')
    stack.compose('up', '--detach', '--wait')
    with stack.client() as client:
        read = client.get(f'/v1/devices/{linked["device_id"]}/events', headers=bearer(linked['token']))
    assert read.status_code == 200, read.text
    assert [event['id'] for event in read.json()['events']] == ['backed-up']
