"""Asking the AI from a background job: no AI or an AI that can't answer means "say nothing", never a crash."""

from __future__ import annotations

from desktalk.config import AiReply
from desktalk.core.chat.workers.ai import ask, look
from tests.fakes import FakeChatModel

MESSAGES = [{'role': 'user', 'content': 'Topic:'}]


def test_ask_sends_the_reply_size_and_temperature_from_the_settings():
    ai = FakeChatModel('Mugs of the world')
    text = ask(ai, MESSAGES, AiReply(max_tokens=200, temperature=0.9), own_temperature=0.5, background=True,
               what='a topic')
    assert text == 'Mugs of the world'
    call = ai.calls[0]
    assert (call['max_tokens'], call['temperature'], call['background']) == (200, 0.9, True)
    assert call['messages'] == MESSAGES


def test_ask_uses_the_speakers_own_temperature_when_the_setting_has_none():
    ai = FakeChatModel('Hello')
    ask(ai, MESSAGES, AiReply(max_tokens=200), own_temperature=0.6, background=False, what='a line')
    assert ai.calls[0]['temperature'] == 0.6


def test_ask_keeps_a_temperature_of_zero():
    ai = FakeChatModel('Hello')
    ask(ai, MESSAGES, AiReply(max_tokens=200, temperature=0.0), own_temperature=0.6, background=False, what='a line')
    assert ai.calls[0]['temperature'] == 0.0


def test_ask_gives_none_when_the_ai_cannot_answer():
    ai = FakeChatModel(fail='Gemini is out of quota for today')
    assert ask(ai, MESSAGES, AiReply(max_tokens=60), own_temperature=0.7, background=True, what='x') is None


def test_ask_does_not_call_an_ai_that_is_not_set_up():
    ai = FakeChatModel('Hello', ready=False)
    assert ask(ai, MESSAGES, AiReply(max_tokens=60), own_temperature=0.7, background=True, what='x') is None
    assert ai.calls == []


def test_look_asks_about_the_picture():
    ai = FakeChatModel('{"visible": ["keys"]}')
    assert look(ai, b'jpeg', 'What is on the desk?', background=True, what='a scan') == '{"visible": ["keys"]}'
    assert ai.calls[0]['kind'] == 'vision' and ai.calls[0]['jpeg'] == b'jpeg' and ai.calls[0]['background']


def test_look_gives_none_with_no_vision_ai_no_picture_or_a_failure():
    assert look(None, b'jpeg', 'prompt', background=True, what='x') is None
    unused = FakeChatModel('hi')
    assert look(unused, b'', 'prompt', background=True, what='x') is None
    assert unused.calls == []
    assert look(FakeChatModel(fail='Ollama is not running'), b'jpeg', 'prompt', background=True, what='x') is None
