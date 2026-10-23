"""The AI connection (Gemini / OpenAI / Groq / Ollama through the OpenAI-compatible API), with a fake client."""

import base64
from pathlib import Path

import pytest

from desktalk.adapters.llm.errors import AIRefused
from desktalk.adapters.llm.openai_compatible import OllamaKeepAlive, OpenAICompatibleChatModel
from desktalk.config import Secrets, load_config
from desktalk.domain import AIUnavailable
from tests.conftest import FakeAI
from tests.contracts.chat_model_contract import ChatModelContract
from tests.fakes import FakeClock

HELLO = [{'role': 'user', 'content': 'Say hello.'}]


@pytest.fixture(scope='module')
def base_llm():
    return load_config(environ={}, dotenv=Path('/nonexistent')).llm


@pytest.fixture
def llm(base_llm):
    """The llm settings with the numbers these tests check written out by hand."""
    replies = base_llm.replies.model_copy(update={
        'vision': base_llm.replies.vision.model_copy(update={'max_tokens': 400, 'temperature': 0.2})})
    return base_llm.model_copy(update={
        'gemini_model': 'gemini-test', 'openai_model': 'gpt-test', 'groq_model': 'groq-test',
        'ollama_model': 'llama-test', 'vision_model': 'llava-test', 'max_retries': 3, 'timeout_sec': 30,
        'ollama_timeout_sec': 90, 'ollama_retry_sec': 2, 'ollama_warmer_per_retry': 0.15,
        'gemini_thinking_budget': 0, 'ollama_keepalive_sec': 240, 'replies': replies,
        'gemini_url': 'https://gemini.test/openai/', 'groq_url': 'https://groq.test/openai/v1'})


def secrets(gemini='', openai='', groq='', ollama=''):
    return Secrets(gemini_api_key=gemini, openai_api_key=openai, groq_api_key=groq, ollama_url=ollama)


class Factory:
    """Makes FakeAI clients (stand-ins for openai.OpenAI) and keeps them, so tests can see the requests."""

    def __init__(self, reply='Hi there, nice desk you have!'):
        self.reply = reply
        self.clients = []

    def __call__(self, **kw):
        client = FakeAI(reply=self.reply, **kw)
        self.clients.append(client)
        return client

    def by_url(self, url):
        return next(c for c in self.clients if c.base_url == url)


def make(llm, reply='Hi there, nice desk you have!', clock=None, **keys):
    factory = Factory(reply)
    model = OpenAICompatibleChatModel(llm, secrets(**keys), clock or FakeClock(), client_factory=factory)
    return model, factory


# ── which AI answers ──────────────────────────────────────────────────
@pytest.mark.parametrize('keys, label, url', [
    ({'gemini': 'g', 'openai': 'o', 'groq': 'q', 'ollama': 'http://ollama.test/v1'}, 'Gemini gemini-test',
     'https://gemini.test/openai/'),
    ({'openai': 'o', 'groq': 'q', 'ollama': 'http://ollama.test/v1'}, 'OpenAI gpt-test', None),
    ({'groq': 'q', 'ollama': 'http://ollama.test/v1'}, 'Groq groq-test', 'https://groq.test/openai/v1'),
    ({'ollama': 'http://ollama.test/v1'}, 'Ollama llama-test', 'http://ollama.test/v1'),
])
def test_the_first_ai_with_a_key_answers(llm, keys, label, url):
    model, factory = make(llm, **keys)
    assert model.ready and model.label == label
    model.chat(HELLO, max_tokens=50, temperature=0.7)
    asked = [c for c in factory.clients if c.requests]
    assert len(asked) == 1 and asked[0].base_url == url
    assert asked[0].requests[0]['model'] == label.split(' ', 1)[1]


def test_the_key_from_env_goes_to_the_client(llm):
    _, factory = make(llm, gemini='secret-gemini-key')
    assert factory.by_url('https://gemini.test/openai/').api_key == 'secret-gemini-key'


def test_no_key_means_no_ai_and_a_plain_reason(llm):
    model, factory = make(llm)
    assert not model.ready and model.label == 'No AI (add a key to .env)'
    with pytest.raises(AIUnavailable) as err:
        model.chat(HELLO, max_tokens=50, temperature=0.7)
    assert '.env' in err.value.reason and factory.clients == []


def test_without_the_openai_package_there_is_no_ai(llm, monkeypatch):
    import builtins
    real_import = builtins.__import__

    def no_openai(name, *args, **kw):
        if name == 'openai':
            raise ImportError('No module named openai')
        return real_import(name, *args, **kw)
    monkeypatch.setattr(builtins, '__import__', no_openai)
    model = OpenAICompatibleChatModel(llm, secrets(gemini='g'), FakeClock())
    assert not model.ready


# ── the request each AI gets ─────────────────────────────────────────
def test_a_cloud_request_has_the_settings_and_a_time_limit(llm):
    model, factory = make(llm, openai='o')
    model.chat(HELLO, max_tokens=120, temperature=0.55)
    req = factory.clients[0].requests[0]
    assert req['model'] == 'gpt-test' and req['messages'] == HELLO
    assert req['max_tokens'] == 120 and req['temperature'] == 0.55 and req['timeout'] == 30
    assert 'extra_body' not in req


def test_gemini_is_told_not_to_think_first(llm):
    model, factory = make(llm.model_copy(update={'gemini_thinking_budget': 0}), gemini='g')
    model.chat(HELLO, max_tokens=50, temperature=0.7)
    req = factory.by_url('https://gemini.test/openai/').requests[0]
    assert req['extra_body'] == {'extra_body': {'google': {'thinking_config': {'thinking_budget': 0}}}}


def test_ollama_gets_its_own_time_limit_and_reply_length(llm):
    model, factory = make(llm, ollama='http://ollama.test/v1')
    model.chat(HELLO, max_tokens=80, temperature=0.5)
    req = [c for c in factory.clients if c.requests][0].requests[0]
    assert req['timeout'] == 90 and req['extra_body'] == {'num_predict': 80}
    assert model.is_local and model.time_limit_sec == 90


def test_a_cloud_ai_is_not_local(llm):
    model, _ = make(llm, gemini='g', ollama='http://ollama.test/v1')
    assert not model.is_local and model.time_limit_sec == 30


def test_the_reply_loses_quotes_and_spaces(llm):
    model, _ = make(llm, reply='  "Hello from the cup!"  ', gemini='g')
    assert model.chat(HELLO, max_tokens=50, temperature=0.7) == 'Hello from the cup!'


# ── problems ─────────────────────────────────────────────────────────
def test_an_error_becomes_ai_unavailable_in_plain_words(llm):
    model, _ = make(llm, reply=ConnectionError('Connection refused'), gemini='g')
    with pytest.raises(AIUnavailable) as err:
        model.chat(HELLO, max_tokens=50, temperature=0.7)
    assert 'Check the internet connection' in err.value.reason
    assert model.problem() == err.value.reason


def test_a_rate_limit_says_so(llm):
    model, _ = make(llm, reply=RuntimeError('Error code: 429'), gemini='g')
    with pytest.raises(AIUnavailable) as err:
        model.chat(HELLO, max_tokens=50, temperature=0.7)
    assert err.value.rate_limited


def test_a_working_call_clears_the_problem(llm):
    answers = iter([ConnectionError('Connection refused'), 'Back again!'])
    model, _ = make(llm, reply=lambda kw: next(answers), gemini='g')
    with pytest.raises(AIUnavailable):
        model.chat(HELLO, max_tokens=50, temperature=0.7)
    assert model.chat(HELLO, max_tokens=50, temperature=0.7) == 'Back again!'
    assert model.problem() is None


def test_a_cloud_empty_reply_is_unavailable_without_asking_again(llm):
    model, factory = make(llm, reply='', gemini='g')
    with pytest.raises(AIUnavailable) as err:
        model.chat(HELLO, max_tokens=50, temperature=0.7)
    assert 'empty reply' in err.value.reason
    assert len(factory.by_url('https://gemini.test/openai/').requests) == 1


def test_ollama_asks_again_warmer_after_an_empty_reply(llm):
    answers = iter(['', '', 'Finally, a sentence.'])
    clock = FakeClock()
    model, factory = make(llm, reply=lambda kw: next(answers), clock=clock, ollama='http://ollama.test/v1')
    messages = [{'role': 'system', 'content': 'Be a cup.'}, {'role': 'user', 'content': 'Hi'}]
    assert model.chat(messages, max_tokens=80, temperature=0.5) == 'Finally, a sentence.'
    reqs = [c for c in factory.clients if c.requests][0].requests
    assert [r['temperature'] for r in reqs] == pytest.approx([0.5, 0.65, 0.8])
    assert reqs[0]['messages'][-1]['content'] == 'Hi'
    assert reqs[1]['messages'][-1]['content'] == 'Hi\n(Please respond with at least one sentence.)'
    assert reqs[2]['messages'][-1]['content'] == 'Hi\n(Please respond with at least one sentence.)'
    assert clock.slept == [2, 2]
    assert messages[-1]['content'] == 'Hi'              # the caller's messages are left alone


def test_ollama_gives_up_after_the_tries_run_out(llm):
    model, factory = make(llm, reply='', ollama='http://ollama.test/v1')
    with pytest.raises(AIUnavailable):
        model.chat(HELLO, max_tokens=80, temperature=0.5)
    assert len([c for c in factory.clients if c.requests][0].requests) == 3


# ── pictures ─────────────────────────────────────────────────────────
def test_vision_uses_ollama_when_it_is_there(llm):
    model, factory = make(llm, reply='A cup and a laptop.', gemini='g', ollama='http://ollama.test/v1')
    assert model.local_vision
    assert model.vision(b'\xff\xd8jpeg', 'What is on the desk?') == 'A cup and a laptop.'
    req = factory.by_url('http://ollama.test/v1').requests[0]
    assert req['model'] == 'llava-test' and req['max_tokens'] == 400 and req['temperature'] == 0.2
    assert req['timeout'] == 90
    text, picture = req['messages'][0]['content']
    assert text == {'type': 'text', 'text': 'What is on the desk?'}
    expected_url = 'data:image/jpeg;base64,' + base64.b64encode(b'\xff\xd8jpeg').decode()
    assert picture == {'type': 'image_url', 'image_url': {'url': expected_url}}
    assert factory.by_url('https://gemini.test/openai/').requests == []


def test_vision_without_ollama_uses_the_chat_model(llm):
    model, factory = make(llm, reply='A cup.', gemini='g')
    assert not model.local_vision
    model.vision(b'\xff\xd8jpeg', 'What is on the desk?')
    req = factory.by_url('https://gemini.test/openai/').requests[0]
    assert req['model'] == 'gemini-test' and req['timeout'] == 30


def test_a_text_only_model_is_refused_for_pictures_from_then_on(llm):
    model, factory = make(llm, reply=RuntimeError('model llava-test does not support multimodal input'),
                          ollama='http://ollama.test/v1')
    with pytest.raises(AIRefused) as err:
        model.vision(b'\xff\xd8jpeg', 'What is on the desk?')
    assert 'llm.vision_model' in err.value.reason
    asked = sum(len(c.requests) for c in factory.clients)
    with pytest.raises(AIRefused):
        model.vision(b'\xff\xd8jpeg', 'What is on the desk?')
    assert sum(len(c.requests) for c in factory.clients) == asked


def test_a_rate_limit_on_a_picture_question_is_a_problem(llm):
    model, _ = make(llm, reply=RuntimeError('Error code: 429'), gemini='g')
    with pytest.raises(AIUnavailable) as err:
        model.vision(b'\xff\xd8jpeg', 'What is on the desk?')
    assert err.value.rate_limited and model.problem() == err.value.reason


def test_any_other_picture_failure_leaves_the_chat_problem_alone(llm):
    model, _ = make(llm, reply=ConnectionError('Connection refused'), ollama='http://ollama.test/v1')
    with pytest.raises(AIUnavailable):
        model.vision(b'\xff\xd8jpeg', 'What is on the desk?')
    assert model.problem() is None


def test_no_ai_means_no_pictures_either(llm):
    model, _ = make(llm)
    with pytest.raises(AIUnavailable):
        model.vision(b'\xff\xd8jpeg', 'What is on the desk?')


# ── keeping Ollama's model loaded ────────────────────────────────────
def test_only_ollama_needs_keeping_warm(llm):
    assert make(llm, gemini='g', ollama='http://ollama.test/v1')[0].keepalive_worker() is None
    assert isinstance(make(llm, ollama='http://ollama.test/v1')[0].keepalive_worker(), OllamaKeepAlive)


def test_the_keepalive_asks_something_tiny_and_waits_its_turn(llm):
    model, factory = make(llm, ollama='http://ollama.test/v1')
    worker = model.keepalive_worker()
    assert worker.name and worker.tick(FakeClock().now()) == 240
    req = factory.clients[-1].requests[0]
    assert req['model'] == 'llama-test' and req['max_tokens'] == 1


def test_a_failed_keepalive_is_not_a_problem(llm):
    model, _ = make(llm, reply=ConnectionError('Connection refused'), ollama='http://ollama.test/v1')
    assert model.keepalive_worker().tick(FakeClock().now()) == 240
    assert model.problem() is None


# ── the ChatModel contract ───────────────────────────────────────────
class TestOpenAICompatibleChatModel(ChatModelContract):
    @pytest.fixture
    def model(self, llm):
        return make(llm, reply='Hello there!', gemini='g')[0]

    @pytest.fixture
    def broken_model(self, llm):
        return make(llm, reply=ConnectionError('Connection refused'), gemini='g')[0]
