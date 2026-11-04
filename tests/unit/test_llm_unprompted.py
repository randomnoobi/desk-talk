"""The AI as everything but the chat you asked for sees it: not there at all while the address is the cloud server."""

from __future__ import annotations

from pathlib import Path

import pytest

from desktalk.adapters.llm.unprompted import UnpromptedChatModel
from desktalk.config import load_config
from desktalk.domain import AIUnavailable
from tests.contracts.chat_model_contract import ChatModelContract
from tests.fakes import FakeChatModel

CONFIG = load_config(environ={}, dotenv=Path('/nonexistent'))
HELLO = [{'role': 'user', 'content': 'Hello'}]
REASON = CONFIG.prompts.replies.ai_errors.cloud_chat_only


def guarded(inner, *, cloud=False):
    return UnpromptedChatModel(inner, goes_to_cloud=lambda: cloud, reason=REASON)


class TestUnpromptedChatModel(ChatModelContract):
    @pytest.fixture
    def model(self):
        return guarded(FakeChatModel(reply='Hi'))

    @pytest.fixture
    def broken_model(self):
        return guarded(FakeChatModel(fail='The AI is off.'))


def test_with_the_cloud_it_calls_nothing_and_isnt_ready():
    inner = FakeChatModel()
    model = guarded(inner, cloud=True)
    assert not model.ready
    with pytest.raises(AIUnavailable, match=REASON):
        model.chat(HELLO, max_tokens=5, temperature=0.5, background=True)
    with pytest.raises(AIUnavailable, match=REASON):
        model.vision(b'x', 'What is this?')
    assert inner.calls == []


def test_the_answer_is_worked_out_again_each_time():
    inner, cloud = FakeChatModel(), [False]
    model = UnpromptedChatModel(inner, goes_to_cloud=lambda: cloud[0], reason=REASON)
    assert model.ready
    cloud[0] = True
    assert not model.ready


def test_it_passes_on_what_it_doesnt_guard():
    inner = FakeChatModel(is_local=True, local_vision=True)
    model = guarded(inner)
    assert model.label == inner.label and model.is_local and model.local_vision and model.problem() is None
