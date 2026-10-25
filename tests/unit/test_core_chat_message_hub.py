"""MessageHub: sends chat messages to every open app page and keeps the recent ones."""

import logging
import queue

import pytest

from desktalk.core.chat.message_hub import MessageHub
from desktalk.ports import CLOSED, Notifier
from tests.contracts.notifier_contract import NotifierContract
from tests.fakes import FakeClock


def drain(q):
    out = []
    while True:
        try:
            out.append(q.get_nowait())
        except queue.Empty:
            return out


def hub(clock=None, recent_kept=3, queue_size=5):
    return MessageHub(clock or FakeClock(), recent_kept=recent_kept, queue_size=queue_size)


def test_it_is_a_notifier():
    assert isinstance(hub(), Notifier)


def test_a_message_gets_an_id_and_the_time():
    clock = FakeClock(start=1_760_000_123.0)
    sent = hub(clock).send({'type': 'group_message', 'character': 'cup', 'text': 'Hi'})
    assert sent['ts'] == 1_760_000_123.0 and isinstance(sent['id'], str) and sent['id']
    assert sent['type'] == 'group_message' and sent['text'] == 'Hi'


def test_an_id_and_time_already_given_are_kept():
    sent = hub().send({'type': 'group_message', 'character': 'cup', 'text': 'Hi', 'id': 'abc', 'ts': 5.0})
    assert sent['id'] == 'abc' and sent['ts'] == 5.0


def test_two_messages_get_different_ids():
    h = hub()
    assert h.send({'type': 'roster', 'text': 'a'})['id'] != h.send({'type': 'roster', 'text': 'b'})['id']


def test_the_message_passed_in_is_left_alone():
    msg = {'type': 'group_message', 'character': 'cup', 'text': 'Nice [LOG_SIP]'}
    hub().send(msg)
    assert msg == {'type': 'group_message', 'character': 'cup', 'text': 'Nice [LOG_SIP]'}


def test_leftover_action_tags_never_show():
    assert hub().send({'type': 'group_message', 'character': 'computer', 'text': 'Nice [LOG_BREAK]'})['text'] == 'Nice'


def test_a_message_without_a_character_is_sent_as_written():
    assert hub().send({'type': 'roster', 'text': 'Cup joined [LOG_SIP]'})['text'] == 'Cup joined [LOG_SIP]'


def test_every_open_page_gets_every_message():
    h = hub()
    a, b = h.subscribe(), h.subscribe()
    sent = h.send({'type': 'group_message', 'character': 'cup', 'text': 'Hi'})
    assert drain(a) == [sent] and drain(b) == [sent]


def test_a_closed_page_gets_nothing_more():
    h = hub()
    q = h.subscribe()
    h.unsubscribe(q)
    h.unsubscribe(q)                   # twice is fine
    h.send({'type': 'roster', 'text': 'Cup joined the chat'})
    assert drain(q) == []


def test_a_page_that_stopped_reading_is_dropped_and_the_others_carry_on(caplog):
    h = hub(queue_size=2)
    stuck, live = h.subscribe(), h.subscribe()
    with caplog.at_level(logging.INFO):
        for i in range(3):
            h.send({'type': 'habit_update', 'habit': 'hydration', 'n': i})
            drain(live)
        h.send({'type': 'habit_update', 'habit': 'breaks'})
    got = drain(stuck)
    assert [m['n'] for m in got[:-1]] == [0, 1]                     # nothing after its queue filled up...
    assert got[-1] is CLOSED                                         # ...but the marker that ends its stream
    assert drain(live)[0]['habit'] == 'breaks'
    assert caplog.text.count('dropped') == 1


def test_recent_keeps_chat_lines_and_roster_notes_only():
    h = hub(recent_kept=3)
    h.send({'type': 'habit_update', 'habit': 'hydration'})
    h.send({'type': 'roster', 'action': 'joined', 'text': ''})
    texts = [f'line {i}' for i in range(4)]
    for t in texts:
        h.send({'type': 'group_message', 'character': 'cup', 'text': t})
    h.send({'type': 'roster', 'action': 'left', 'text': 'Cup left the chat'})
    assert [m['text'] for m in h.recent()] == ['line 2', 'line 3', 'Cup left the chat']


def test_recent_is_a_copy():
    h = hub()
    h.send({'type': 'roster', 'text': 'Cup joined the chat'})
    h.recent().clear()
    assert len(h.recent()) == 1


class TestMessageHubContract(NotifierContract):
    @pytest.fixture
    def notifier(self, clock):
        return hub(clock)


def test_a_page_can_ask_to_be_woken_for_each_thing_put_in_its_queue():
    h = hub(queue_size=1)
    woken = []
    q = h.subscribe(on_put=lambda: woken.append(1))
    h.send({'type': 'group_message', 'text': 'hi'})
    assert len(woken) == 1
    h.send({'type': 'group_message', 'text': 'too many'})          # full: the hub drops the page with CLOSED
    assert len(woken) == 2 and drain(q)[-1] is CLOSED
    q.put_nowait('stop')                                           # anyone else's put wakes it too
    assert len(woken) == 3
