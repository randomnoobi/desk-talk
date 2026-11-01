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


# ── the reply is read a piece at a time, so one chat can't run on past its deadline ─────────────
class FakeStream(httpx.SyncByteStream):
    """A reply that arrives in pieces. Each piece moves the clock by `each_takes`; `then_raise` is raised at the end."""

    def __init__(self, pieces: list[bytes], clock: FakeClock, each_takes: float = 0,
                 then_raise: Exception | None = None):
        self.pieces = pieces
        self.clock = clock
        self.each_takes = each_takes
        self.then_raise = then_raise
        self.handed_out = 0
        self.closed = False

    def __iter__(self):
        for piece in self.pieces:
            self.clock.advance(self.each_takes)
            self.handed_out += 1
            yield piece
        if self.then_raise is not None:
            raise self.then_raise

    def close(self):
        self.closed = True


def streaming(make_stream, *, status=200, **chat_changes):
    """A forwarder whose AI company answers with the stream `make_stream(clock)` makes. Also gives back the stream."""
    settings = cloud_config(chat=chat_changes).chat
    clock = FakeClock()
    stream = make_stream(clock)
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(status, stream=stream)))
    return HttpChatForwarder(settings, 'sk-test-key', clock, client=client), stream, settings


def in_pieces(reply=GOOD_REPLY, size=5) -> list[bytes]:
    whole = json.dumps(reply).encode()
    return [whole[start:start + size] for start in range(0, len(whole), size)]


def ask(chat, text='hi'):
    return chat.complete([{'role': 'user', 'content': text}], None, None)


def test_a_reply_in_pieces_gives_the_same_body_and_is_closed():
    chat, stream, _ = streaming(lambda clock: FakeStream(in_pieces(), clock, each_takes=0.01))
    assert ask(chat) == GOOD_REPLY
    assert stream.closed


def test_a_slow_drip_fails_past_the_deadline_with_no_text_in_the_logs(caplog):
    caplog.set_level(logging.DEBUG)
    pieces = [SECRET_TEXT.encode()] * 1000
    chat, stream, settings = streaming(lambda clock: FakeStream(pieces, clock, each_takes=1), deadline_sec=10)
    with pytest.raises(ChatFailed) as raised:
        ask(chat, SECRET_TEXT)
    assert stream.handed_out == settings.deadline_sec + 1           # it stopped at the first piece past the deadline
    assert stream.closed
    assert SECRET_TEXT not in str(raised.value) and SECRET_TEXT not in caplog.text
    assert 'sk-test-key' not in caplog.text


def test_a_reply_that_ends_exactly_at_the_deadline_is_fine():
    chat, _, _ = streaming(lambda clock: FakeStream([json.dumps(GOOD_REPLY).encode()], clock, each_takes=10),
                           deadline_sec=10)
    assert ask(chat) == GOOD_REPLY


def test_a_last_piece_past_the_deadline_still_fails():
    chat, stream, _ = streaming(lambda clock: FakeStream([json.dumps(GOOD_REPLY).encode()], clock, each_takes=11),
                                deadline_sec=10)
    with pytest.raises(ChatFailed):
        ask(chat)
    assert stream.closed


def test_a_reply_bigger_than_the_limit_fails_and_stops_being_read(caplog):
    caplog.set_level(logging.DEBUG)
    piece = SECRET_TEXT.encode().ljust(1024, b'x')
    chat, stream, settings = streaming(lambda clock: FakeStream([piece] * 100, clock), max_body_kb=3)
    with pytest.raises(ChatFailed) as raised:
        ask(chat)
    assert stream.handed_out == settings.max_body_kb + 1
    assert stream.closed
    assert SECRET_TEXT not in str(raised.value) and SECRET_TEXT not in caplog.text


def test_a_failure_while_reading_is_plain_and_closes_the_reply(caplog):
    caplog.set_level(logging.DEBUG)
    chat, stream, _ = streaming(lambda clock: FakeStream(
        [b'{"cho'], clock, then_raise=httpx.ReadTimeout(f'too slow while sending {SECRET_TEXT}')))
    with pytest.raises(ChatFailed) as raised:
        ask(chat)
    assert stream.closed
    assert SECRET_TEXT not in str(raised.value) and SECRET_TEXT not in caplog.text
    assert raised.value.__cause__ is None and raised.value.__suppress_context__
    assert 'ReadTimeout' in caplog.text                     # only the kind of error


def test_an_error_status_closes_the_reply_without_reading_it():
    chat, stream, _ = streaming(lambda clock: FakeStream([SECRET_TEXT.encode()], clock), status=500)
    with pytest.raises(ChatFailed):
        ask(chat)
    assert stream.closed and stream.handed_out == 0


def test_pieces_that_are_not_json_or_have_no_answer_are_failures():
    for pieces in ([b'<ht', b'ml>'], in_pieces({'choices': []})):
        chat, stream, _ = streaming(lambda clock, chunks=pieces: FakeStream(chunks, clock))
        with pytest.raises(ChatFailed):
            ask(chat)
        assert stream.closed


def test_asks_for_a_plain_reply_so_each_piece_is_seen_as_it_arrives():
    # A squeezed (gzip) reply can arrive for a long time before any of it can be unsqueezed, and the deadline is only
    # looked at when a piece comes out.
    handler, seen = answers()
    forwarder(handler)[0].complete([{'role': 'user', 'content': 'hi'}], None, None)
    assert seen[0].headers['accept-encoding'] == 'identity'
