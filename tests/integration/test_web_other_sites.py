"""A web site open on a phone on the same Wi-Fi must not be able to read the desk's picture or change anything."""

from __future__ import annotations

import pytest

from desktalk.web.app import create_phone_app
from tests.integration.app_kit import PAIR_KEY, DeskRig, changed, desk_config
from tests.integration.web_kit import web_client

OTHER_SITE = {'Origin': 'http://evil.example'}
ALLOW = 'access-control-allow-origin'
A_WRITE = ('/api/activity', {})


@pytest.fixture
def client(tmp_path):
    return web_client(DeskRig(tmp_path).app)


def own_page(client) -> dict[str, str]:
    """What a browser sends from a page this server gave it: the address the request goes to."""
    return {'Origin': str(client.base_url).rstrip('/')}


def listed_client(tmp_path, *sites: str):
    config = changed(desk_config(tmp_path), 'web', cors_origins=list(sites))
    return web_client(DeskRig(tmp_path, config=config).app)


def test_no_site_is_allowed_by_default(tmp_path):
    assert desk_config(tmp_path).web.cors_origins == []


@pytest.mark.parametrize('path', ['/api/status', '/api/todos', '/api/latest_frame_raw/0', '/api/live'])
def test_a_read_from_another_site_gets_no_allow_header(client, path):
    assert ALLOW not in client.get(path, headers=OTHER_SITE).headers


def test_a_read_from_nowhere_or_its_own_page_works_with_no_allow_header(client):
    for headers in ({}, own_page(client)):
        reply = client.get('/api/status', headers=headers)
        assert reply.status_code == 200 and ALLOW not in reply.headers


def test_another_site_cant_get_permission_first(client):
    reply = client.options('/api/rules', headers={**OTHER_SITE, 'Access-Control-Request-Method': 'PUT'})
    assert reply.status_code == 400 and ALLOW not in reply.headers


@pytest.mark.parametrize('method', ['post', 'put', 'delete', 'patch'])
def test_a_write_from_another_site_is_refused(client, method):
    reply = client.request(method, A_WRITE[0], json=A_WRITE[1], headers=OTHER_SITE)
    assert reply.status_code == 403 and set(reply.json()) == {'error'}
    assert ALLOW not in reply.headers


def test_a_write_from_a_page_with_no_real_address_is_refused(client):
    assert client.post(A_WRITE[0], json=A_WRITE[1], headers={'Origin': 'null'}).status_code == 403


def test_a_refused_write_changes_nothing(tmp_path):
    rig = DeskRig(tmp_path)
    client = web_client(rig.app)
    before = client.get('/api/rules').json()
    reply = client.put('/api/rules', json={'desk_outline': [[0, 0], [1, 0], [1, 0.5], [0, 0.5]]}, headers=OTHER_SITE)
    assert reply.status_code == 403 and client.get('/api/rules').json() == before


def test_writes_from_its_own_page_and_from_programs_work(client):
    assert client.post(A_WRITE[0], json=A_WRITE[1], headers=own_page(client)).status_code == 204
    assert client.post(A_WRITE[0], json=A_WRITE[1]).status_code == 204     # no Origin: the camera program


def test_its_own_page_may_use_capital_letters(client):
    assert client.post(A_WRITE[0], json=A_WRITE[1], headers={'Origin': own_page(client)['Origin'].upper()}).status_code == 204


def test_a_site_in_the_settings_may_read_and_write(tmp_path):
    client = listed_client(tmp_path, 'http://friend.example')
    friend = {'Origin': 'http://friend.example'}
    assert client.get('/api/status', headers=friend).headers[ALLOW] == 'http://friend.example'
    assert client.post(A_WRITE[0], json=A_WRITE[1], headers=friend).status_code == 204
    assert client.post(A_WRITE[0], json=A_WRITE[1], headers=OTHER_SITE).status_code == 403


def test_the_phone_camera_page_can_send_and_another_site_cant(tmp_path):
    rig = DeskRig(tmp_path, config=changed(desk_config(tmp_path), 'camera', source='phone'))
    phone = web_client(create_phone_app(rig.app))
    key = {'X-Desk-Key': PAIR_KEY}
    assert phone.post('/api/phone/stop', headers={**key, **own_page(phone)}).status_code == 200
    assert phone.post('/api/phone/stop', headers={**key, **OTHER_SITE}).status_code == 403
