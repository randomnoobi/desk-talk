"""The chat messages stream (SSE) of one open page: desktalk/web/streams.py with a real MessageHub."""

from __future__ import annotations

import json
import time

import anyio

from desktalk.core.chat.message_hub import MessageHub
from desktalk.web.streams import LiveStreams
from tests.fakes import FakeClock

RETRY_MS = 2000
WAIT_SEC = 3          # a stream that should end has ended by then


async def read_all(stream) -> list[str]:
    """Everything the stream sends until it ends (fails after WAIT_SEC instead of waiting forever)."""
    with anyio.fail_after(WAIT_SEC):
        return [part async for part in stream]


def test_a_page_that_falls_behind_gets_what_it_had_then_its_stream_ends():
    hub = MessageHub(FakeClock(), recent_kept=10, queue_size=2)
    stream = LiveStreams().chat_messages(hub, retry_ms=RETRY_MS, keepalive_sec=5)

    async def main() -> list[str]:
        assert await anext(stream) == f'retry: {RETRY_MS}\n\n'        # the page is subscribed now
        for n in range(3):                                            # the third doesn't fit: the hub drops the page
            hub.send({'type': 'group_message', 'character': 'cup', 'text': f'line {n}'})
        return await read_all(stream)

    parts = anyio.run(main)
    assert [json.loads(p.removeprefix('data: '))['text'] for p in parts] == ['line 0', 'line 1']


def test_a_quiet_stream_says_it_is_still_there():
    hub = MessageHub(FakeClock(), recent_kept=10, queue_size=5)
    stream = LiveStreams().chat_messages(hub, retry_ms=RETRY_MS, keepalive_sec=0.01)

    async def main() -> list[str]:
        out = [await anext(stream), await anext(stream)]
        await stream.aclose()
        return out

    assert anyio.run(main)[1] == ': keepalive\n\n'


def test_close_all_ends_an_open_stream_at_once():
    hub = MessageHub(FakeClock(), recent_kept=10, queue_size=5)
    streams = LiveStreams()
    stream = streams.chat_messages(hub, retry_ms=RETRY_MS, keepalive_sec=30)

    async def main() -> tuple[list[str], float]:
        await anext(stream)
        started = time.monotonic()
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(anyio.to_thread.run_sync, lambda: (time.sleep(0.05), streams.close_all()))
            rest = await read_all(stream)
        return rest, time.monotonic() - started

    rest, took = anyio.run(main)
    assert rest == [] and took < 1                      # not the 30 s wait for the next keepalive
