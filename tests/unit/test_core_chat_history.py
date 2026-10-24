"""The chat history the app sends: cleaned up, and the older part squeezed into a recap."""

from pathlib import Path

import pytest

from desktalk.config import load_config
from desktalk.core.chat import history as hx


@pytest.fixture(scope='module')
def chat():
    return load_config(environ={}, dotenv=Path('/nonexistent')).chat.model_copy(update={
        'history_from_app': 10, 'message_max_chars': 20, 'name_max_chars': 8, 'ollama_history_turns': 4,
        'summary_turns': 8, 'summary_chars': 70})


def test_history_is_cleaned(chat):
    raw = [{'role': 'user', 'text': f'message {i}'} for i in range(12)] + [{'role': 'system', 'text': 'x'}, 'junk']
    clean = hx.sanitize_chat_history(raw, chat)
    assert clean == [{'role': 'user', 'text': f'message {i}'} for i in range(4, 12)]


def test_history_text_and_names_are_cut(chat):
    raw = [{'role': 'Assistant', 'text': '  ' + 'y' * 30, 'name': 'Computer the laptop'}, {'role': 'user', 'text': ''},
           {'role': 'user', 'text': 42}]
    assert hx.sanitize_chat_history(raw, chat) == [
        {'role': 'assistant', 'text': 'y' * 20, 'name': 'Computer'}, {'role': 'user', 'text': '42'}]


@pytest.mark.parametrize('raw', [None, 'text', {'role': 'user'}])
def test_no_history(chat, raw):
    assert hx.sanitize_chat_history(raw, chat) == []


def test_history_from_the_app_can_be_switched_off(chat):
    raw = [{'role': 'user', 'text': 'hi'}]
    assert hx.sanitize_chat_history(raw, chat.model_copy(update={'history_from_app': 0})) == []


def test_older_history_becomes_a_recap(chat):
    history = [{'role': 'user', 'text': f'message {i}'} for i in range(10)]
    recent, recap = hx.budget_history(history, 6, chat, local=False)
    assert recent == history[4:]
    assert recap == ' | '.join(f'User: "message {i}"' for i in range(4))


def test_a_local_ai_gets_fewer_turns_word_for_word(chat):
    history = [{'role': 'user', 'text': f'message {i}'} for i in range(10)]
    recent, _ = hx.budget_history(history, 6, chat, local=True)
    assert recent == history[6:]


def test_the_recap_is_short(chat):
    history = [{'role': 'assistant', 'name': 'Cup', 'text': 'z' * 100}, {'role': 'user', 'text': 'now'}]
    _, recap = hx.budget_history(history, 1, chat, local=False)
    assert recap == 'Cup: "' + 'z' * 70 + '…"'
    assert hx.budget_history(history, 6, chat, local=False) == (history, '')
    assert hx.budget_history([], 6, chat, local=False) == ([], '')
