"""GET /api/objects/states, /api/objects/smoothing and /api/objects/smoothing/trace (desktalk/web/routers/objects.py):
what the camera sees now and how each object's smoothing went, for a computer whose camera is the Pi to ask the Pi.
Any device on the Wi-Fi may ask. The developer page's /api/dev/states and the rest give the same replies on a
computer whose camera isn't elsewhere."""

from __future__ import annotations

import pytest

from tests.integration.app_kit import CUP_FAR, DeskRig, changed, desk_config
from tests.integration.web_kit import ON_THE_WIFI
from tests.integration.web_kit import web_client as main_client

ROUTES = ['/api/objects/states?since=0', '/api/objects/smoothing', '/api/objects/smoothing/trace?label=cup']


@pytest.fixture
def rig(tmp_path) -> DeskRig:
    """A desk with a phone camera (so the developer page answers from here) that watched a cup until it appeared."""
    rig = DeskRig(tmp_path, config=changed(desk_config(tmp_path), 'camera', source='phone'))
    rig.watch(rig.config.object_states.appear_sec + 2, CUP_FAR)
    return rig


def test_states_reply_matches_the_dev_reply(rig):
    client = main_client(rig.app)
    reply = client.get('/api/objects/states?since=0')
    assert reply.status_code == 200
    states = reply.json()
    assert states['objects']['cup']['state'] == 'PRESENT'
    assert states['changes']
    assert {**states, 'from': 'here', 'address': ''} == client.get('/api/dev/states?since=0').json()


def test_smoothing_lists_traced_labels(rig):
    reply = main_client(rig.app).get('/api/objects/smoothing').json()
    assert set(reply) == {'labels'}
    assert 'cup' in reply['labels']


def test_trace_of_a_word_never_seen_is_empty(rig):
    client = main_client(rig.app)
    reply = client.get('/api/objects/smoothing/trace?label=pen%2Fpencil')
    assert (reply.status_code, reply.json()) == (200, {'label': 'pen/pencil', 'steps': []})
    cup = client.get('/api/objects/smoothing/trace?label=cup').json()
    assert {**cup, 'from': 'here', 'address': ''} == client.get('/api/dev/smoothing/trace?label=cup').json()
    assert cup['steps']


@pytest.mark.parametrize('path', ROUTES)
def test_any_device_on_the_wifi_may_ask(rig, path):
    assert main_client(rig.app, from_ip=ON_THE_WIFI).get(path).status_code == 200
