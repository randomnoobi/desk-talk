"""llm.ai_url, the Pi's one way to an AI: text only, and the cloud token only ever going to the server it came from.
The cloud path is checked here with a fake client only: no real cloud server has been tried."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from desktalk.adapters.llm.errors import AIRefused
from desktalk.adapters.llm.openai_compatible import OpenAICompatibleChatModel, is_chat_address_of
from desktalk.adapters.llm.resilient import ResilientChatModel
from desktalk.config import Secrets, load_config
from desktalk.core.cloud import linked_server
from desktalk.domain import AIUnavailable
from tests.conftest import FakeAI
from tests.fakes import FakeClock, InMemoryKeyValueStore

CONFIG = load_config(environ={}, dotenv=Path('/nonexistent'))
HELLO = [{'role': 'user', 'content': 'Say hello.'}]
SERVER = 'https://cloud.example.org'
TOKEN = 'device-token-abc123'
JPEG = b'\xff\xd8 a picture'


class Factory:
    def __init__(self, reply='Hi there, nice desk you have!'):
        self.reply = reply
        self.clients = []

    def __call__(self, **kw):
        self.clients.append(FakeAI(reply=self.reply, **kw))
        return self.clients[-1]

    def asked(self):
        return [c for c in self.clients if c.requests]


def make(ai_url, *, link=None, reply: Any = 'Hi there, nice desk you have!', ollama='', **llm):
    """`link`: what the store holds under the link key (changed later with `store`)."""
    store = InMemoryKeyValueStore({} if link is None else {CONFIG.cloud.link_key: link})
    factory = Factory(reply)
    settings = CONFIG.llm.model_copy(update={'ai_url': ai_url, 'ollama_model': 'small-model', **llm})
    none = SecretStr('')
    secrets = Secrets(gemini_api_key=none, openai_api_key=none, groq_api_key=none, ollama_url=ollama)
    model = OpenAICompatibleChatModel(settings, secrets, FakeClock(), client_factory=factory,
                                      replies=CONFIG.prompts.replies,
                                      cloud_link=lambda: linked_server(store, CONFIG.cloud.link_key))
    return model, factory, store


def linked(server=SERVER, token=TOKEN):
    return {'server': server, 'device_id': 'pi-1', 'token': token}


def say_hello(model):
    return model.chat(HELLO, max_tokens=50, temperature=0.7)


# ── which way in ──────────────────────────────────────────────────────
def test_empty_means_env_decides_as_today():
    model, factory, _ = make('', ollama='http://ollama.test/v1')
    say_hello(model)
    assert model.is_local and [c.base_url for c in factory.asked()] == ['http://ollama.test/v1']


def test_nothing_set_means_no_ai():
    model, factory, _ = make('')
    assert not model.ready and factory.clients == []
    with pytest.raises(AIUnavailable, match=CONFIG.prompts.replies.needs_an_ai):
        say_hello(model)
    assert 'set up' in model.label


def test_ai_url_wins_over_ollama_url_and_uses_ollamas_model_name():
    model, factory, _ = make('http://laptop.test:11434/v1', ollama='http://ollama.test/v1')
    say_hello(model)
    [client] = factory.asked()
    assert client.base_url == 'http://laptop.test:11434/v1' and client.requests[0]['model'] == 'small-model'
    assert model.is_local and model.keepalive_worker() is not None


# ── text only ─────────────────────────────────────────────────────────
@pytest.mark.parametrize('link', [None, linked('http://laptop.test:11434')])
def test_pictures_never_go_to_ai_url(link):
    model, factory, _ = make('http://laptop.test:11434/v1', link=link)
    assert not model.local_vision
    with pytest.raises(AIRefused):
        model.vision(JPEG, 'What is this?')
    assert factory.asked() == []


def test_a_picture_inside_a_chat_message_is_refused_too():
    model, factory, _ = make('http://laptop.test:11434/v1')
    with_picture = [{'role': 'user', 'content': [{'type': 'image_url', 'image_url': {'url': 'data:x'}}]}]
    with pytest.raises(AIRefused):
        model.chat(with_picture, max_tokens=50, temperature=0.7)
    assert factory.asked() == []


def test_send_pictures_off_keeps_them_from_ollama_url():
    model, factory, _ = make('', ollama='http://ollama.test/v1', send_pictures=False)
    assert model.ready and not model.local_vision
    with pytest.raises(AIRefused):
        model.vision(JPEG, 'What is this?')
    assert factory.asked() == []


# ── the token ─────────────────────────────────────────────────────────
def test_the_token_goes_to_the_linked_servers_chat_address():
    model, factory, _ = make(f'{SERVER}/v1', link=linked())
    say_hello(model)
    [client] = factory.asked()
    assert client.api_key == TOKEN and client.base_url == f'{SERVER}/v1'


@pytest.mark.parametrize('ai_url', ['https://other.example.org/v1', 'http://cloud.example.org/v1',
                                    'https://cloud.example.org:8443/v1', 'https://cloud.example.org/elsewhere/v1',
                                    'https://cloud.example.org.evil.test/v1', 'http://laptop.test:11434/v1'])
def test_no_other_address_ever_sees_the_token(ai_url):
    model, factory, _ = make(ai_url, link=linked())
    say_hello(model)
    assert all(c.api_key != TOKEN for c in factory.clients)
    assert model.is_local


def test_with_no_ai_url_the_token_goes_nowhere():
    model, factory, _ = make('', link=linked(), ollama=f'{SERVER}/v1')
    say_hello(model)
    assert all(c.api_key != TOKEN for c in factory.clients)


@pytest.mark.parametrize('ai_url, server, same', [
    ('https://a.org/v1', 'https://a.org', True), ('https://a.org/v1/', 'https://a.org/', True),
    ('https://A.org:443/v1', 'https://a.org', True), ('http://a.org:80/v1', 'http://a.org', True),
    ('https://a.org/app/v1', 'https://a.org/app', True), ('https://a.org', 'https://a.org', False),
    ('https://a.org:bad/v1', 'https://a.org', False),
])
def test_what_counts_as_the_servers_chat_address(ai_url, server, same):
    assert is_chat_address_of(ai_url, server) is same


def test_linking_later_needs_no_restart_and_linking_again_uses_the_new_token():
    model, factory, store = make(f'{SERVER}/v1')
    assert model.is_local
    say_hello(model)
    assert factory.asked()[-1].api_key != TOKEN
    store.save(CONFIG.cloud.link_key, linked())
    say_hello(model)
    assert factory.asked()[-1].api_key == TOKEN and not model.is_local
    assert model.label == CONFIG.prompts.replies.cloud_ai_label
    store.save(CONFIG.cloud.link_key, linked(token='new-token-456'))
    say_hello(model)
    assert factory.asked()[-1].api_key == 'new-token-456'


def test_a_damaged_link_sends_no_token():
    model, factory, _ = make(f'{SERVER}/v1', link={'server': SERVER, 'token': 'has a space'})
    say_hello(model)
    assert all('space' not in c.api_key for c in factory.clients)


def test_the_cloud_gets_no_ollama_extras():
    model, factory, _ = make(f'{SERVER}/v1', link=linked())
    say_hello(model)
    [request] = factory.asked()[0].requests
    assert 'extra_body' not in request and request['timeout'] == CONFIG.llm.timeout_sec


def test_keep_alive_leaves_the_cloud_alone():
    model, factory, _ = make(f'{SERVER}/v1', link=linked())
    model.ping()
    assert factory.asked() == []


def test_the_token_is_never_logged_or_shown(caplog):
    with caplog.at_level(logging.DEBUG):
        model, _, _ = make(f'{SERVER}/v1', link=linked(), reply=ValueError(f'something odd with {TOKEN}'))
        with pytest.raises(AIUnavailable) as err:
            say_hello(model)
    assert TOKEN not in caplog.text and TOKEN not in err.value.reason and TOKEN not in (model.problem() or '')
    assert err.value.__cause__ is None and err.value.__suppress_context__


def test_the_server_refusing_the_link_says_to_link_again():
    model, _, _ = make(f'{SERVER}/v1', link=linked(), reply=ValueError('Error code: 401 unauthorized'))
    with pytest.raises(AIRefused) as err:
        say_hello(model)
    assert err.value.reason == CONFIG.prompts.replies.ai_errors.cloud_refused


def test_the_servers_limit_counts_as_a_rate_limit():
    model, _, _ = make(f'{SERVER}/v1', link=linked(), reply=ValueError('Error code: 429 too many'))
    with pytest.raises(AIUnavailable) as err:
        say_hello(model)
    assert err.value.rate_limited


def test_a_server_with_no_ai_key_trips_the_breaker():
    clock = FakeClock()
    model, factory, _ = make(f'{SERVER}/v1', link=linked(), reply=ValueError('Error code: 503 no AI here'))
    resilient = ResilientChatModel(model, clock, CONFIG.llm.model_copy(update={'min_interval_sec': 0}),
                                   replies=CONFIG.prompts.replies, time_limit_sec=0.1)
    for _ in range(CONFIG.llm.breaker_failures):
        with pytest.raises(AIUnavailable):
            resilient.chat(HELLO, max_tokens=50, temperature=0.7)
    asked = sum(len(c.requests) for c in factory.clients)
    with pytest.raises(AIUnavailable):
        resilient.chat(HELLO, max_tokens=50, temperature=0.7)
    assert sum(len(c.requests) for c in factory.clients) == asked


# ── a model that thinks before it answers ─────────────────────────────
def test_ollama_can_be_told_not_to_think_first():
    """A thinking model uses up max_tokens on its thoughts and answers with nothing."""
    model, factory, _ = make('http://laptop.test:11434/v1', ollama_reasoning_effort='none')
    say_hello(model)
    assert factory.asked()[0].requests[0]['extra_body']['reasoning_effort'] == 'none'


def test_nothing_is_said_about_thinking_unless_the_setting_asks():
    model, factory, _ = make('http://laptop.test:11434/v1')
    say_hello(model)
    assert 'reasoning_effort' not in factory.asked()[0].requests[0]['extra_body']
