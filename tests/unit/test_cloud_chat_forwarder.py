from __future__ import annotations

import asyncio
import gzip
import json
import logging
import time

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
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
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
    reply = asyncio.run(chat.complete([{'role': 'user', 'content': 'hello'}], 50, 0.4))
    assert reply == GOOD_REPLY
    (request,) = seen
    assert str(request.url) == settings.url
    assert request.headers['authorization'] == 'Bearer sk-test-key'
    assert json.loads(request.content) == {'model': settings.model, 'messages': [{'role': 'user', 'content': 'hello'}],
                                           'max_tokens': 50, 'temperature': 0.4, 'stream': False}


def test_leaves_out_what_the_pi_did_not_send():
    handler, seen = answers()
    asyncio.run(forwarder(handler)[0].complete([{'role': 'user', 'content': 'hello'}], None, None))
    assert set(json.loads(seen[0].content)) == {'model', 'messages', 'stream'}


@pytest.mark.parametrize('status', [400, 401, 429, 500, 503])
def test_an_error_status_is_a_plain_failure_without_the_reply(status, caplog):
    caplog.set_level(logging.DEBUG)
    handler, _ = answers({'error': {'message': f'bad input: {SECRET_TEXT}'}}, status)
    with pytest.raises(ChatFailed) as raised:
        asyncio.run(forwarder(handler)[0].complete([{'role': 'user', 'content': SECRET_TEXT}], None, None))
    assert SECRET_TEXT not in str(raised.value)
    assert SECRET_TEXT not in caplog.text
    assert 'sk-test-key' not in caplog.text
    assert str(status) in caplog.text                       # the status is logged


def test_a_network_error_is_a_plain_failure_without_its_message(caplog):
    caplog.set_level(logging.DEBUG)

    def handler(request):
        raise httpx.ConnectError(f'could not connect while sending {SECRET_TEXT}')
    with pytest.raises(ChatFailed) as raised:
        asyncio.run(forwarder(handler)[0].complete([{'role': 'user', 'content': SECRET_TEXT}], None, None))
    assert SECRET_TEXT not in str(raised.value) and SECRET_TEXT not in caplog.text
    assert raised.value.__cause__ is None and raised.value.__suppress_context__


def test_a_timeout_is_a_plain_failure():
    def handler(request):
        raise httpx.ReadTimeout('too slow')
    with pytest.raises(ChatFailed):
        asyncio.run(forwarder(handler)[0].complete([{'role': 'user', 'content': 'hi'}], None, None))


@pytest.mark.parametrize('reply', [[], 'text', {'choices': []}, {'choices': 'no'}, {'nothing': 1}, None])
def test_a_reply_that_is_not_a_chat_reply_is_a_failure(reply):
    handler, _ = answers(reply)
    with pytest.raises(ChatFailed):
        asyncio.run(forwarder(handler)[0].complete([{'role': 'user', 'content': 'hi'}], None, None))


def test_a_reply_that_is_not_json_is_a_failure():
    with pytest.raises(ChatFailed):
        asyncio.run(forwarder(lambda request: httpx.Response(200, content=b'<html>'))[0].complete(
            [{'role': 'user'}], None, None))


def test_a_redirect_is_not_followed_so_the_key_goes_nowhere_else():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(307, headers={'location': 'https://evil.test/steal'})
    with pytest.raises(ChatFailed):
        asyncio.run(forwarder(handler)[0].complete([{'role': 'user', 'content': 'hi'}], None, None))
    assert len(seen) == 1


def test_logs_only_the_status_and_the_time(caplog):
    caplog.set_level(logging.DEBUG)
    handler, _ = answers()
    asyncio.run(forwarder(handler)[0].complete([{'role': 'user', 'content': SECRET_TEXT}], 7, 0.1))
    assert SECRET_TEXT not in caplog.text and 'sk-test-key' not in caplog.text and 'Hi' not in caplog.text
    assert '200' in caplog.text


# ── one hard limit on a whole chat, whatever the AI company does ───────
TINY_DEADLINE = 0.05                                        # a real wait, kept short so the tests are quick
WELL_PAST_IT = 5                                            # a chat this slow was not stopped by the deadline
EMPTY_GZIP_BLOCK = b'\x00\x00\x00\xff\xff'                  # makes the gzip reader work and gives nothing out


class FakeStream(httpx.AsyncByteStream):
    """A reply that arrives in pieces, `gap` seconds apart. After the last piece it does `then`: raises it, or hangs
    for ever when it is HANG. `forever` hands out its pieces again and again."""

    def __init__(self, pieces: list[bytes], *, gap: float = 0, then: object = None, forever: bool = False):
        self.pieces = pieces
        self.gap = gap
        self.then = then
        self.forever = forever
        self.handed_out = 0
        self.closed = False

    async def __aiter__(self):
        while True:
            for piece in self.pieces:
                await asyncio.sleep(self.gap)
                self.handed_out += 1
                yield piece
            if not self.forever:
                break
        if self.then is HANG:
            await asyncio.Event().wait()
        if isinstance(self.then, Exception):
            raise self.then

    async def aclose(self):
        self.closed = True


HANG = object()


def streaming(stream, *, status=200, headers=None, **chat_changes):
    """A forwarder whose AI company answers with `stream`."""
    settings = cloud_config(chat=chat_changes).chat
    client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(status, stream=stream, headers=headers)))
    return HttpChatForwarder(settings, 'sk-test-key', FakeClock(), client=client)


def in_pieces(reply=GOOD_REPLY, size=5) -> list[bytes]:
    whole = json.dumps(reply).encode()
    return [whole[start:start + size] for start in range(0, len(whole), size)]


def ask(chat, text='hi'):
    return asyncio.run(chat.complete([{'role': 'user', 'content': text}], None, None))


def fails_at_the_deadline(chat, caplog):
    """The chat fails in plain fixed words, soon after the deadline, with no chat text in the logs."""
    caplog.set_level(logging.DEBUG)
    started = time.monotonic()
    with pytest.raises(ChatFailed) as raised:
        ask(chat, SECRET_TEXT)
    assert time.monotonic() - started < WELL_PAST_IT
    assert str(raised.value) == 'The AI company took too long.'
    assert raised.value.__cause__ is None and raised.value.__suppress_context__
    assert SECRET_TEXT not in caplog.text and 'sk-test-key' not in caplog.text


def test_hanging_before_the_headers_fails_at_the_deadline(caplog):
    async def handler(request) -> httpx.Response:
        await asyncio.Event().wait()
        raise AssertionError('never gets here')
    settings = cloud_config(chat={'deadline_sec': TINY_DEADLINE}).chat
    chat = HttpChatForwarder(settings, 'sk-test-key', FakeClock(),
                             client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    fails_at_the_deadline(chat, caplog)


def test_hanging_in_the_middle_of_the_reply_fails_at_the_deadline_and_closes_it(caplog):
    stream = FakeStream([b'{"cho', SECRET_TEXT.encode()], then=HANG)
    fails_at_the_deadline(streaming(stream, deadline_sec=TINY_DEADLINE), caplog)
    assert stream.closed


def test_a_drip_for_ever_fails_at_the_deadline_and_closes_it(caplog):
    stream = FakeStream([SECRET_TEXT.encode()], gap=0.001, forever=True)
    fails_at_the_deadline(streaming(stream, deadline_sec=TINY_DEADLINE), caplog)
    assert stream.closed and stream.handed_out > 1


def test_endless_empty_gzip_blocks_fail_at_the_deadline(caplog):
    # They give nothing out, so a check made per piece would never run.
    stream = FakeStream([gzip.compress(b'')[:10], *[EMPTY_GZIP_BLOCK] * 100_000], gap=0.001)
    fails_at_the_deadline(streaming(stream, headers={'content-encoding': 'gzip'}, deadline_sec=TINY_DEADLINE), caplog)
    assert stream.closed


def test_httpx_has_no_timeout_of_its_own_the_deadline_is_the_one_limit():
    handler, seen = answers()
    ask(forwarder(handler)[0])
    assert set(seen[0].extensions['timeout'].values()) == {None}


def test_a_reply_in_pieces_gives_the_same_body_and_is_closed():
    stream = FakeStream(in_pieces())
    assert ask(streaming(stream)) == GOOD_REPLY
    assert stream.closed


def test_a_reply_bigger_than_the_limit_fails_and_stops_being_read(caplog):
    caplog.set_level(logging.DEBUG)
    stream = FakeStream([SECRET_TEXT.encode().ljust(1024, b'x')] * 100)
    with pytest.raises(ChatFailed) as raised:
        ask(streaming(stream, max_body_kb=3))
    assert stream.handed_out == 4                           # it stopped at the first piece over the 3 kilobytes
    assert stream.closed
    assert SECRET_TEXT not in str(raised.value) and SECRET_TEXT not in caplog.text


def test_a_failure_while_reading_is_plain_and_closes_the_reply(caplog):
    caplog.set_level(logging.DEBUG)
    stream = FakeStream([b'{"cho'], then=httpx.ReadError(f'broke while sending {SECRET_TEXT}'))
    with pytest.raises(ChatFailed) as raised:
        ask(streaming(stream))
    assert stream.closed
    assert SECRET_TEXT not in str(raised.value) and SECRET_TEXT not in caplog.text
    assert raised.value.__cause__ is None and raised.value.__suppress_context__
    assert 'ReadError' in caplog.text                       # only the kind of error


def test_an_error_status_closes_the_reply_without_reading_it():
    stream = FakeStream([SECRET_TEXT.encode()])
    with pytest.raises(ChatFailed):
        ask(streaming(stream, status=500))
    assert stream.closed and stream.handed_out == 0


@pytest.mark.parametrize('pieces', [[b'<ht', b'ml>'], in_pieces({'choices': []})], ids=['not json', 'no answer'])
def test_pieces_that_are_not_json_or_have_no_answer_are_failures(pieces):
    stream = FakeStream(pieces)
    with pytest.raises(ChatFailed):
        ask(streaming(stream))
    assert stream.closed


def test_being_cancelled_closes_the_reply_and_is_not_a_chat_failure():
    stream = FakeStream([b'{"cho'], then=HANG)

    async def run():
        task = asyncio.create_task(streaming(stream).complete([{'role': 'user', 'content': 'hi'}], None, None))
        while not stream.handed_out:
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(run())
    assert stream.closed


def test_asks_for_a_plain_reply():
    handler, seen = answers()
    ask(forwarder(handler)[0])
    assert seen[0].headers['accept-encoding'] == 'identity'


def test_closing_closes_its_client():
    client = httpx.AsyncClient(transport=httpx.MockTransport(answers()[0]))
    chat = HttpChatForwarder(cloud_config().chat, 'sk-x', FakeClock(), client=client)
    asyncio.run(chat.aclose())
    assert client.is_closed
