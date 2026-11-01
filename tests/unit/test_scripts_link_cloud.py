"""scripts/link_cloud.py: trade a one-time code for a token and save the link (the server's address, the device's
id, the token and the place to send from) where the cloud sender reads it."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from desktalk.config import load_config
from desktalk.domain import CloudNotLinked, CloudUnavailable
from desktalk_shared.cloud_api import ClaimReply
from tests.fakes import FakeClock, InMemoryKeyValueStore

ROOT = Path(__file__).resolve().parents[2]
CONFIG = load_config(environ={}, dotenv=Path('/nonexistent'))
KEY = CONFIG.cloud.link_key
TOKEN = 'a-very-secret-token'
NOW = 1_760_000_000.0


def load_script():
    spec = importlib.util.spec_from_file_location('link_cloud_script', ROOT / 'scripts' / 'link_cloud.py')
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


script = load_script()


class Claims:
    """Stands in for the server: gives the token for the right code, refuses the rest."""

    def __init__(self, code='RIGHT-CODE', device_id='dev-1', token=TOKEN, error: Exception | None = None):
        self.code, self.device_id, self.token, self.error = code, device_id, token, error
        self.calls: list[tuple[str, str, str]] = []

    def __call__(self, server: str, code: str, name: str, *, timeout_sec: float, error_text_chars: int) -> ClaimReply:
        self.calls.append((server, code, name))
        if self.error is not None:
            raise self.error
        if code != self.code:
            raise CloudNotLinked('')                      # the server gave no reason
        return ClaimReply(device_id=self.device_id, token=self.token)


def run(argv, claims, store=None, said=None):
    store = store if store is not None else InMemoryKeyValueStore()
    said = said if said is not None else []
    code = script.run(argv, config=CONFIG, store=store, clock=FakeClock(NOW), claim=claims, say=said.append)
    return code, store, said


def test_link_script_saves_token_and_place():
    claims = Claims()
    code, store, said = run(['--server', 'http://192.168.1.9:8000/', '--code', 'RIGHT-CODE', '--name', 'Desk'], claims)
    assert code == 0
    assert store.load(KEY) == {'server': 'http://192.168.1.9:8000', 'device_id': 'dev-1', 'token': TOKEN,
                               'place_ts': NOW, 'start_ts': NOW, 'recent_ids': [], 'stopped': False}
    assert claims.calls == [('http://192.168.1.9:8000', 'RIGHT-CODE', 'Desk')]
    assert 'dev-1' in said[0] and 'http://192.168.1.9:8000' in said[0]


def test_link_script_never_prints_the_token():
    _, _, said = run(['--server', 'http://x:1', '--code', 'RIGHT-CODE'], Claims())
    assert TOKEN not in ' '.join(said)


def test_link_script_wrong_code_says_so_and_saves_nothing():
    code, store, said = run(['--server', 'http://x:1', '--code', 'WRONG'], Claims())
    assert code != 0 and store.keys() == []
    assert said == [CONFIG.prompts.cloud.link_code_refused]


def test_link_script_with_the_server_off_says_so_and_saves_nothing():
    code, store, said = run(['--server', 'http://x:1', '--code', 'RIGHT-CODE'],
                            Claims(error=CloudUnavailable('it is not running')))
    assert code != 0 and store.keys() == []
    assert 'http://x:1' in said[0] and 'it is not running' in said[0]


def test_linking_again_keeps_the_place_when_it_is_the_same_device():
    old = {'server': 'http://old:1', 'device_id': 'dev-1', 'token': 'old-token', 'place_ts': NOW - 5_000,
           'start_ts': NOW - 90_000, 'recent_ids': ['a', 'b'], 'stopped': True}
    store = InMemoryKeyValueStore({KEY: old})
    code, store, _ = run(['--server', 'http://new:2', '--code', 'RIGHT-CODE'], Claims(device_id='dev-1'), store)
    assert code == 0
    assert store.load(KEY) == {'server': 'http://new:2', 'device_id': 'dev-1', 'token': TOKEN,
                               'place_ts': NOW - 5_000, 'start_ts': NOW - 90_000, 'recent_ids': ['a', 'b'],
                               'stopped': False}


def test_linking_a_different_device_starts_from_now():
    old = {'server': 'http://old:1', 'device_id': 'dev-1', 'token': 'old-token', 'place_ts': NOW - 5_000,
           'start_ts': NOW - 90_000, 'recent_ids': ['a'], 'stopped': False}
    store = InMemoryKeyValueStore({KEY: old})
    run(['--server', 'http://new:2', '--code', 'RIGHT-CODE'], Claims(device_id='dev-2'), store)
    saved = store.load(KEY)
    assert (saved['device_id'], saved['place_ts'], saved['start_ts'], saved['recent_ids']) == ('dev-2', NOW, NOW, [])


def test_a_server_address_that_is_not_a_web_address_is_refused():
    code, store, said = run(['--server', 'file:///etc/passwd', '--code', 'RIGHT-CODE'], Claims())
    assert code != 0 and store.keys() == [] and said


def test_the_script_needs_a_server_and_a_code():
    with pytest.raises(SystemExit):
        script.run(['--code', 'x'], config=CONFIG, store=InMemoryKeyValueStore(), clock=FakeClock(NOW),
                   claim=Claims(), say=lambda text: None)


# ── a damaged link already saved ──
@pytest.mark.parametrize('old', [
    {'device_id': 'dev-1', 'recent_ids': 5, 'place_ts': 'soon', 'start_ts': float('nan')},
    {'device_id': 'dev-1', 'recent_ids': [1, None], 'place_ts': float('inf')},
    {'device_id': ['dev-1']}, ['a', 'list'], 'text', 7])
def test_a_damaged_old_link_does_not_crash_the_script_after_the_code_is_used(old):
    store = InMemoryKeyValueStore({KEY: old})
    code, store, _ = run(['--server', 'http://x:1', '--code', 'RIGHT-CODE'], Claims(), store)
    saved = store.load(KEY)
    assert code == 0 and saved['token'] == TOKEN
    assert saved['recent_ids'] == [] and saved['place_ts'] == NOW and saved['start_ts'] == NOW


def test_a_half_good_old_link_for_the_same_device_keeps_what_is_good():
    old = {'device_id': 'dev-1', 'place_ts': NOW - 50, 'start_ts': 'bad', 'recent_ids': ['a', 3, 'b']}
    code, store, _ = run(['--server', 'http://x:1', '--code', 'RIGHT-CODE'], Claims(),
                         InMemoryKeyValueStore({KEY: old}))
    saved = store.load(KEY)
    assert saved['place_ts'] == NOW - 50 and saved['start_ts'] == NOW - 50 and saved['recent_ids'] == ['a', 'b']


# ── the reason the server gives ──
def test_a_reason_from_the_server_is_shown_so_a_too_long_name_does_not_read_as_a_bad_code():
    code, store, said = run(['--server', 'http://x:1', '--code', 'RIGHT-CODE'],
                            Claims(error=CloudNotLinked('The name is too long.')))
    assert code != 0 and store.keys() == []
    assert said == [CONFIG.prompts.cloud.link_refused_because.format(reason='The name is too long.')]


def test_a_long_reason_is_cut():
    long = 'x' * (CONFIG.cloud.error_text_chars + 300)
    _, _, said = run(['--server', 'http://x:1', '--code', 'RIGHT-CODE'], Claims(error=CloudNotLinked(long)))
    assert said[0].count('x') == CONFIG.cloud.error_text_chars
