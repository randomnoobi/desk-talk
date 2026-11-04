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
    shouted = {'Origin': own_page(client)['Origin'].upper()}
    assert client.post(A_WRITE[0], json=A_WRITE[1], headers=shouted).status_code == 204


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


# ── a web site that points its own name at this server (DNS "rebinding") ──
WEB_PORT = 5000
OWN_NAMES = ['127.0.0.1:{port}', 'localhost:{port}', 'LocalHost:{port}', '[::1]:{port}', '192.168.1.20:{port}',
             '[fe80::1]:{port}', '10.0.0.5', 'desktalk.local:{port}', 'DeskTalk.Local:{port}', 'desktalk.local.:{port}']
OTHER_NAMES = ['evil.example:{port}', 'evil.example', 'localhost.evil.example:{port}', '127.0.0.1.evil.example:{port}',
               'desktalk.local.evil.example:{port}', 'testserver', 'app:{port}', '[::1', '']


def at(name: str, client) -> dict[str, str]:
    """What a browser sends when it opened this server under `name`: the Host, and its page as the Origin."""
    host = name.format(port=WEB_PORT)
    return {'Host': host, 'Origin': f'http://{host}'}


@pytest.mark.parametrize('name', OWN_NAMES)
def test_its_own_names_and_addresses_may_read_and_write(client, name):
    assert client.get('/api/todos', headers={'Host': name.format(port=WEB_PORT)}).status_code == 200
    assert client.post(A_WRITE[0], json=A_WRITE[1], headers=at(name, client)).status_code == 204


@pytest.mark.parametrize('name', OTHER_NAMES)
def test_another_name_can_neither_read_nor_write(tmp_path, name):
    rig = DeskRig(tmp_path)
    client = web_client(rig.app)
    refused = (403, {'error': rig.config.prompts.dev.other_name})
    reply = client.get('/api/todos', headers={'Host': name.format(port=WEB_PORT)})
    assert (reply.status_code, reply.json()) == refused
    reply = client.post(A_WRITE[0], json=A_WRITE[1], headers=at(name, client))
    assert (reply.status_code, reply.json()) == refused


def test_the_public_address_and_the_names_in_the_settings_work(tmp_path):
    config = changed(desk_config(tmp_path), 'web', public_address=f'desk.home:{WEB_PORT}', port=WEB_PORT,
                     allowed_hosts=['proxy.example.com'])
    client = web_client(DeskRig(tmp_path, config=config).app)
    for name in ('desk.home:{port}', 'proxy.example.com', 'Proxy.Example.com:443'):
        assert client.get('/api/status', headers={'Host': name.format(port=WEB_PORT)}).status_code == 200
    assert client.get('/api/status', headers={'Host': 'evil.example'}).status_code == 403


def test_no_other_name_is_allowed_by_default(tmp_path):
    assert desk_config(tmp_path).web.allowed_hosts == []


def test_the_phone_camera_server_checks_the_name_too(tmp_path):
    rig = DeskRig(tmp_path, config=changed(desk_config(tmp_path), 'camera', source='phone'))
    phone = web_client(create_phone_app(rig.app))
    assert phone.get('/api/phone/config', headers={'Host': '192.168.1.20:8443'}).status_code != 403
    assert phone.get('/api/phone/config', headers={'Host': 'evil.example:8443'}).status_code == 403
