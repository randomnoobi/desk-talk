"""The developer page's routes (/api/dev/..., desktalk/web/routers/dev.py) and the page itself at /dev/, on the
whole app with a fake test runner (nothing really runs) and the real database browser on the desk's own object
state history.

Only this computer may ask, and only from the server's own page or the Vite dev server's: anything else is a 403
and nothing runs. With settings → dev.enabled false there are no routes and no page."""

from __future__ import annotations

import html
from typing import Any

import pytest

from desktalk.domain import BBox, Detection, TestItem
from desktalk.web.app import create_app
from tests.characterization.client import WebClient
from tests.fakes import FakeTestRunner
from tests.integration.app_kit import CUP_FAR, DeskRig, changed, desk_config
from tests.integration.web_kit import ON_THE_WIFI, links_for, web_client

PEN = Detection('pen/pencil', 0.8, BBox(100, 380, 160, 400))
TESTS = [TestItem('tests/unit/test_a.py::test_one', 'tests/unit/test_a.py', ()),
         TestItem('tests/unit/test_a.py::test_slow', 'tests/unit/test_a.py', ('slow',))]
LONGEST_POLL = 20               # a fake run finishes the first time it is asked about; this only stops a stuck loop

# Every /api/dev route, with a request that would work from this computer.
EVERY_ROUTE: list[tuple[str, str, Any]] = [
    ('GET', '/api/dev/settings', None),
    ('GET', '/api/dev/states?since=0', None),
    ('GET', '/api/dev/smoothing', None),
    ('GET', '/api/dev/smoothing/trace?label=cup', None),
    ('GET', '/api/dev/tests', None),
    ('POST', '/api/dev/runs', {'check': True}),
    ('GET', '/api/dev/runs/run-1?from=0', None),
    ('POST', '/api/dev/runs/run-1/stop', None),
    ('GET', '/api/dev/db', None),
    ('GET', '/api/dev/db/object_states/tables', None),
    ('POST', '/api/dev/db/object_states/query', {'sql': 'SELECT 1'}),
    ('GET', '/api/dev/files', None),
    ('GET', '/api/dev/files/preview?path=preferences.json', None),
]


def settle_sec(rig: DeskRig) -> float:
    """Long enough for an object to appear (object_states.appear_sec), with room to spare."""
    return rig.config.object_states.appear_sec + 2


@pytest.fixture
def rig(tmp_path) -> DeskRig:
    """A desk that watched a cup and a pen long enough for both to appear, with a fake test runner."""
    rig = DeskRig(tmp_path, test_runner=FakeTestRunner(TESTS))
    rig.watch(settle_sec(rig), CUP_FAR, PEN)
    return rig


def runner(rig: DeskRig) -> FakeTestRunner:
    assert isinstance(rig.app.test_runner, FakeTestRunner)
    return rig.app.test_runner


def test_settings_give_the_page_its_numbers(rig):
    settings = web_client(rig.app).get('/api/dev/settings').json()
    config = rig.config
    assert settings['poll_sec'] == config.dev.poll_sec
    assert settings['checks'] == [check.name for check in config.dev.checks]
    assert settings['health'] == config.dev.health.model_dump()
    assert settings['trace_pictures'] == config.object_states.trace_pictures
    assert settings['sql_max_rows'] == config.dev.sql_max_rows
    assert settings['data_folder'] == str(config.resolve(config.storage.folder))
    assert settings['object_states']['window_pictures'] == config.object_states.window_pictures
    assert set(settings['object_states']) == {'on_confidence', 'off_confidence', 'appear_sec', 'gone_sec',
                                              'window_pictures', 'vote_share'}
    assert settings['disconnect_after_sec'] == config.camera.disconnect_after_sec
    assert settings['web_port'] == config.web.port
    assert settings['flicker_flips_per_min'] == config.dev.flicker_flips_per_min


def test_states_show_objects_and_changes(rig):
    states = web_client(rig.app).get('/api/dev/states?since=0').json()
    assert states['objects']['cup']['state'] == 'PRESENT'
    assert isinstance(states['objects']['cup']['since'], float)
    assert states['watched_since'] is not None
    assert {'label': 'cup', 'change': 'appeared'} in [{k: c[k] for k in ('label', 'change')}
                                                     for c in states['changes']]
    later = web_client(rig.app).get(f'/api/dev/states?since={rig.clock.now()}').json()
    assert later['changes'] == []
    assert web_client(rig.app).get('/api/dev/states?since=yesterday').status_code == 400


def test_smoothing_trace_for_a_label_with_a_slash(rig):
    client = web_client(rig.app)
    assert {'cup', 'pen/pencil'} <= set(client.get('/api/dev/smoothing').json()['labels'])
    reply = client.get('/api/dev/smoothing/trace?label=pen%2Fpencil')
    assert reply.status_code == 200
    trace = reply.json()
    assert trace['label'] == 'pen/pencil'
    assert trace['steps'] and trace['steps'][-1]['phase'] == 'PRESENT'
    assert set(trace['steps'][0]) == {'ts', 'conf', 'box_seen', 'votes_yes', 'votes_total', 'seen', 'center',
                                      'phase', 'heading', 'waiting_sec'}
    nothing = client.get('/api/dev/smoothing/trace?label=nothing')
    assert (nothing.status_code, nothing.json()) == (200, {'label': 'nothing', 'steps': []})
    assert client.get('/api/dev/smoothing/trace').status_code == 400


def test_the_test_list(rig):
    tests = web_client(rig.app).get('/api/dev/tests').json()['tests']
    assert tests[1] == {'id': 'tests/unit/test_a.py::test_slow', 'file': 'tests/unit/test_a.py', 'markers': ['slow']}


def test_run_and_poll(rig):
    client = web_client(rig.app)
    started = client.post('/api/dev/runs', json={'check': True})
    assert started.status_code == 200
    run_id = started.json()['run_id']
    second = client.post('/api/dev/runs', json={'check': True})
    assert (second.status_code, second.json()) == (409, {'error': rig.config.prompts.dev.run_already_going})
    for _ in range(LONGEST_POLL):
        status = client.get(f'/api/dev/runs/{run_id}?from=0').json()
        if status['state'] != 'running':
            break
    assert status['state'] == 'passed'
    assert status['run_id'] == run_id and status['steps'][0]['exit_code'] == 0
    end = status['output_end']
    assert client.get(f'/api/dev/runs/{run_id}?from={end}').json()['output'] == ''
    assert runner(rig).started == [('check', [])]


def test_run_some_tests_and_stop(rig):
    client = web_client(rig.app)
    client.get('/api/dev/tests')                                # the runner knows the tests it listed
    run_id = client.post('/api/dev/runs', json={'tests': [TESTS[0].id]}).json()['run_id']
    assert client.post(f'/api/dev/runs/{run_id}/stop').json() == {'ok': True}
    assert client.get(f'/api/dev/runs/{run_id}').json()['state'] == 'stopped'
    assert runner(rig).started == [('tests', [TESTS[0].id])]


def test_unknown_test_id_is_400(rig):
    client = web_client(rig.app)
    client.get('/api/dev/tests')
    reply = client.post('/api/dev/runs', json={'tests': ['tests/x.py::nope; rm -rf /']})
    assert reply.status_code == 400 and 'nope' in reply.json()['error']
    nothing = client.post('/api/dev/runs', json={})
    assert (nothing.status_code, nothing.json()) == (400, {'error': rig.config.prompts.dev.nothing_to_run})
    assert client.post('/api/dev/runs', json={'tests': 'all'}).status_code == 400
    assert runner(rig).started == []


def test_an_unknown_run_is_404(rig):
    reply = web_client(rig.app).get('/api/dev/runs/no-such-run')
    assert reply.status_code == 404
    assert web_client(rig.app).get('/api/dev/runs/run-1?from=-1').status_code == 400


def test_query_and_refusal(rig):
    client = web_client(rig.app)
    assert client.get('/api/dev/db').json() == {'databases': ['object_states']}
    tables = client.get('/api/dev/db/object_states/tables').json()['tables']
    assert 'events' in [table['name'] for table in tables]
    reply = client.post('/api/dev/db/object_states/query', json={'sql': 'SELECT count(*) AS n FROM events'})
    assert reply.status_code == 200
    result = reply.json()
    assert result['columns'] == ['n'] and result['rows'][0][0] >= 1
    assert result['truncated'] is False and result['took_ms'] >= 0
    refused = client.post('/api/dev/db/object_states/query', json={'sql': 'DELETE FROM events'})
    assert refused.status_code == 400 and refused.json()['error']
    assert client.post('/api/dev/db/object_states/query', json={'sql': 'SELECT count(*) AS n FROM events'}
                       ).json()['rows'] == result['rows']                       # nothing was deleted
    assert client.post('/api/dev/db/object_states/query', json={}).status_code == 400


def test_odd_values_in_a_query_still_make_json(rig):
    reply = web_client(rig.app).post('/api/dev/db/object_states/query',
                                     json={'sql': "SELECT x'00ff' AS blob, 1e999 AS big, 'text' AS words"})
    assert reply.status_code == 200
    assert reply.json()['rows'] == [["x'00ff'", 'inf', 'text']]


def test_an_unknown_database_is_404(rig):
    client = web_client(rig.app)
    words = rig.config.prompts.dev
    reply = client.get('/api/dev/db/nope/tables')
    assert (reply.status_code, reply.json()) == (404, {'error': words.unknown_database.format(name='nope')})
    assert client.post('/api/dev/db/nope/query', json={'sql': 'SELECT 1'}).status_code == 404


def test_files_and_previews(rig):
    rig.app.preferences.update({'wake_time': '06:45'})
    client = web_client(rig.app)
    files = {f['path']: f for f in client.get('/api/dev/files').json()['files']}
    assert files['preferences.json']['is_folder'] is False
    preview = client.get('/api/dev/files/preview?path=preferences.json').json()
    assert preview['path'] == 'preferences.json' and '06:45' in preview['text']
    missing = client.get('/api/dev/files/preview?path=nothing-here.txt')
    assert missing.status_code == 404
    assert missing.json() == {'error': rig.config.prompts.dev.no_such_file.format(path='nothing-here.txt')}


@pytest.mark.parametrize('path', ['../.env', '/etc/passwd', '../../config/settings.yaml'])
def test_file_outside_is_400(rig, path):
    reply = web_client(rig.app).get('/api/dev/files/preview', params={'path': path})
    assert reply.status_code == 400
    assert reply.json() == {'error': rig.config.prompts.dev.outside_data_folder.format(path=path)}


@pytest.mark.parametrize('method, path, body', EVERY_ROUTE, ids=[f'{m} {p}' for m, p, _ in EVERY_ROUTE])
def test_other_computers_get_403(rig, method, path, body):
    reply = web_client(rig.app, from_ip=ON_THE_WIFI).request(method, path, json=body)
    assert (reply.status_code, reply.json()) == (403, {'error': rig.config.prompts.dev.other_computer})
    assert runner(rig).started == []


def test_the_list_has_every_dev_route(rig):
    """So the 403 test above covers each route, including any added later."""
    server = WebClient(create_app(rig.app, links_for(rig.app)))
    dev_routes = {(method, rule) for method, rule in server.routes() if rule.startswith('/api/dev')}
    assert {server.route_for(method, path) for method, path, _ in EVERY_ROUTE} == dev_routes


@pytest.mark.parametrize('origin', ['https://example.com', 'http://localhost:9999', 'null',
                                    'http://192.168.1.20:5000'])
def test_other_web_pages_get_403(rig, origin):
    client = web_client(rig.app)
    reply = client.post('/api/dev/runs', json={'check': True}, headers={'Origin': origin})
    assert (reply.status_code, reply.json()) == (403, {'error': rig.config.prompts.dev.other_web_page})
    assert client.get('/api/dev/settings', headers={'Origin': origin}).status_code == 403
    assert runner(rig).started == []


@pytest.mark.parametrize('origin', ['http://localhost:{vite}', 'http://localhost:{web}', 'http://127.0.0.1:{web}'])
def test_the_page_itself_may_ask(rig, origin):
    origin = origin.format(vite=rig.config.dev.vite_port, web=rig.config.web.port)
    reply = web_client(rig.app).post('/api/dev/runs', json={'check': True}, headers={'Origin': origin})
    assert reply.status_code == 200
    assert runner(rig).started == [('check', [])]


def test_dev_off_means_no_routes(tmp_path):
    rig = DeskRig(tmp_path, config=changed(desk_config(tmp_path), 'dev', enabled=False))
    client = web_client(rig.app)
    assert client.get('/api/dev/settings').status_code == 404
    # the app's page files answer every other path, and they only take GET
    assert client.post('/api/dev/runs', json={'check': True}).status_code == 405
    assert rig.app.test_runner is None
    assert client.get('/dev/').status_code == 404
    assert client.get('/').status_code == 200                   # the app's own page is still there


def test_a_missing_tool_is_404(rig):
    """An app built without the developer tools (bootstrap only builds them with dev.enabled) has none to use."""
    rig.app.test_runner = rig.app.databases = rig.app.data_files = None
    client = web_client(rig.app)
    assert client.get('/api/dev/tests').status_code == 404
    assert client.get('/api/dev/db').status_code == 404
    assert client.get('/api/dev/files').status_code == 404


def test_page_not_built_says_how(tmp_path):
    empty = tmp_path / 'not-built'
    empty.mkdir()
    rig = DeskRig(tmp_path, config=changed(desk_config(tmp_path), 'dev', ui_folder=empty))
    client = web_client(rig.app)
    reply = client.get('/dev/')
    assert reply.status_code == 200
    assert reply.headers['content-type'].startswith('text/html')
    assert 'npm run build' in reply.text
    assert html.escape(rig.config.prompts.dev.not_built) in reply.text
    assert client.get('/dev/assets/index.js').status_code == 404


def test_the_built_page_is_served(tmp_path):
    built = tmp_path / 'dist'
    (built / 'assets').mkdir(parents=True)
    (built / 'index.html').write_text('<!DOCTYPE html><title>Desk Talk developer page</title>')
    (built / 'assets' / 'index.js').write_text('console.log("hi")')
    rig = DeskRig(tmp_path, config=changed(desk_config(tmp_path), 'dev', ui_folder=built))
    client = web_client(rig.app)
    page = client.get('/dev/')
    assert page.status_code == 200 and 'Desk Talk developer page' in page.text
    assert page.headers['cache-control'] == 'no-cache'
    assert client.get('/dev/assets/index.js').text == 'console.log("hi")'
    assert client.get('/dev/assets/nope.js').status_code == 404
    moved = client.get('/dev')
    assert moved.status_code in (301, 307, 308) and moved.headers['location'] == '/dev/'
    assert client.get('/').status_code == 200                   # the app's page is still at /
