"""The website's "Find Pis on my network": GET /api/pis/nearby and POST /api/pis/link, with a fake home network."""

from __future__ import annotations

import asyncio
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from desktalk_cloud.app import create_app
from desktalk_cloud.domain import FoundPi, GoogleUser
from desktalk_cloud.pi_network import addresses_to_search
from desktalk_cloud.ports import PiOfferFailed
from desktalk_cloud.routers import auth, find_pis, site
from desktalk_cloud.tokens import hash_secret, normal_code
from tests.fakes.cloud import FakeGoogleLogin, FakePiFinder, cloud_config, make_parts

HOME = 'http://192.168.86.30:8080'
DESK = FoundPi('192.168.86.225', 'desktalk', False)
KITCHEN = FoundPi('192.168.86.9', 'kitchen', True)


def build(public_url: str = HOME, pis=(DESK, KITCHEN), **finder_options):
    google = FakeGoogleLogin()
    google.people['ann-code'] = GoogleUser(sub='google-ann', email='ann@example.com', name='Ann', email_verified=True)
    finder = FakePiFinder(pis=pis, **finder_options)
    parts = make_parts(cloud_config(server={'public_url': public_url}), google=google, pi_finder=finder)
    return parts, finder, create_app(parts, [auth.router, site.router, find_pis.router])


def sign_in(app, public_url: str = HOME) -> TestClient:
    client = TestClient(app, follow_redirects=False, base_url=public_url)
    reply = client.get('/auth/google/login')
    state = parse_qs(urlparse(reply.headers['location']).query)['state'][0]
    assert client.get('/auth/google/callback', params={'code': 'ann-code', 'state': state}).status_code == 303
    return client


@pytest.fixture
def home():
    parts, finder, app = build()
    return parts, finder, sign_in(app)


def ann_of(parts):
    (user,) = parts.store.list_users()
    return user


# ── who may ask ────────────────────────────────────────────────────────
def test_both_routes_need_a_login():
    parts, finder, app = build()
    client = TestClient(app)
    assert client.get('/api/pis/nearby').status_code == 401
    reply = client.post('/api/pis/link', json={'address': DESK.address, 'time_zone': 'UTC'})
    assert reply.status_code == 401 and reply.json()['detail'] == parts.config.messages.not_logged_in
    assert finder.searched == [] and finder.offers == []


def test_link_must_be_sent_as_json(home):
    _parts, finder, client = home
    reply = client.post('/api/pis/link', content=b'{"address": "192.168.86.225", "time_zone": "UTC"}',
                        headers={'content-type': 'text/plain'})
    assert reply.status_code == 415 and finder.offers == []


# ── looking ────────────────────────────────────────────────────────────
def test_nearby_lists_the_pis_the_finder_found(home):
    parts, finder, client = home
    reply = client.get('/api/pis/nearby')
    assert reply.status_code == 200
    assert reply.json() == {'pis': [{'address': '192.168.86.9', 'name': 'kitchen', 'linked': True},
                                    {'address': '192.168.86.225', 'name': 'desktalk', 'linked': False}]}
    assert finder.searched == [addresses_to_search(HOME, parts.config.find_pis.network_prefix)]


def test_nearby_with_none_found(home):
    _parts, finder, client = home
    finder.pis = []
    assert client.get('/api/pis/nearby').json() == {'pis': []}


@pytest.mark.parametrize('public_url', ['http://localhost:8080', 'http://desk.example.com', 'http://8.8.8.8:8080',
                                        'http://127.0.0.1:8080'])
def test_nearby_refuses_without_a_home_network_to_search(public_url):
    parts, finder, app = build(public_url)
    reply = sign_in(app, public_url).get('/api/pis/nearby')
    assert reply.status_code == 409
    assert reply.json()['detail'] == parts.config.messages.no_network_to_search
    assert finder.searched == []


def test_a_second_search_while_one_runs_is_refused():
    parts, finder, app = build()

    async def two_searches():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=HOME) as client:
            reply = await client.get('/auth/google/login')
            state = parse_qs(urlparse(reply.headers['location']).query)['state'][0]
            await client.get('/auth/google/callback', params={'code': 'ann-code', 'state': state})
            finder.hold = asyncio.Event()
            first = asyncio.create_task(client.get('/api/pis/nearby'))
            while not finder.searched:
                await asyncio.sleep(0)
            second = await client.get('/api/pis/nearby')
            finder.hold.set()
            return await first, second, await client.get('/api/pis/nearby')
    first, second, after = asyncio.run(two_searches())
    assert first.status_code == 200 and len(first.json()['pis']) == 2
    assert second.status_code == 429 and second.json()['detail'] == parts.config.messages.pi_search_running
    assert after.status_code == 200                     # the search that ended let go
    assert len(finder.searched) == 2


def test_a_search_that_fails_lets_go(home):
    parts, finder, client = home

    async def broken(_addresses):
        raise RuntimeError('boom')
    finder.find = broken
    with pytest.raises(RuntimeError):
        client.get('/api/pis/nearby')
    assert not parts.pi_search.locked()


# ── linking ────────────────────────────────────────────────────────────
def test_link_makes_a_real_code_and_offers_it_to_the_pi(home):
    parts, finder, client = home
    reply = client.post('/api/pis/link', json={'address': DESK.address, 'time_zone': 'Asia/Tokyo'})
    assert reply.status_code == 200 and reply.json() == {'linked': True}
    ((address, server_url, code),) = finder.offers
    assert (address, server_url) == (DESK.address, HOME)
    assert code not in reply.text
    device = parts.store.claim_code(hash_secret(normal_code(code)), 'desktalk', hash_secret('tok'), parts.clock.now())
    assert device is not None
    assert (device.user_id, device.time_zone) == (ann_of(parts).id, 'Asia/Tokyo')


def test_the_offer_uses_the_public_url_without_its_trailing_slash():
    _parts, finder, app = build(HOME + '/')
    sign_in(app, HOME).post('/api/pis/link', json={'address': DESK.address, 'time_zone': 'UTC'})
    assert finder.offers[0][1] == HOME


def test_the_code_lasts_as_long_as_any_other(home):
    parts, finder, client = home
    client.post('/api/pis/link', json={'address': DESK.address, 'time_zone': 'UTC'})
    code = finder.offers[0][2]
    parts.clock.advance(parts.config.pairing.code_valid_min * 60 + 1)
    assert parts.store.claim_code(hash_secret(normal_code(code)), 'x', hash_secret('t'), parts.clock.now()) is None


@pytest.mark.parametrize('address', ['8.8.8.8', '192.168.87.5', '192.168.86.30', '192.168.86.0', '192.168.86.255',
                                     'desktalk.local', '192.168.86.225:80', 'http://192.168.86.225', ''])
def test_link_to_an_address_outside_the_home_network_is_refused(home, address):
    parts, finder, client = home
    reply = client.post('/api/pis/link', json={'address': address, 'time_zone': 'UTC'})
    assert reply.status_code == 400 and reply.json()['detail'] == parts.config.messages.pi_not_in_network
    assert finder.offers == []
    assert_no_code_made(parts)


def assert_no_code_made(parts):
    assert parts.store._codes == {}


def test_link_without_a_home_network_is_refused():
    parts, finder, app = build('http://localhost:8080')
    reply = sign_in(app, 'http://localhost:8080').post('/api/pis/link',
                                                        json={'address': '127.0.0.5', 'time_zone': 'UTC'})
    assert reply.status_code == 400 and finder.offers == []


@pytest.mark.parametrize('zone', ['Not/AZone', '../etc', 'a\x00b', ''])
def test_link_with_a_bad_time_zone_is_refused(home, zone):
    parts, finder, client = home
    reply = client.post('/api/pis/link', json={'address': DESK.address, 'time_zone': zone})
    assert reply.status_code == 400 and reply.json()['detail'] == parts.config.messages.bad_time_zone
    assert finder.offers == []
    assert_no_code_made(parts)


@pytest.mark.parametrize('body', [{}, {'address': DESK.address}, {'time_zone': 'UTC'}, [DESK.address, 'UTC'],
                                  {'address': 5, 'time_zone': 'UTC'}, {'address': DESK.address, 'time_zone': 5}])
def test_link_with_a_body_of_the_wrong_shape_is_refused(home, body):
    parts, finder, client = home
    reply = client.post('/api/pis/link', json=body)
    assert reply.status_code == 400 and finder.offers == []


def test_a_huge_link_request_is_refused(home):
    parts, finder, client = home
    body = b'{"address": "' + b'1' * (parts.config.limits.claim_body_kb * 1024) + b'"}'
    reply = client.post('/api/pis/link', content=body, headers={'content-type': 'application/json'})
    assert reply.status_code == 413 and finder.offers == []


def test_a_pi_that_is_already_linked_is_409(home):
    parts, finder, client = home
    reply = client.post('/api/pis/link', json={'address': KITCHEN.address, 'time_zone': 'UTC'})
    assert reply.status_code == 409 and reply.json()['detail'] == parts.config.messages.pi_already_linked


def test_an_already_linked_answer_with_the_pis_own_words_shows_them(home):
    parts, finder, client = home
    finder.refusals[DESK.address] = PiOfferFailed('already_linked', 'Busy linking another server.')
    reply = client.post('/api/pis/link', json={'address': DESK.address, 'time_zone': 'UTC'})
    assert reply.status_code == 409
    assert reply.json()['detail'] == parts.config.messages.pi_refused.format(reason='Busy linking another server.')


def test_a_pi_that_refuses_is_502_with_its_reason(home):
    parts, finder, client = home
    finder.offer_fails = PiOfferFailed('refused', 'That code is wrong')
    reply = client.post('/api/pis/link', json={'address': DESK.address, 'time_zone': 'UTC'})
    assert reply.status_code == 502
    assert reply.json()['detail'] == parts.config.messages.pi_refused.format(reason='That code is wrong')
    assert 'That code is wrong' in reply.json()['detail']


def test_a_pi_that_refuses_without_a_reason_is_502(home):
    parts, finder, client = home
    finder.offer_fails = PiOfferFailed('refused')
    reply = client.post('/api/pis/link', json={'address': DESK.address, 'time_zone': 'UTC'})
    assert reply.status_code == 502 and reply.json()['detail'] == parts.config.messages.pi_refused_no_reason


def test_a_pi_that_does_not_answer_is_502(home):
    parts, finder, client = home
    reply = client.post('/api/pis/link', json={'address': '192.168.86.77', 'time_zone': 'UTC'})
    assert reply.status_code == 502 and reply.json()['detail'] == parts.config.messages.pi_no_answer


def test_the_code_is_in_no_reply(home):
    parts, finder, client = home
    replies = [client.post('/api/pis/link', json={'address': DESK.address, 'time_zone': 'UTC'})]
    for failure in (PiOfferFailed('refused', 'no'), PiOfferFailed('no_answer'), PiOfferFailed('already_linked')):
        finder.offer_fails = failure
        replies.append(client.post('/api/pis/link', json={'address': DESK.address, 'time_zone': 'UTC'}))
    codes = [code for _, _, code in finder.offers]
    assert len(codes) == 4
    for reply in replies:
        for code in codes:
            assert code not in reply.text and normal_code(code) not in reply.text


def test_a_failed_offer_leaves_a_code_that_simply_runs_out(home):
    parts, finder, client = home
    finder.offer_fails = PiOfferFailed('no_answer')
    client.post('/api/pis/link', json={'address': DESK.address, 'time_zone': 'UTC'})
    assert parts.store.list_devices(ann_of(parts).id) == []
