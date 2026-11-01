"""bootstrap, the two programs (main.py, reports_main.py) and the Docker files, without Docker: the running stack is in
tests/docker/test_cloud_stack.py."""

from __future__ import annotations

import io
import ipaddress
import logging
import re
import signal
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy.exc
import yaml
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from desktalk_cloud import bootstrap, main, reports_main
from desktalk_cloud.chat_forwarder import HttpChatForwarder
from desktalk_cloud.clock import SystemClock
from desktalk_cloud.config import ConfigError
from desktalk_cloud.google_login import HttpGoogleLogin
from desktalk_cloud.llm_anthropic import AnthropicReportWriter
from desktalk_cloud.llm_openai import OpenAIReportWriter
from desktalk_cloud.report_job import ReportJob
from desktalk_cloud.retention import RetentionJob
from desktalk_cloud.store_memory import MemoryStore
from desktalk_cloud.store_postgres import PostgresStore
from tests.fakes import FakeClock
from tests.fakes.cloud import CLOUD_FOLDER, TEST_SESSION_SECRET, cloud_config, link_device, make_parts

DATABASE = 'postgresql+psycopg://user:hidden-password@nowhere.invalid:5432/desktalk'
ALL_SET = {'DATABASE_URL': DATABASE, 'GOOGLE_CLIENT_ID': 'id', 'GOOGLE_CLIENT_SECRET': 'secret', 'CHAT_API_KEY': 'key'}
ROOT = CLOUD_FOLDER.parent
# What Postgres says about a row it refused: the values come back in its own words.
POSTGRES_WORDS = 'duplicate key\nDETAIL:  Key (device_id, event_id)=(the-pis-id, a private value) already exists.'


def database_error() -> sqlalchemy.exc.IntegrityError:
    return sqlalchemy.exc.IntegrityError('INSERT', {}, Exception(POSTGRES_WORDS))


# ── build ──────────────────────────────────────────────────────────────
def test_build_uses_the_real_parts_from_the_settings():
    config = cloud_config(ALL_SET, chat={'limit_requests': 1}, limits={'claim_tries': 1})
    server = bootstrap.build(config)
    parts = server.app.state.parts
    assert isinstance(parts.store, PostgresStore) and server.store is parts.store
    assert isinstance(parts.clock, SystemClock)
    assert isinstance(parts.google, HttpGoogleLogin)
    assert isinstance(parts.chat_forwarder, HttpChatForwarder)
    assert parts.claim_limiter.hit('a') and not parts.claim_limiter.hit('a')
    assert parts.chat_limiter.hit('a') and not parts.chat_limiter.hit('a')


@pytest.mark.parametrize('missing', ['GOOGLE_CLIENT_ID', 'GOOGLE_CLIENT_SECRET'])
def test_no_google_without_both_of_its_values(missing):
    env = {name: value for name, value in ALL_SET.items() if name != missing}
    assert bootstrap.build(cloud_config(env)).app.state.parts.google is None


def test_no_chat_without_its_key():
    env = {name: value for name, value in ALL_SET.items() if name != 'CHAT_API_KEY'}
    assert bootstrap.build(cloud_config(env)).app.state.parts.chat_forwarder is None


def test_build_says_so_in_plain_words_when_there_is_no_database_address():
    with pytest.raises(ConfigError, match='DATABASE_URL'):
        bootstrap.build(cloud_config())


def test_every_router_is_included():
    paths = {path for _, path in every_route()}
    assert {'/auth/google/login', '/auth/google/callback', '/auth/logout', '/auth/admin/login',
            '/api/me', '/api/devices', '/api/devices/code', '/api/reports', '/api/admin/overview',
            '/v1/chat/completions', '/v1/devices/claim', '/v1/devices/x/state',
            '/v1/devices/x/events', '/v1/events', '/healthz'} <= paths
    client = TestClient(bootstrap.build_app(make_parts()), raise_server_exceptions=False)
    for method, path in every_route():
        reply = client.request(method, path, json={})
        assert reply.status_code != 405 and reply.text != '{"detail":"Not Found"}', f'{method} {path} is not served'


def test_stopping_the_server_closes_the_database_connections(monkeypatch):
    closed = []
    monkeypatch.setattr(PostgresStore, 'dispose', lambda self: closed.append(self))
    server = bootstrap.build(cloud_config(ALL_SET))
    with TestClient(server.app):
        assert closed == []
    assert closed == [server.store]


# ── who may call what ──────────────────────────────────────────────────
def every_route(app: FastAPI | None = None) -> list[tuple[str, str]]:
    """Each route of each included router and of the real built app (so one added straight on the app counts too), and
    the health check, with any id in its address filled in."""
    built = app if app is not None else bootstrap.build_app(make_parts())
    routes = {('GET', '/healthz')}
    for route in [*(item for router in bootstrap.ROUTERS for item in router.routes), *built.routes]:
        if isinstance(route, APIRoute):
            path = re.sub(r'{[a-z_]+}', 'x', route.path)
            routes |= {(method, path) for method in route.methods or ()}
    return sorted(routes)


def test_every_route_sees_a_route_added_straight_on_the_app():
    app = bootstrap.build_app(make_parts())
    app.add_api_route('/added-on-the-app/{thing_id}', lambda thing_id: thing_id, methods=['POST'])
    assert ('POST', '/added-on-the-app/x') in every_route(app)


def is_open_to_anyone(path: str) -> bool:
    return path.startswith('/auth/') or path in ('/v1/devices/claim', '/healthz')


def test_every_route_needs_the_right_login():
    parts = make_parts()
    app = bootstrap.build_app(parts)
    client = TestClient(app, raise_server_exceptions=False)
    routes = [route for route in every_route() if not is_open_to_anyone(route[1])]
    assert len(routes) >= 15
    for method, path in routes:
        reply = client.request(method, path, json={})
        assert reply.status_code == 401, f'{method} {path} answered {reply.status_code} with no login'


def test_only_the_expected_routes_are_open_to_anyone():
    opened = {path for _, path in every_route() if is_open_to_anyone(path)}
    assert opened == {'/auth/google/login', '/auth/google/callback', '/auth/logout', '/auth/admin/login',
                      '/v1/devices/claim', '/healthz'}


def test_a_pis_token_opens_no_website_or_admin_route():
    parts = make_parts()
    app = bootstrap.build_app(parts)
    headers = {'Authorization': f'Bearer {link_device(parts).token}'}
    client = TestClient(app, raise_server_exceptions=False)
    for method, path in every_route():
        if path.startswith('/api/'):
            reply = client.request(method, path, json={}, headers=headers)
            assert reply.status_code == 401, f'{method} {path} answered {reply.status_code} to a token'


# ── start-up checks ────────────────────────────────────────────────────
@pytest.mark.parametrize('address', ['', 'http://localhost:8080', 'https://desk.example.com', 'http://192.168.1.20:80',
                                     'https://desk.example.com/home'])
def test_a_usable_login_url_passes(address):
    bootstrap.check_settings(cloud_config(server={'login_url': address}))


@pytest.mark.parametrize('address', ['localhost:8080', 'desk.example.com', 'ftp://desk.example.com', 'http://',
                                     'javascript:alert(1)', '//desk.example.com', 'http://a b', 'http://host:port',
                                     'http://me:pw@desk.example.com', 'http://desk.example.com/\nX: y'])
def test_a_bad_login_url_stops_the_start_in_plain_words(address):
    with pytest.raises(ConfigError, match='LOGIN_URL'):
        bootstrap.check_settings(cloud_config(server={'login_url': address}))


def test_broken_site_words_stop_the_start():
    config = cloud_config()
    broken = config.model_copy(update={'prompts': {**config.prompts, 'site': {'setup_command': '{nope}',
                                                                              'needs_json': 'x'}}})
    with pytest.raises(ConfigError):
        bootstrap.check_settings(broken)


# ── the report jobs ────────────────────────────────────────────────────
def writer_of(jobs) -> Any:
    report_job = next(job for job in jobs if isinstance(job, ReportJob))
    return report_job._writer


def test_the_jobs_are_the_weekly_report_and_the_clean_up():
    jobs = bootstrap.build_jobs(cloud_config(), MemoryStore(), FakeClock(), lambda: False)
    assert sorted(type(job).__name__ for job in jobs) == ['ReportJob', 'RetentionJob']
    assert isinstance(jobs[1], RetentionJob)
    assert isinstance(writer_of(jobs), AnthropicReportWriter)


def test_the_report_writer_follows_the_provider_setting():
    config = cloud_config(reports={'provider': 'openai_compatible', 'url': 'http://nowhere.invalid/chat'})
    jobs = bootstrap.build_jobs(config, MemoryStore(), FakeClock(), lambda: False)
    assert isinstance(writer_of(jobs), OpenAIReportWriter)


# ── logs: what Postgres says stays out of them ─────────────────────────
def logged(emit) -> str:
    stream = io.StringIO()
    logger = logging.getLogger('test_cloud_bootstrap.logged')
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = bootstrap.log_handler(stream)
    logger.addHandler(handler)
    try:
        emit(logger)
    finally:
        logger.removeHandler(handler)
    return stream.getvalue()


def test_a_database_error_logs_its_kind_and_not_postgres_words():
    def emit(logger):
        try:
            raise database_error()
        except Exception:
            logger.exception('The job hit a problem with Pi %s', 'pi-1')

    text = logged(emit)
    assert 'The job hit a problem with Pi pi-1' in text and 'IntegrityError' in text
    assert 'private value' not in text and 'DETAIL' not in text and 'Traceback' not in text


def test_a_database_error_behind_another_error_is_hidden_too():
    def emit(logger):
        try:
            try:
                raise database_error()
            except Exception as error:
                raise RuntimeError('wrapped') from error
        except Exception:
            logger.exception('A route broke')

    text = logged(emit)
    assert 'A route broke' in text and 'private value' not in text and 'Traceback' not in text


def test_a_database_error_passed_as_a_log_value_is_hidden():
    text = logged(lambda logger: logger.warning('It failed: %s', database_error()))
    assert 'It failed' in text and 'IntegrityError' in text and 'private value' not in text


def test_a_database_error_that_is_the_message_itself_is_hidden():
    text = logged(lambda logger: logger.error(database_error()))
    assert 'IntegrityError' in text and 'private value' not in text and 'DETAIL' not in text


def test_a_database_error_behind_the_message_itself_is_hidden():
    def emit(logger):
        try:
            try:
                raise database_error()
            except Exception as error:
                raise RuntimeError('wrapped') from error
        except Exception as error:
            logger.error(error)

    text = logged(emit)
    assert 'IntegrityError' in text and 'private value' not in text


def test_any_other_error_as_the_message_stays_as_it_is():
    assert 'a plain problem' in logged(lambda logger: logger.error(ValueError('a plain problem')))


def test_a_message_with_a_percent_sign_still_logs_after_the_error_is_hidden():
    def emit(logger):
        try:
            raise database_error()
        except Exception:
            logger.exception('At %d%% done', 50)

    assert 'At 50% done' in logged(emit)


def test_any_other_error_keeps_its_traceback():
    def emit(logger):
        try:
            raise ValueError('a plain problem')
        except ValueError:
            logger.exception('It broke')

    text = logged(emit)
    assert 'Traceback' in text and 'a plain problem' in text


def test_set_up_logging_uses_the_handler_that_hides_database_errors():
    root = logging.getLogger()
    before, level = list(root.handlers), root.level
    try:
        bootstrap.set_up_logging()
        assert len(root.handlers) == 1 and root.handlers[0].filters
        assert root.level == logging.INFO
        assert logging.getLogger('httpx').level == logging.WARNING      # httpx logs whole addresses at INFO
    finally:
        root.handlers[:] = before
        root.setLevel(level)
        logging.getLogger('httpx').setLevel(logging.NOTSET)


# ── main.py: the website's server ──────────────────────────────────────
@pytest.fixture
def program(monkeypatch):
    """Everything a program reads to start, from cloud/ as Docker runs it, with nothing real behind it. Says in `calls`
    what it did, in order. `set_up_logging` is left out so pytest's log capture stays."""
    calls: list[Any] = []
    monkeypatch.chdir(CLOUD_FOLDER)
    monkeypatch.setenv('SESSION_SECRET', TEST_SESSION_SECRET)
    monkeypatch.setenv('DATABASE_URL', DATABASE)
    for module in (main, reports_main):
        monkeypatch.setattr(module, 'set_up_logging', lambda: calls.append('logging'))
        monkeypatch.setattr(module, 'run_migrations', lambda url: calls.append(('migrations', url)))
    monkeypatch.setattr(main, 'ensure_admin', lambda config, store: calls.append('admin'))
    monkeypatch.setattr(main.uvicorn, 'run', lambda app, **options: calls.append(('uvicorn', options)))
    monkeypatch.setattr(PostgresStore, 'dispose', lambda self: calls.append('dispose'))
    return calls


def test_main_makes_the_tables_and_the_admin_and_then_serves(program):
    assert main.main() == 0
    assert program[:3] == ['logging', ('migrations', DATABASE), 'admin']
    name, options = program[3]
    assert name == 'uvicorn'
    assert options['host'] == '0.0.0.0' and options['port'] == 8000


def test_main_trusts_proxy_headers_and_writes_no_access_log(program, monkeypatch):
    monkeypatch.delenv('FORWARDED_ALLOW_IPS', raising=False)
    main.main()
    options = program[3][1]
    assert options['proxy_headers'] is True
    assert options['access_log'] is False           # Google's code and state arrive in an address
    assert options['log_config'] is None            # uvicorn's own lines go through set_up_logging
    # uvicorn then reads FORWARDED_ALLOW_IPS itself: compose.yaml sets it to nginx's address, and it stays 127.0.0.1
    # when it's not set. Passing a value here would override that.
    assert options.get('forwarded_allow_ips') is None


def test_main_stops_in_plain_words_on_a_bad_setting(program, monkeypatch, caplog):
    monkeypatch.setenv('LOGIN_URL', 'not an address')
    assert main.main() == 1
    assert 'LOGIN_URL' in caplog.text
    assert not [call for call in program if isinstance(call, tuple)]        # no database touched


def test_main_stops_with_no_postgres_words_when_the_database_fails(program, monkeypatch, caplog):
    def fail(url):
        raise database_error()

    monkeypatch.setattr(main, 'run_migrations', fail)
    with caplog.at_level(logging.INFO):
        assert main.main() == 1
    assert 'IntegrityError' in caplog.text
    assert 'private value' not in caplog.text and 'hidden-password' not in caplog.text
    assert 'dispose' in program


BAD_ADDRESSES = ['not an address', 'postgresql+psycopg://user:hidden-password@nowhere.invalid:not-a-port/desktalk',
                 'nosuchthing://user:hidden-password@nowhere.invalid/desktalk',
                 'sqlite:///hidden-password.db']


@pytest.mark.parametrize('address', BAD_ADDRESSES)
def test_main_stops_in_plain_words_on_a_bad_database_address(program, monkeypatch, caplog, address):
    monkeypatch.setenv('DATABASE_URL', address)
    assert main.main() == 1
    assert 'DATABASE_URL' in caplog.text
    assert 'hidden-password' not in caplog.text and address not in caplog.text and 'Traceback' not in caplog.text
    assert program == ['logging']                   # no database touched


@pytest.mark.parametrize('address', BAD_ADDRESSES)
def test_the_report_program_stops_in_plain_words_on_a_bad_database_address(program, monkeypatch, caplog, address):
    monkeypatch.setenv('DATABASE_URL', address)
    assert reports_main.main([]) == 1
    assert 'DATABASE_URL' in caplog.text
    assert 'hidden-password' not in caplog.text and address not in caplog.text and 'Traceback' not in caplog.text
    assert program == ['logging']


@pytest.mark.parametrize('address', ['postgresql+psycopg://u:p@db:5432/d', 'postgresql://u:p@db:5432/d',
                                     'postgres://u:p@db:5432/d'])
def test_the_kinds_of_database_address_the_server_uses_pass(address):
    assert bootstrap.database_url(cloud_config({'DATABASE_URL': address})) == address


# ── reports_main.py: the weekly report and the clean-up ────────────────
class Job:
    def __init__(self, name: str, wait: float, *, fail_on: tuple[int, ...] = ()) -> None:
        self.name, self.wait, self.fail_on = name, wait, fail_on
        self.ticks: list[float] = []

    def tick(self, now: float) -> float:
        self.ticks.append(now)
        if len(self.ticks) in self.fail_on:
            raise database_error()
        return self.wait


def run_for(jobs, seconds: float, retry_sec: float = 30) -> None:
    clock = FakeClock()
    end = clock.now() + seconds

    def wait(delay: float) -> bool:
        clock.advance(delay)
        return clock.now() >= end

    reports_main.run_jobs(jobs, clock, wait, retry_sec)


def test_each_job_ticks_again_when_it_asks_to():
    fast, slow = Job('fast', 10), Job('slow', 25)
    run_for([fast, slow], 50)
    start = fast.ticks[0]
    assert [t - start for t in fast.ticks] == [0, 10, 20, 30, 40]
    assert [t - start for t in slow.ticks] == [0, 25]


def test_one_bad_tick_never_ends_the_loop_or_stops_the_other_job(caplog):
    bad, other = Job('bad', 10, fail_on=(1, 2)), Job('other', 10)
    with caplog.at_level(logging.INFO):
        run_for([bad, other], 100, retry_sec=30)
    start = bad.ticks[0]
    assert [t - start for t in bad.ticks][:4] == [0, 30, 60, 70]
    assert len(other.ticks) == 10
    assert 'IntegrityError' in caplog.text and 'private value' not in caplog.text


def test_the_stop_flag_ends_a_wait_quickly():
    stop = reports_main.StopFlag(cloud_config().reports.stop_check_sec)
    assert stop.wait(0) is False
    stop.set()
    assert stop.wait(3600) is True


def test_the_stop_flag_waits_the_time_it_is_given():
    assert reports_main.StopFlag(0.001).wait(0.01) is False


def test_the_stop_flag_looks_for_a_stop_as_often_as_it_is_told(monkeypatch):
    naps: list[float] = []
    monkeypatch.setattr(reports_main.time, 'sleep', naps.append)
    reports_main.StopFlag(0.25).wait(0.5)
    assert naps == [0.25, 0.25]


def reports_program(monkeypatch, jobs) -> None:
    monkeypatch.setattr(reports_main, 'build_jobs', lambda config, store, clock, should_stop: jobs)


@pytest.fixture(autouse=True)
def _put_signal_handlers_back():
    before = {name: signal.getsignal(name) for name in (signal.SIGTERM, signal.SIGINT)}
    yield
    for name, handler in before.items():
        signal.signal(name, handler)


@pytest.mark.parametrize('stop_with', [signal.SIGTERM, signal.SIGINT])
def test_the_report_program_stops_cleanly_on_a_stop_signal(program, monkeypatch, tmp_path, stop_with):
    class StopsItself(Job):
        def tick(self, now: float) -> float:
            signal.raise_signal(stop_with)
            return super().tick(now)

    ready = tmp_path / 'ready'
    job = StopsItself('job', 3600)
    reports_program(monkeypatch, [job])
    assert reports_main.main(['--ready-file', str(ready)]) == 0
    assert program == ['logging', ('migrations', DATABASE), 'dispose']
    assert len(job.ticks) == 1
    assert ready.exists()


def test_a_stop_signal_reaches_the_report_job_while_its_round_is_running(program, monkeypatch):
    given: list[Callable[[], bool]] = []
    seen: list[bool] = []

    class LooksForTheStop(Job):
        def tick(self, now: float) -> float:
            seen.append(given[0]())
            signal.raise_signal(signal.SIGTERM)
            seen.append(given[0]())
            return super().tick(now)

    def build_jobs(config, store, clock, should_stop):
        given.append(should_stop)
        return [LooksForTheStop('job', 3600)]

    monkeypatch.setattr(reports_main, 'build_jobs', build_jobs)
    assert reports_main.main([]) == 0
    assert seen == [False, True]


def test_the_report_program_says_it_is_ready_only_after_the_migrations(program, monkeypatch, tmp_path, caplog):
    def fail(url):
        raise database_error()

    ready = tmp_path / 'ready'
    ready.write_text('from the run before the restart')
    monkeypatch.setattr(reports_main, 'run_migrations', fail)
    with caplog.at_level(logging.INFO):
        assert reports_main.main(['--ready-file', str(ready)]) == 1
    assert not ready.exists()
    assert 'IntegrityError' in caplog.text and 'private value' not in caplog.text
    assert 'dispose' in program


def test_the_report_program_stops_in_plain_words_on_a_bad_setting(program, monkeypatch, caplog):
    monkeypatch.delenv('DATABASE_URL')
    assert reports_main.main([]) == 1
    assert 'DATABASE_URL' in caplog.text
    assert program == ['logging']


# ── the Docker files ───────────────────────────────────────────────────
def compose() -> dict[str, Any]:
    return yaml.safe_load((CLOUD_FOLDER / 'compose.yaml').read_text(encoding='utf-8'))


def nginx_conf() -> str:
    return (CLOUD_FOLDER / 'web' / 'nginx.conf').read_text(encoding='utf-8')


def kb_setting(conf: str) -> int:
    found = re.search(r'client_max_body_size\s+(\d+)k;', conf)
    assert found is not None
    return int(found.group(1))


def test_only_the_website_publishes_a_port():
    services = compose()['services']
    assert {name for name, service in services.items() if 'ports' in service} == {'web'}
    assert {'postgres', 'api', 'web', 'reports'} == set(services)


def test_every_service_comes_back_after_a_reboot_and_has_a_health_check():
    for name, service in compose()['services'].items():
        assert service['restart'] == 'unless-stopped', name
        assert 'test' in service['healthcheck'], name


def test_the_website_waits_for_the_server_and_the_server_for_postgres():
    services = compose()['services']
    assert services['web']['depends_on']['api']['condition'] == 'service_healthy'
    for name in ('api', 'reports'):
        assert services[name]['depends_on']['postgres']['condition'] == 'service_healthy'


def test_postgres_keeps_its_data_in_a_named_volume():
    config = compose()
    volume = config['services']['postgres']['volumes'][0].split(':')[0]
    assert volume in config['volumes']


def test_there_is_exactly_one_report_writer():
    # Two would both pay for the same report: there's no step where one claims the work.
    reports = compose()['services']['reports']
    assert reports.get('deploy', {}).get('replicas', 1) == 1
    assert 'scale' not in reports


def test_postgres_keeps_the_values_out_of_its_own_log():
    command = ' '.join(compose()['services']['postgres']['command'])
    assert 'log_error_verbosity=terse' in command and 'log_min_error_statement=panic' in command


def test_the_server_trusts_forwarded_addresses_only_from_nginx():
    services = compose()['services']
    allowed = services['api']['environment']['FORWARDED_ALLOW_IPS']
    assert allowed == services['web']['networks']['default']['ipv4_address']
    assert '*' not in allowed


def default_of(setting: str) -> str:
    found = re.search(r':-([^}]+)}', setting)
    assert found is not None
    return found.group(1)


def test_nginx_address_is_outside_the_range_docker_hands_out():
    # Docker gives containers addresses from ip_range as they start, so nothing can already hold nginx's when it starts.
    network = compose()['networks']['default']['ipam']['config'][0]
    address = ipaddress.ip_address(default_of(compose()['services']['web']['networks']['default']['ipv4_address']))
    assert address in ipaddress.ip_network(default_of(network['subnet']))
    assert address not in ipaddress.ip_network(default_of(network['ip_range']))


def image_names() -> list[str]:
    names = [service['image'] for service in compose()['services'].values()
             if 'image' in service and not service['image'].endswith(':local')]
    for dockerfile in (CLOUD_FOLDER / 'server' / 'Dockerfile', CLOUD_FOLDER / 'web' / 'Dockerfile'):
        text = dockerfile.read_text(encoding='utf-8')
        names += re.findall(r'^FROM\s+(\S+)', text, re.MULTILINE) + re.findall(r'--from=(\S+/\S+)', text)
    return names


def test_every_image_has_an_exact_version():
    names = image_names()
    assert len(names) >= 5
    for name in names:
        tag = name.rsplit(':', 1)[-1] if ':' in name else ''
        assert re.match(r'\d+\.\d+\.\d+', tag) or re.match(r'\d+\.\d+-alpine', tag), f'{name} has no exact version'


def test_the_server_and_the_website_run_without_root_rights():
    for dockerfile, base in ((CLOUD_FOLDER / 'server' / 'Dockerfile', None),
                             (CLOUD_FOLDER / 'web' / 'Dockerfile', 'nginx-unprivileged')):
        text = dockerfile.read_text(encoding='utf-8')
        if base is None:
            assert re.search(r'^USER\s+(?!root)\S+', text, re.MULTILINE)
        else:
            assert base in text
            assert not re.search(r'^USER\s+root', text, re.MULTILINE)


def test_a_stop_waits_longer_than_the_slowest_report():
    grace = compose()['services']['reports']['stop_grace_period']
    found = re.fullmatch(r'(\d+)s', grace)
    assert found is not None
    assert int(found.group(1)) > cloud_config().reports.timeout_sec


def test_the_nginx_body_limit_matches_the_settings():
    config = cloud_config()
    assert kb_setting(nginx_conf()) == config.ingest.max_body_kb
    assert kb_setting(nginx_conf()) >= config.chat.max_body_kb      # so the chat route's own limit is the one that acts


def test_nginx_waits_longer_than_the_slowest_route():
    config = cloud_config()
    found = re.search(r'proxy_read_timeout\s+(\d+)s;', (CLOUD_FOLDER / 'web' / 'nginx-proxy.conf').read_text('utf-8'))
    assert found is not None
    assert int(found.group(1)) > config.chat.timeout_sec
    assert int(found.group(1)) > 2 * config.google.timeout_sec      # a login makes two calls to Google


def test_nginx_passes_only_the_three_paths_on_and_to_the_servers_own_port():
    conf = nginx_conf()
    assert set(re.findall(r'location\s+(/\S*)\s*{\s*include', conf)) == {'/api/', '/auth/', '/v1/'}
    assert f'http://api:{cloud_config().server.port}' in conf
    health = ' '.join(compose()['services']['api']['healthcheck']['test'])
    assert f':{cloud_config().server.port}/healthz' in health


def test_nginx_overwrites_the_forwarded_address():
    conf = (CLOUD_FOLDER / 'web' / 'nginx-proxy.conf').read_text(encoding='utf-8')
    assert re.search(r'proxy_set_header\s+X-Forwarded-For\s+\$remote_addr;', conf)
    assert '$proxy_add_x_forwarded_for' not in conf


def test_nginx_logs_no_query_strings():
    conf = nginx_conf()
    log_format = re.search(r'log_format[^;]+;', conf, re.DOTALL)
    assert log_format is not None
    for leaky in ('$request ', '$request"', '$request_uri', '$args', '$query_string', '$http_referer'):
        assert leaky not in log_format.group(0)
    assert '$uri' in log_format.group(0)
    assert 'error_log' in conf                      # nginx's error lines hold the whole request: see /auth/ there


def test_the_server_image_has_the_time_zone_data():
    project = tomllib.loads((CLOUD_FOLDER / 'server' / 'pyproject.toml').read_text(encoding='utf-8'))['project']
    assert any(line.startswith('tzdata') for line in project['dependencies'])
    lock = (ROOT / 'uv.lock').read_text(encoding='utf-8')
    assert re.search(r'name = "tzdata"', lock)


def test_the_readme_says_what_a_person_has_to_know():
    text = Path(CLOUD_FOLDER / 'README.md').read_text(encoding='utf-8')
    for words in ('docker compose up -d --build --wait', 'WEB_PORT', 'Too many tries', 'PUBLIC_URL', 'LOGIN_URL',
                  'new username', 'plain HTTP', 'pg_dump', 'docker compose down', 'Google', 'ADMIN_USERNAME',
                  'CHAT_API_KEY', 'REPORT_API_KEY'):
        assert words in text, words


def test_docker_builds_never_get_your_keys_or_data():
    ignored = (ROOT / '.dockerignore').read_text(encoding='utf-8').splitlines()
    assert {'**/.env', 'data', 'models', '**/node_modules'} <= set(ignored)


def test_env_example_lists_what_compose_needs():
    text = (CLOUD_FOLDER / '.env.example').read_text(encoding='utf-8')
    for name in re.findall(r'\$\{([A-Z_]+)', (CLOUD_FOLDER / 'compose.yaml').read_text(encoding='utf-8')):
        assert re.search(rf'^#?\s*{name}=', text, re.MULTILINE), f'{name} is not in .env.example'
