"""desktalk/core/cloud/linker.py: the Pi says who it is, and links itself to a cloud server when the cloud website
offers it a one-time code, but only to the computer that asks, only once, and never while it is already linked."""

from __future__ import annotations

import logging
import threading
from pathlib import Path

import pytest

from desktalk.config import load_config
from desktalk.core.cloud.linker import CloudLinker
from desktalk.domain import CloudHello, CloudLinkRefused, CloudNotLinked, CloudUnavailable
from desktalk.ports import CloudLinking
from desktalk_shared.cloud_api import ClaimReply
from tests.fakes import FakeClock, FakeDesktopNotifications, InMemoryKeyValueStore

CONFIG = load_config(environ={}, dotenv=Path('/nonexistent'))
KEY = CONFIG.cloud.link_key
WORDS = CONFIG.prompts.cloud
NOW = 1_760_000_000.0
SERVER = 'http://192.168.86.30:8080'
CALLER = '192.168.86.30'
CODE = 'ABCD-EFGH'
TOKEN = 'a-very-secret-token'


class Claims:
    """Stands in for the cloud server's claim route: records each call, gives a token or raises `error`."""

    def __init__(self, device_id: str = 'dev-1', error: Exception | None = None, wait: threading.Event | None = None):
        self.device_id, self.error, self.wait = device_id, error, wait
        self.calls: list[tuple[str, str, str]] = []
        self.started = threading.Event()

    def __call__(self, server: str, code: str, name: str, *, timeout_sec: float, error_text_chars: int) -> ClaimReply:
        self.calls.append((server, code, name))
        self.started.set()
        if self.wait is not None:
            assert self.wait.wait(5)
        if self.error is not None:
            raise self.error
        return ClaimReply(device_id=self.device_id, token=TOKEN)


def make(store: InMemoryKeyValueStore | None = None, claims: Claims | None = None, *,
         public_address: str = 'desktalk.local:5000', saved: bool = True, host_name: str = 'other-host',
         desktop: FakeDesktopNotifications | None = None):
    store = store if store is not None else InMemoryKeyValueStore()
    claims = claims if claims is not None else Claims()
    desktop = desktop if desktop is not None else FakeDesktopNotifications()
    linker = CloudLinker(store, claims, CONFIG.cloud, WORDS, desktop, FakeClock(NOW), public_address=public_address,
                         saved=saved, host_name=lambda: host_name)
    return linker, store, claims, desktop


def refusal(linker: CloudLinker, server: str = SERVER, code: str = CODE, caller: str = CALLER) -> CloudLinkRefused:
    with pytest.raises(CloudLinkRefused) as caught:
        linker.link(server, code, caller)
    return caught.value


def active_link(**changes) -> dict:
    return {'server': 'http://192.168.86.40:8080', 'device_id': 'dev-0', 'token': 'old-token', 'stopped': False,
            'place_ts': NOW - 50, 'start_ts': NOW - 90, 'recent_ids': [], **changes}


def test_the_linker_is_the_cloud_linking_part():
    assert isinstance(make()[0], CloudLinking)


def test_hello_says_its_name_and_not_linked():
    assert make()[0].hello() == CloudHello(name='desktalk', linked=False)


def test_hello_says_linked_with_an_active_link_saved():
    linker, _, _, _ = make(InMemoryKeyValueStore({KEY: active_link()}))
    assert linker.hello().linked is True


DAMAGED = [{'token': ''}, {'device_id': None}, {'place_ts': 1e300}, {'recent_ids': 'oops'}, {'server': 'ftp://x'},
           {'token': 'abc def'}, {'start_ts': float('nan')}, {'stopped': 'maybe'}]


@pytest.mark.parametrize('changes', [{'stopped': True}, *DAMAGED])
def test_a_stopped_or_damaged_link_is_not_linked(changes):
    linker, _, _, _ = make(InMemoryKeyValueStore({KEY: active_link(**changes)}))
    assert linker.hello().linked is False


@pytest.mark.parametrize('changes', DAMAGED)
def test_a_damaged_link_the_sender_cannot_use_does_not_stop_a_new_link(changes):
    """The sender counts a damaged link as not linked, so the Pi says so too and takes a new link."""
    linker, store, claims, _ = make(InMemoryKeyValueStore({KEY: active_link(**changes)}))
    linker.link(SERVER, CODE, caller=CALLER)
    assert len(claims.calls) == 1 and store.load(KEY)['token'] == TOKEN
    assert linker.hello().linked is True


def test_hello_uses_the_host_name_when_there_is_no_public_address():
    assert make(public_address='', host_name='my-mac.local')[0].hello().name == 'my-mac'
    long = 'x' * (CONFIG.cloud.name_max_chars + 20)
    assert make(public_address='', host_name=long)[0].hello().name == 'x' * CONFIG.cloud.name_max_chars


def test_link_claims_saves_and_speaks(caplog):
    linker, store, claims, desktop = make()
    with caplog.at_level(logging.DEBUG):
        linker.link(SERVER, CODE, caller=CALLER)
    assert claims.calls == [(SERVER, CODE, 'desktalk')]
    saved = store.load(KEY)
    assert {name: saved[name] for name in ('server', 'device_id', 'token', 'stopped')} == {
        'server': SERVER, 'device_id': 'dev-1', 'token': TOKEN, 'stopped': False}
    assert (saved['place_ts'], saved['start_ts'], saved['recent_ids']) == (NOW, NOW, [])
    assert desktop.shown == [(WORDS.offer_linked_title, WORDS.offer_linked_said)]
    assert SERVER in caplog.text
    assert CODE not in caplog.text and TOKEN not in caplog.text
    assert linker.hello().linked is True


def test_a_trailing_slash_on_the_server_is_dropped():
    linker, store, claims, _ = make()
    linker.link(SERVER + '/', CODE, caller=CALLER)
    assert claims.calls[0][0] == SERVER and store.load(KEY)['server'] == SERVER


def test_link_refuses_a_server_that_is_not_the_caller():
    linker, store, claims, _ = make()
    assert refusal(linker, caller='192.168.86.99').kind == 'not_caller'
    assert claims.calls == [] and store.keys() == []


def test_a_caller_written_as_an_ipv6_mapped_address_is_the_same_caller():
    linker, _, claims, _ = make()
    linker.link(SERVER, CODE, caller='::ffff:192.168.86.30')
    assert len(claims.calls) == 1


@pytest.mark.parametrize('server, caller', [('http://example.com:8080', 'example.com'),
                                            ('http://8.8.8.8:8080', '8.8.8.8'),
                                            ('http://127.0.0.1:8080', '127.0.0.1'),
                                            ('http://[fe80::1]:8080', 'fe80::1'),
                                            ('http://169.254.1.1:8080', '169.254.1.1')])
def test_link_refuses_a_public_or_named_server(server, caller):
    linker, _, claims, _ = make()
    assert refusal(linker, server=server, caller=caller).kind == 'not_caller'
    assert claims.calls == []


@pytest.mark.parametrize('server, code', [('ftp://192.168.86.30', CODE), ('file:///etc/passwd', CODE),
                                          ('192.168.86.30:8080', CODE), ('http://', CODE),
                                          ('http://192.168.86.30:99999', CODE), (SERVER, ''), (SERVER, '   ')])
def test_link_refuses_a_bad_address_or_empty_code(server, code):
    linker, _, claims, _ = make()
    refused = refusal(linker, server=server, code=code)
    assert refused.kind == 'bad_request' and claims.calls == []
    assert CODE not in refused.text


def test_link_refuses_while_linked():
    old = active_link()
    linker, store, claims, desktop = make(InMemoryKeyValueStore({KEY: old}))
    refused = refusal(linker)
    assert refused.kind == 'already_linked' and refused.text == WORDS.offer_already_linked
    assert store.load(KEY) == old and store.saves == 0 and claims.calls == [] and desktop.shown == []


def test_a_stopped_link_does_not_block_a_new_one():
    linker, store, claims, _ = make(InMemoryKeyValueStore({KEY: active_link(stopped=True)}))
    linker.link(SERVER, CODE, caller=CALLER)
    assert len(claims.calls) == 1 and store.load(KEY)['token'] == TOKEN


def test_link_refuses_when_nothing_is_saved():
    linker, store, claims, _ = make(saved=False)
    refused = refusal(linker)
    assert refused.kind == 'not_saved' and refused.text == WORDS.offer_not_saved
    assert claims.calls == [] and store.keys() == []


def test_a_second_offer_while_one_is_claiming_is_busy():
    release = threading.Event()
    linker, store, claims, _ = make(claims=Claims(wait=release))
    first = threading.Thread(target=linker.link, args=(SERVER, CODE, CALLER))
    first.start()
    try:
        assert claims.started.wait(5)
        refused = refusal(linker, code='OTHR-CODE')
        assert refused.kind == 'busy' and refused.text == WORDS.offer_busy
    finally:
        release.set()
        first.join(5)
    assert claims.calls == [(SERVER, CODE, 'desktalk')] and store.load(KEY)['token'] == TOKEN
    assert refusal(linker).kind == 'already_linked'         # and once it is done, the next one finds it linked


def test_the_busy_lock_is_let_go_after_a_failure():
    claims = Claims(error=CloudUnavailable('no answer within 10 s'))
    linker, _, _, _ = make(claims=claims)
    assert refusal(linker).kind == 'unavailable'
    claims.error = None
    linker.link(SERVER, CODE, caller=CALLER)
    assert len(claims.calls) == 2


def test_a_refused_code_saves_nothing():
    linker, store, _, desktop = make(claims=Claims(error=CloudNotLinked('That code is wrong')))
    refused = refusal(linker)
    assert refused.kind == 'refused' and 'That code is wrong' in refused.text
    assert store.keys() == [] and desktop.shown == []


def test_a_refusal_with_no_reason_says_the_code_was_not_accepted():
    linker, _, _, _ = make(claims=Claims(error=CloudNotLinked('')))
    assert refusal(linker).text == WORDS.link_code_refused


def test_a_server_that_does_not_answer_saves_nothing():
    linker, store, _, _ = make(claims=Claims(error=CloudUnavailable('no answer within 10 s')))
    refused = refusal(linker)
    assert refused.kind == 'unavailable' and 'no answer within 10 s' in refused.text
    assert store.keys() == []


def test_the_code_is_never_in_a_refusal_or_the_log(caplog):
    with caplog.at_level(logging.DEBUG):
        for claims in (Claims(error=CloudNotLinked('nope')), Claims(error=CloudUnavailable('off'))):
            assert CODE not in refusal(make(claims=claims)[0]).text
        refusal(make(InMemoryKeyValueStore({KEY: active_link()}))[0])
        refusal(make()[0], caller='192.168.86.99')
    assert CODE not in caplog.text


def test_relinking_the_same_device_keeps_its_counters():
    old = active_link(device_id='dev-1', stopped=True, place_ts=NOW - 5_000, start_ts=NOW - 90_000,
                      recent_ids=['a', 'b'])
    linker, store, _, _ = make(InMemoryKeyValueStore({KEY: old}), Claims(device_id='dev-1'))
    linker.link(SERVER, CODE, caller=CALLER)
    assert store.load(KEY) == {'server': SERVER, 'device_id': 'dev-1', 'token': TOKEN, 'stopped': False,
                               'place_ts': NOW - 5_000, 'start_ts': NOW - 90_000, 'recent_ids': ['a', 'b']}
