"""The website's "Find Pis on my network": GET /api/pis/where_to_look (the browser does the knocking) and
POST /api/pis/link, with a fake home network."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from desktalk_cloud.app import create_app
from desktalk_cloud.domain import GoogleUser
from desktalk_cloud.pi_network import addresses_to_search
from desktalk_cloud.ports import PiOfferFailed
from desktalk_cloud.routers import auth, find_pis, site
from desktalk_cloud.tokens import hash_secret, normal_code
from tests.fakes.cloud import FakeGoogleLogin, FakePiOffers, cloud_config, make_parts

HOME = 'http://192.168.86.30:8080'
DESK = '192.168.86.225'                 # a Pi that takes an offer
KITCHEN = '192.168.86.9'                # a Pi that is linked already


def build(public_url: str = HOME, **offer_options):
    google = FakeGoogleLogin()
    google.people['ann-code'] = GoogleUser(sub='google-ann', email='ann@example.com', name='Ann', email_verified=True)
    pis = FakePiOffers(pis=[DESK], linked=[KITCHEN], **offer_options)
    parts = make_parts(cloud_config(server={'public_url': public_url}), google=google, pi_offers=pis)
    return parts, pis, create_app(parts, [auth.router, site.router, find_pis.router])


def sign_in(app, public_url: str = HOME) -> TestClient:
    client = TestClient(app, follow_redirects=False, base_url=public_url)
    reply = client.get('/auth/google/login')
    state = parse_qs(urlparse(reply.headers['location']).query)['state'][0]
    assert client.get('/auth/google/callback', params={'code': 'ann-code', 'state': state}).status_code == 303
    return client


@pytest.fixture
def home():
    parts, pis, app = build()
    return parts, pis, sign_in(app)


def ann_of(parts):
    (user,) = parts.store.list_users()
    return user


# ── who may ask ────────────────────────────────────────────────────────
def test_both_routes_need_a_login():
    parts, pis, app = build()
    client = TestClient(app)
    assert client.get('/api/pis/where_to_look').status_code == 401
    reply = client.post('/api/pis/link', json={'address': DESK, 'time_zone': 'UTC'})
    assert reply.status_code == 401 and reply.json()['detail'] == parts.config.messages.not_logged_in
    assert pis.offers == []


def test_link_must_be_sent_as_json(home):
    _parts, pis, client = home
    reply = client.post('/api/pis/link', content=b'{"address": "192.168.86.225", "time_zone": "UTC"}',
                        headers={'content-type': 'text/plain'})
    assert reply.status_code == 415 and pis.offers == []


# ── where to look ─────────────────────────────────────────────────────
def test_where_to_look_gives_the_page_the_addresses_and_how_to_knock(home):
    parts, pis, client = home
    reply = client.get('/api/pis/where_to_look')
    assert reply.status_code == 200
    settings = parts.config.find_pis
    assert reply.json() == {'addresses': addresses_to_search(HOME, settings.network_prefix),
                            'port': settings.pi_port, 'wait_ms': round(settings.knock_timeout_sec * 1000),
                            'at_once': settings.at_once, 'name_max_chars': parts.config.pairing.name_max_chars,
                            'reply_max_kb': settings.reply_max_kb}
    assert pis.offers == []                          # the server knocks on nothing: the browser does


def test_wait_ms_is_a_whole_number_of_milliseconds():
    google = FakeGoogleLogin()
    google.people['ann-code'] = GoogleUser(sub='google-ann', email='ann@example.com', name='Ann', email_verified=True)
    parts = make_parts(cloud_config(server={'public_url': HOME}, find_pis={'knock_timeout_sec': 0.25}), google=google)
    client = sign_in(create_app(parts, [auth.router, site.router, find_pis.router]))
    assert client.get('/api/pis/where_to_look').json()['wait_ms'] == 250


@pytest.mark.parametrize('public_url', ['http://localhost:8080', 'http://desk.example.com', 'http://8.8.8.8:8080',
                                        'http://127.0.0.1:8080'])
def test_where_to_look_refuses_without_a_home_network_to_search(public_url):
    parts, _pis, app = build(public_url)
    reply = sign_in(app, public_url).get('/api/pis/where_to_look')
    assert reply.status_code == 409
    assert reply.json()['detail'] == parts.config.messages.no_network_to_search


def test_the_old_search_route_is_gone(home):
    _parts, _pis, client = home
    assert client.get('/api/pis/nearby').status_code == 404


# ── linking ────────────────────────────────────────────────────────────
def test_link_makes_a_real_code_and_offers_it_to_the_pi(home):
    parts, pis, client = home
    reply = client.post('/api/pis/link', json={'address': DESK, 'time_zone': 'Asia/Tokyo'})
    assert reply.status_code == 200 and reply.json() == {'linked': True}
    ((address, server_url, code),) = pis.offers
    assert (address, server_url) == (DESK, HOME)
    assert code not in reply.text
    device = parts.store.claim_code(hash_secret(normal_code(code)), 'desktalk', hash_secret('tok'), parts.clock.now())
    assert device is not None
    assert (device.user_id, device.time_zone) == (ann_of(parts).id, 'Asia/Tokyo')


def test_the_offer_uses_the_public_url_without_its_trailing_slash():
    _parts, pis, app = build(HOME + '/')
    sign_in(app, HOME).post('/api/pis/link', json={'address': DESK, 'time_zone': 'UTC'})
    assert pis.offers[0][1] == HOME


def test_the_code_lasts_as_long_as_any_other(home):
    parts, pis, client = home
    client.post('/api/pis/link', json={'address': DESK, 'time_zone': 'UTC'})
    code = pis.offers[0][2]
    parts.clock.advance(parts.config.pairing.code_valid_min * 60 + 1)
    assert parts.store.claim_code(hash_secret(normal_code(code)), 'x', hash_secret('t'), parts.clock.now()) is None


@pytest.mark.parametrize('address', ['8.8.8.8', '192.168.87.5', '192.168.86.30', '192.168.86.0', '192.168.86.255',
                                     'desktalk.local', '192.168.86.225:80', 'http://192.168.86.225', ''])
def test_link_to_an_address_outside_the_home_network_is_refused(home, address):
    parts, pis, client = home
    reply = client.post('/api/pis/link', json={'address': address, 'time_zone': 'UTC'})
    assert reply.status_code == 400 and reply.json()['detail'] == parts.config.messages.pi_not_in_network
    assert pis.offers == []
    assert_no_code_made(parts)


def assert_no_code_made(parts):
    assert parts.store._codes == {}


def test_link_without_a_home_network_is_refused():
    parts, pis, app = build('http://localhost:8080')
    reply = sign_in(app, 'http://localhost:8080').post('/api/pis/link',
                                                        json={'address': '127.0.0.5', 'time_zone': 'UTC'})
    assert reply.status_code == 400 and pis.offers == []


@pytest.mark.parametrize('zone', ['Not/AZone', '../etc', 'a\x00b', ''])
def test_link_with_a_bad_time_zone_is_refused(home, zone):
    parts, pis, client = home
    reply = client.post('/api/pis/link', json={'address': DESK, 'time_zone': zone})
    assert reply.status_code == 400 and reply.json()['detail'] == parts.config.messages.bad_time_zone
    assert pis.offers == []
    assert_no_code_made(parts)


@pytest.mark.parametrize('body', [{}, {'address': DESK}, {'time_zone': 'UTC'}, [DESK, 'UTC'],
                                  {'address': 5, 'time_zone': 'UTC'}, {'address': DESK, 'time_zone': 5}])
def test_link_with_a_body_of_the_wrong_shape_is_refused(home, body):
    parts, pis, client = home
    reply = client.post('/api/pis/link', json=body)
    assert reply.status_code == 400 and pis.offers == []


def test_a_huge_link_request_is_refused(home):
    parts, pis, client = home
    body = b'{"address": "' + b'1' * (parts.config.limits.claim_body_kb * 1024) + b'"}'
    reply = client.post('/api/pis/link', content=body, headers={'content-type': 'application/json'})
    assert reply.status_code == 413 and pis.offers == []


def test_a_pi_that_is_already_linked_is_409(home):
    parts, pis, client = home
    reply = client.post('/api/pis/link', json={'address': KITCHEN, 'time_zone': 'UTC'})
    assert reply.status_code == 409 and reply.json()['detail'] == parts.config.messages.pi_already_linked


def test_an_already_linked_answer_says_so_in_this_servers_words(home):
    parts, pis, client = home
    pis.refusals[DESK] = PiOfferFailed('already_linked', 'Some words from the Pi.')
    reply = client.post('/api/pis/link', json={'address': DESK, 'time_zone': 'UTC'})
    assert reply.status_code == 409 and reply.json()['detail'] == parts.config.messages.pi_already_linked


@pytest.mark.parametrize(('kind', 'status', 'message'), [
    ('busy', 409, 'pi_busy'),
    ('not_saved', 409, 'pi_not_saved'),
    ('not_caller', 502, 'pi_not_caller'),
    ('unavailable', 502, 'pi_cant_reach_server'),
    ('something_new', 502, 'pi_no_answer'),
])
def test_each_way_the_pi_says_no_gets_its_own_words(home, kind, status, message):
    parts, pis, client = home
    pis.refusals[DESK] = PiOfferFailed(kind, 'Some words from the Pi.')
    reply = client.post('/api/pis/link', json={'address': DESK, 'time_zone': 'UTC'})
    assert reply.status_code == status
    assert reply.json()['detail'] == getattr(parts.config.messages, message).format(server=HOME)
    assert 'Some words from the Pi.' not in reply.text


def test_the_words_for_a_pi_that_cant_reach_this_server_name_public_url(home):
    parts, pis, client = home
    pis.refusals[DESK] = PiOfferFailed('unavailable')
    reply = client.post('/api/pis/link', json={'address': DESK, 'time_zone': 'UTC'})
    assert HOME in reply.json()['detail']


def test_a_pi_that_refuses_is_502_with_its_reason(home):
    parts, pis, client = home
    pis.offer_fails = PiOfferFailed('refused', 'That code is wrong')
    reply = client.post('/api/pis/link', json={'address': DESK, 'time_zone': 'UTC'})
    assert reply.status_code == 502
    assert reply.json()['detail'] == parts.config.messages.pi_refused.format(reason='That code is wrong')
    assert 'That code is wrong' in reply.json()['detail']


def test_a_pi_that_refuses_without_a_reason_is_502(home):
    parts, pis, client = home
    pis.offer_fails = PiOfferFailed('refused')
    reply = client.post('/api/pis/link', json={'address': DESK, 'time_zone': 'UTC'})
    assert reply.status_code == 502 and reply.json()['detail'] == parts.config.messages.pi_refused_no_reason


def test_a_pi_that_does_not_answer_is_502(home):
    parts, pis, client = home
    reply = client.post('/api/pis/link', json={'address': '192.168.86.77', 'time_zone': 'UTC'})
    assert reply.status_code == 502 and reply.json()['detail'] == parts.config.messages.pi_no_answer


def test_the_code_is_in_no_reply(home):
    parts, pis, client = home
    replies = [client.post('/api/pis/link', json={'address': DESK, 'time_zone': 'UTC'})]
    for failure in (PiOfferFailed('refused', 'no'), PiOfferFailed('no_answer'), PiOfferFailed('already_linked')):
        pis.offer_fails = failure
        replies.append(client.post('/api/pis/link', json={'address': DESK, 'time_zone': 'UTC'}))
    codes = [code for _, _, code in pis.offers]
    assert len(codes) == 4
    for reply in replies:
        for code in codes:
            assert code not in reply.text and normal_code(code) not in reply.text


def test_a_failed_offer_leaves_a_code_that_simply_runs_out(home):
    parts, pis, client = home
    pis.offer_fails = PiOfferFailed('no_answer')
    client.post('/api/pis/link', json={'address': DESK, 'time_zone': 'UTC'})
    assert parts.store.list_devices(ann_of(parts).id) == []


def test_a_pi_that_took_the_code_but_answered_too_late_counts_as_linked():
    def claims_then_times_out(_address, _server_url, code):
        assert parts.store.claim_code(hash_secret(code), 'desktalk', hash_secret('tok'), parts.clock.now())
        raise PiOfferFailed('no_answer')
    parts, pis, app = build(on_offer=claims_then_times_out)
    client = sign_in(app)
    reply = client.post('/api/pis/link', json={'address': DESK, 'time_zone': 'UTC'})
    assert reply.status_code == 200 and reply.json() == {'linked': True}
    assert len(parts.store.list_devices(ann_of(parts).id)) == 1


def test_only_no_answer_looks_for_a_claimed_code():
    def claims_then_refuses(_address, _server_url, code):
        assert parts.store.claim_code(hash_secret(code), 'desktalk', hash_secret('tok'), parts.clock.now())
        raise PiOfferFailed('unavailable')
    parts, pis, app = build(on_offer=claims_then_refuses)
    reply = sign_in(app).post('/api/pis/link', json={'address': DESK, 'time_zone': 'UTC'})
    assert reply.status_code == 502
