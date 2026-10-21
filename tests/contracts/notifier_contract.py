"""What every Notifier must do (desktalk/ports.py). The real MessageHub and the fake RecordingNotifier both
run these tests (see tests/unit/test_core_chat_message_hub.py and test_fakes_pass_contracts.py)."""

from __future__ import annotations

import json

import pytest

from desktalk.ports import Notifier
from tests.fakes import FakeClock

NOW = 1_760_000_123.0


class NotifierContract:
    """Subclass this and give a `notifier` fixture, made with the `clock` fixture."""

    @pytest.fixture
    def clock(self):
        return FakeClock(start=NOW)

    @pytest.fixture
    def notifier(self, clock):
        raise NotImplementedError

    def test_it_is_a_notifier(self, notifier):
        assert isinstance(notifier, Notifier)

    def test_send_fills_in_an_id_and_the_time(self, notifier):
        sent = notifier.send({'type': 'group_message', 'character': 'cup', 'text': 'Hi'})
        assert isinstance(sent['id'], str) and sent['id']
        assert sent['ts'] == NOW

    def test_send_keeps_everything_it_was_given(self, notifier):
        message = {'type': 'roster', 'action': 'joined', 'text': 'Cup joined the chat',
                   'agent': {'id': 'cup', 'name': 'Cup'}, 'toast': True}
        sent = notifier.send(message)
        assert {key: sent[key] for key in message} == message
        assert set(sent) == {*message, 'id', 'ts'}

    def test_an_id_and_a_time_already_given_are_kept(self, notifier):
        sent = notifier.send({'type': 'group_message', 'character': 'cup', 'text': 'Hi', 'id': 'abc', 'ts': 5.0})
        assert (sent['id'], sent['ts']) == ('abc', 5.0)

    def test_each_message_gets_its_own_id(self, notifier):
        ids = {notifier.send({'type': 'group_message', 'character': 'cup', 'text': 'Hi'})['id'] for _ in range(20)}
        assert len(ids) == 20

    def test_the_message_given_is_left_as_it_was(self, notifier):
        message = {'type': 'group_message', 'character': 'cup', 'text': 'Hi'}
        notifier.send(message)
        assert message == {'type': 'group_message', 'character': 'cup', 'text': 'Hi'}

    def test_the_message_sent_can_go_to_the_page_as_json(self, notifier):
        sent = notifier.send({'type': 'group_message', 'character': 'cup', 'text': 'Hi'})
        assert json.loads(json.dumps(sent)) == sent
