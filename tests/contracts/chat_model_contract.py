"""What every ChatModel must do (desktalk.ports.ChatModel)."""

from __future__ import annotations

import pytest

from desktalk.domain import AIUnavailable

HELLO = [{'role': 'user', 'content': 'Say hello in one sentence.'}]


class ChatModelContract:
    """Subclass and give fixtures `model` (answers) and `broken_model` (its AI fails every call)."""

    def test_label_and_ready(self, model):
        assert isinstance(model.label, str) and model.label
        assert model.ready

    def test_chat_returns_text_and_clears_the_problem(self, model):
        reply = model.chat(HELLO, max_tokens=50, temperature=0.7)
        assert isinstance(reply, str) and reply
        assert model.problem() is None

    def test_failure_raises_ai_unavailable_and_sets_a_problem(self, broken_model):
        with pytest.raises(AIUnavailable) as err:
            broken_model.chat(HELLO, max_tokens=50, temperature=0.7)
        assert err.value.reason
        assert broken_model.problem()

    def test_vision_returns_text(self, model):
        assert isinstance(model.vision(b'\xff\xd8 fake jpeg', 'What is on the desk?'), str)
