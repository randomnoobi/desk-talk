from __future__ import annotations

import json
import logging

import httpx
import pytest

from desktalk_cloud.chat_forwarder import HttpChatForwarder
from desktalk_cloud.ports import ChatFailed, ChatForwarder
from tests.fakes.clock import FakeClock
from tests.fakes.cloud import cloud_config

SECRET_TEXT = 'buy-purple-giraffe-food-at-5pm'
GOOD_REPLY = {'id': 'x', 'object': 'chat.completion', 'choices': [
    {'index': 0, 'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': 'Hi'}}]}


def forwarder(handler, *, key: str = 'sk-test-key', **chat_changes):
    settings = cloud_config(chat=chat_changes).chat
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return HttpChatForwarder(settings, key, FakeClock(), client=client), settings


def answers(reply=GOOD_REPLY, status=200):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=reply)
    return handler, seen


def test_is_a_chat_forwarder():
    handler, _ = answers()
    assert isinstance(forwarder(handler)[0], ChatForwarder)


def test_sends_settings_model_key_and_no_streaming():
    handler, seen = answers()
    chat, settings = forwarder(handler)
    reply = chat.complete([{'role': 'user', 'content': 'hello'}], 50, 0.4)
    assert reply == GOOD_REPLY
    (request,) = seen
    assert str(request.url) == settings.url
    assert request.headers['authorization'] == 'Bearer sk-test-key'
    assert json.loads(request.content) == {'model': settings.model, 'messages': [{'role': 'user', 'content': 'hello'}],
                                           'max_tokens': 50, 'temperature': 0.4, 'stream': False}


def test_leaves_out_what_the_pi_did_not_send():
    handler, seen = answers()
    forwarder(handler)[0].complete([{'role': 'user', 'content': 'hello'}], None, None)
    assert set(json.loads(seen[0].content)) == {'model', 'messages', 'stream'}


@pytest.mark.parametrize('status', [400, 401, 429, 500, 503])
def test_an_error_status_is_a_plain_failure_without_the_reply(status, caplog):
    caplog.set_level(logging.DEBUG)
    handler, _ = answers({'error': {'message': f'bad input: {SECRET_TEXT}'}}, status)
    with pytest.raises(ChatFailed) as raised:
        forwarder(handler)[0].complete([{'role': 'user', 'content': SECRET_TEXT}], None, None)
    assert SECRET_TEXT not in str(raised.value)
    assert SECRET_TEXT not in caplog.text
    assert 'sk-test-key' not in caplog.text
    assert str(status) in caplog.text                       # the status is logged


def test_a_network_error_is_a_plain_failure_without_its_message(caplog):
    caplog.set_level(logging.DEBUG)

    def handler(request):
        raise httpx.ConnectError(f'could not connect while sending {SECRET_TEXT}')
    with pytest.raises(ChatFailed) as raised:
        forwarder(handler)[0].complete([{'role': 'user', 'content': SECRET_TEXT}], None, None)
    assert SECRET_TEXT not in str(raised.value) and SECRET_TEXT not in caplog.text
    assert raised.value.__cause__ is None and raised.value.__suppress_context__


def test_a_timeout_is_a_plain_failure():
    def handler(request):
        raise httpx.ReadTimeout('too slow')
    with pytest.raises(ChatFailed):
        forwarder(handler)[0].complete([{'role': 'user', 'content': 'hi'}], None, None)


@pytest.mark.parametrize('reply', [[], 'text', {'choices': []}, {'choices': 'no'}, {'nothing': 1}, None])
def test_a_reply_that_is_not_a_chat_reply_is_a_failure(reply):
    handler, _ = answers(reply)
    with pytest.raises(ChatFailed):
        forwarder(handler)[0].complete([{'role': 'user', 'content': 'hi'}], None, None)


def test_a_reply_that_is_not_json_is_a_failure():
    with pytest.raises(ChatFailed):
        forwarder(lambda request: httpx.Response(200, content=b'<html>'))[0].complete([{'role': 'user'}], None, None)


def test_a_redirect_is_not_followed_so_the_key_goes_nowhere_else():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(307, headers={'location': 'https://evil.test/steal'})
    with pytest.raises(ChatFailed):
        forwarder(handler)[0].complete([{'role': 'user', 'content': 'hi'}], None, None)
    assert len(seen) == 1


def test_logs_only_the_status_and_the_time(caplog):
    caplog.set_level(logging.DEBUG)
    handler, _ = answers()
    forwarder(handler)[0].complete([{'role': 'user', 'content': SECRET_TEXT}], 7, 0.1)
    assert SECRET_TEXT not in caplog.text and 'sk-test-key' not in caplog.text and 'Hi' not in caplog.text
    assert '200' in caplog.text
