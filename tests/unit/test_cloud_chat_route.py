from __future__ import annotations

import json
import logging
import threading

import httpx
import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient
from openai import OpenAI

from desktalk_cloud.app import create_app
from desktalk_cloud.chat_forwarder import HttpChatForwarder
from desktalk_cloud.routers import chat_v1
from tests.fakes.clock import FakeClock
from tests.fakes.cloud import FakeChatForwarder, cloud_config, link_device, make_parts

SECRET_TEXT = 'remember-to-feed-the-purple-giraffe'
USER = [{'role': 'user', 'content': 'hello'}]
thread_router = APIRouter()


@thread_router.get('/_test/thread')
async def _thread():
    return {'thread': threading.get_ident()}


def build(parts):
    return TestClient(create_app(parts, [chat_v1.router, thread_router]))


def auth(token):
    return {'authorization': f'Bearer {token}'}


@pytest.fixture
def fake():
    return FakeChatForwarder()


@pytest.fixture
def parts(fake):
    return make_parts(chat_forwarder=fake)


@pytest.fixture
def client(parts):
    return build(parts)


@pytest.fixture
def ann(parts):
    return link_device(parts, 'ann')


def post(client, token, body, **kwargs):
    return client.post('/v1/chat/completions', json=body, headers=auth(token), **kwargs)


def test_answers_in_openai_shape(client, ann, fake):
    # A real openai client, as OpenAICompatibleChatModel uses it, talks to the app through TestClient's transport.
    sdk = OpenAI(api_key=ann.token, base_url='http://testserver/v1', http_client=client, max_retries=0)
    reply = sdk.chat.completions.create(
        model='gpt-whatever', messages=[{'role': 'user', 'content': 'hello'}], max_tokens=40, temperature=0.3,
        extra_body={'num_predict': 40})
    assert reply.choices[0].message.content == 'Hello from the fake'
    assert fake.calls == [(USER, 40, 0.3)]


def test_needs_a_token(client, ann, fake):
    assert client.post('/v1/chat/completions', json={'messages': USER}).status_code == 401
    assert client.post('/v1/chat/completions', json={'messages': USER}, headers=auth('dtk_wrong')).status_code == 401
    assert fake.calls == []


def test_no_key_gets_503():
    parts = make_parts(chat_forwarder=None)
    linked = link_device(parts, 'ann')
    reply = build(parts).post('/v1/chat/completions', json={'messages': USER}, headers=auth(linked.token))
    assert reply.status_code == 503
    assert reply.json()['detail'] == parts.config.messages.chat_no_key


def test_limit_per_pi_gets_429(fake):
    parts = make_parts(cloud_config(chat={'limit_requests': 2}), chat_forwarder=fake)
    client = build(parts)
    ann, bob = link_device(parts, 'ann'), link_device(parts, 'bob')
    assert [post(client, ann.token, {'messages': USER}).status_code for _ in range(3)] == [200, 200, 429]
    assert post(client, ann.token, {'messages': USER}).json()['detail'] == parts.config.messages.chat_too_many
    assert post(client, bob.token, {'messages': USER}).status_code == 200      # another Pi has its own count
    parts.clock.advance(parts.config.chat.limit_window_sec + 1)                    # type: ignore[attr-defined]
    assert post(client, ann.token, {'messages': USER}).status_code == 200


def test_token_and_limit_are_checked_before_the_body_is_read(fake):
    parts = make_parts(cloud_config(chat={'limit_requests': 1}), chat_forwarder=fake)
    client = build(parts)
    ann = link_device(parts, 'ann')
    big = b'x' * (parts.config.chat.max_body_kb * 1024 * 4)
    assert client.post('/v1/chat/completions', content=big).status_code == 401      # not the 413
    assert client.post('/v1/chat/completions', content=big, headers=auth(ann.token)).status_code == 413
    assert client.post('/v1/chat/completions', content=b'not json', headers=auth(ann.token)).status_code == 429


def test_model_from_settings_not_request():
    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={'choices': [{'message': {'role': 'assistant', 'content': 'ok'}}]})
    config = cloud_config()
    chat = HttpChatForwarder(config.chat, 'sk-x', FakeClock(),
                             client=httpx.Client(transport=httpx.MockTransport(handler)))
    parts = make_parts(config, chat_forwarder=chat)
    ann = link_device(parts, 'ann')
    reply = post(build(parts), ann.token, {'model': 'gpt-5-huge', 'messages': USER, 'stream': True,
                                           'extra_body': {'x': 1}, 'tools': [{'a': 1}]})
    assert reply.status_code == 200
    sent = json.loads(seen[0].content)
    assert sent['model'] == config.chat.model and sent['stream'] is False
    assert set(sent) == {'model', 'messages', 'stream'}


def test_only_role_and_content_are_forwarded(client, ann, fake):
    post(client, ann.token, {'messages': [{'role': 'user', 'content': 'hi', 'name': 'x', 'tool_calls': [1]}]})
    assert fake.calls[0][0] == [{'role': 'user', 'content': 'hi'}]


def test_text_parts_are_allowed(client, ann, fake):
    messages = [{'role': 'user', 'content': [{'type': 'text', 'text': 'hello'}, {'type': 'text', 'text': 'there'}]}]
    assert post(client, ann.token, {'messages': messages}).status_code == 200
    assert fake.calls[0][0] == messages


IMAGE = 'data:image/jpeg;base64,AAAA'
REFUSED_IMAGES = {
    'image_url part': [{'role': 'user', 'content': [{'type': 'text', 'text': 'x'},
                                                    {'type': 'image_url', 'image_url': {'url': 'https://a.test/a.png'}}]}],
    'data url in image_url': [{'role': 'user', 'content': [{'type': 'image_url', 'image_url': {'url': IMAGE}}]}],
    'other non-text part': [{'role': 'user', 'content': [{'type': 'input_audio', 'input_audio': {'data': 'x'}}]}],
    'data url in a text': [{'role': 'user', 'content': f'look at {IMAGE}'}],
    'data url in a text part': [{'role': 'user', 'content': [{'type': 'text', 'text': IMAGE}]}],
    'upper case data url': [{'role': 'user', 'content': 'DATA:IMAGE/PNG;base64,AAAA'}],
    'in a later message': [{'role': 'user', 'content': 'hi'}, {'role': 'assistant', 'content': 'yes'},
                           {'role': 'user', 'content': [{'type': 'image_url', 'image_url': {'url': 'x'}}]}],
    'in a system message': [{'role': 'system', 'content': IMAGE}, {'role': 'user', 'content': 'hi'}],
    'in an extra key': [{'role': 'user', 'content': 'hi', 'images': [IMAGE]}],
    'nested deep': [{'role': 'user', 'content': 'hi', 'extra': {'a': [{'b': [{'c': IMAGE}]}]}}],
    'image_url key nested': [{'role': 'user', 'content': 'hi', 'extra': [[{'image_url': 1}]]}],
    'non-text part with no type': [{'role': 'user', 'content': [{'image_url': {'url': 'x'}}]}],
    'in a message that is not a message': ['hi', {'role': 'user', 'content': IMAGE}],
    'content is not text or a list': [{'role': 'user', 'content': {'image_url': {'url': IMAGE}}}],
}


@pytest.mark.parametrize('messages', REFUSED_IMAGES.values(), ids=REFUSED_IMAGES.keys())
def test_any_image_refused(client, ann, fake, parts, messages):
    reply = post(client, ann.token, {'messages': messages})
    assert reply.status_code == 400
    assert reply.json()['detail'] == parts.config.messages.chat_no_images
    assert fake.calls == []
    assert 'AAAA' not in reply.text


def test_an_image_outside_messages_is_refused_too(client, ann, fake):
    assert post(client, ann.token, {'messages': USER, 'extra_body': {'img': IMAGE}}).status_code == 400
    assert fake.calls == []


@pytest.mark.parametrize('depth', [200, 5000, 100_000])
def test_a_very_deep_body_never_gets_a_500(client, ann, fake, depth):
    deep = '[' * depth + ']' * depth
    body = f'{{"messages": [{{"role": "user", "content": "x", "e": {deep}}}]}}'
    reply = client.post('/v1/chat/completions', content=body, headers=auth(ann.token))
    assert reply.status_code in (200, 400)                  # 400 when json can't read it, else the extra key is ignored
    assert all(call[0] == [{'role': 'user', 'content': 'x'}] for call in fake.calls)

    image = client.post('/v1/chat/completions', headers=auth(ann.token), content=(
        '{"messages": [{"role": "user", "content": "x"}], "e": ' + '[' * depth + f'"{IMAGE}"' + ']' * depth + '}'))
    assert image.status_code == 400                         # an image at the bottom is found, or the body is refused


BAD_BODIES = {
    'a list': [], 'a string': 'hello', 'null': None, 'no messages': {}, 'messages null': {'messages': None},
    'messages a string': {'messages': 'hello'}, 'messages a dict': {'messages': {'role': 'user', 'content': 'x'}},
    'messages empty': {'messages': []}, 'message a string': {'messages': ['hello']},
    'message null': {'messages': [None]}, 'no role': {'messages': [{'content': 'x'}]},
    'role a number': {'messages': [{'role': 1, 'content': 'x'}]},
    'role unknown': {'messages': [{'role': 'wizard', 'content': 'x'}]},
    'no content': {'messages': [{'role': 'user'}]}, 'content null': {'messages': [{'role': 'user', 'content': None}]},
    'content a number': {'messages': [{'role': 'user', 'content': 5}]},
    'content empty list': {'messages': [{'role': 'user', 'content': []}]},
    'part a string': {'messages': [{'role': 'user', 'content': ['x']}]},
    'text part without text': {'messages': [{'role': 'user', 'content': [{'type': 'text'}]}]},
    'text part number': {'messages': [{'role': 'user', 'content': [{'type': 'text', 'text': 4}]}]},
    'max_tokens string': {'messages': USER, 'max_tokens': '5'},
    'max_tokens bool': {'messages': USER, 'max_tokens': True},
    'max_tokens float': {'messages': USER, 'max_tokens': 5.5},
    'max_tokens zero': {'messages': USER, 'max_tokens': 0},
    'max_tokens negative': {'messages': USER, 'max_tokens': -3},
    'max_tokens huge': {'messages': USER, 'max_tokens': 10 ** 30},
    'temperature string': {'messages': USER, 'temperature': 'hot'},
    'temperature bool': {'messages': USER, 'temperature': False},
    'temperature negative': {'messages': USER, 'temperature': -1},
    'temperature list': {'messages': USER, 'temperature': [1]},
}


@pytest.mark.parametrize('body', BAD_BODIES.values(), ids=BAD_BODIES.keys())
def test_a_bad_body_gets_a_plain_400(client, ann, fake, parts, body):
    reply = post(client, ann.token, body)
    assert reply.status_code == 400
    assert reply.json()['detail'] in (parts.config.messages.bad_body, parts.config.messages.chat_no_images)
    assert fake.calls == []


ONE = b'{"messages": [{"role": "user", "content": "x"}], '


@pytest.mark.parametrize('raw', [b'', b'not json', b'{"messages": [', b'\xff\xfe', b'{"messages": NaN}',
                                 ONE + b'"temperature": NaN}', ONE + b'"temperature": Infinity}',
                                 ONE + b'"max_tokens": ' + b'9' * 6000 + b'}'])
def test_a_body_that_is_not_usable_json_gets_400(client, ann, fake, raw):
    assert client.post('/v1/chat/completions', content=raw, headers=auth(ann.token)).status_code == 400
    assert fake.calls == []


def test_a_body_over_the_limit_gets_413(client, ann, parts, fake):
    text = 'x' * (parts.config.chat.max_body_kb * 1024)
    assert post(client, ann.token, {'messages': [{'role': 'user', 'content': text}]}).status_code == 413
    assert fake.calls == []


def test_max_tokens_and_temperature_may_be_left_out_or_whole_or_decimal(client, ann, fake):
    post(client, ann.token, {'messages': USER})
    post(client, ann.token, {'messages': USER, 'max_tokens': 20, 'temperature': 1})
    post(client, ann.token, {'messages': USER, 'max_tokens': None, 'temperature': 0.0})
    assert [call[1:] for call in fake.calls] == [(None, None), (20, 1), (None, 0.0)]


def test_upstream_error_gets_a_plain_502(client, ann, fake, parts):
    fake.fail_with = 'The AI company said no'
    reply = post(client, ann.token, {'messages': USER})
    assert reply.status_code == 502
    assert reply.json()['detail'] == parts.config.messages.chat_failed


def test_any_other_failure_in_the_forwarder_is_also_a_plain_502(ann, parts):
    class Broken:
        def complete(self, messages, max_tokens, temperature):
            raise RuntimeError(f'boom {SECRET_TEXT}')
    broken = make_parts(chat_forwarder=Broken())
    linked = link_device(broken, 'ann')
    client = build(broken)
    reply = post(client, linked.token, {'messages': [{'role': 'user', 'content': SECRET_TEXT}]})
    assert reply.status_code == 502
    assert SECRET_TEXT not in reply.text


def _tables_text(parts) -> str:
    return repr(vars(parts.store))


def real_forwarder_parts(handler):
    config = cloud_config()
    client = httpx.Client(transport=httpx.MockTransport(handler))
    chat = HttpChatForwarder(config.chat, 'sk-x', FakeClock(), client=client)
    parts = make_parts(config, chat_forwarder=chat)
    return parts, link_device(parts, 'ann')


def test_chat_text_appears_in_no_log_or_table_on_success(caplog):
    caplog.set_level(logging.DEBUG)
    parts, ann = real_forwarder_parts(lambda request: httpx.Response(200, json={
        'choices': [{'message': {'role': 'assistant', 'content': 'ok'}}]}))
    reply = post(build(parts), ann.token, {'messages': [{'role': 'user', 'content': SECRET_TEXT}]})
    assert reply.status_code == 200
    assert SECRET_TEXT not in caplog.text
    assert SECRET_TEXT not in _tables_text(parts)


@pytest.mark.parametrize('how', ['status', 'network', 'junk', 'crash'])
def test_chat_text_appears_in_no_log_or_table_when_the_ai_company_fails(how, caplog):
    caplog.set_level(logging.DEBUG)

    def handler(request):
        text = json.loads(request.content)['messages'][0]['content']      # the AI company echoes the text back
        if how == 'network':
            raise httpx.ConnectError(f'failed for {text}')
        if how == 'junk':
            return httpx.Response(200, content=text.encode())
        if how == 'crash':
            raise ValueError(f'crash with {text}')
        return httpx.Response(400, json={'error': {'message': f'Bad request: {text}'}})
    parts, ann = real_forwarder_parts(handler)
    reply = post(build(parts), ann.token, {'messages': [{'role': 'user', 'content': SECRET_TEXT}]})
    assert reply.status_code == 502
    assert reply.json()['detail'] == parts.config.messages.chat_failed
    assert SECRET_TEXT not in reply.text
    assert SECRET_TEXT not in caplog.text
    assert SECRET_TEXT not in _tables_text(parts)


def test_a_refused_request_logs_none_of_its_text(client, ann, caplog):
    caplog.set_level(logging.DEBUG)
    post(client, ann.token, {'messages': [{'role': 'user', 'content': f'{SECRET_TEXT} {IMAGE}'}]})
    post(client, ann.token, {'messages': [{'role': 'wizard', 'content': SECRET_TEXT}]})
    assert SECRET_TEXT not in caplog.text


def test_the_forwarder_runs_off_the_event_loop_thread(ann, parts):
    seen: list[int] = []

    class Watching(FakeChatForwarder):
        def complete(self, messages, max_tokens, temperature):
            seen.append(threading.get_ident())
            return super().complete(messages, max_tokens, temperature)
    watching = make_parts(chat_forwarder=Watching(), clock=parts.clock)
    linked = link_device(watching, 'ann')
    client = build(watching)
    loop = client.get('/_test/thread').json()['thread']
    assert post(client, linked.token, {'messages': USER}).status_code == 200
    assert seen and all(thread != loop for thread in seen)
