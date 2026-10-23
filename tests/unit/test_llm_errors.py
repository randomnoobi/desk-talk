"""AI errors in plain words, and which ones are worth trying again."""

import pytest

from desktalk.adapters.llm.errors import AIRefused, error_from, friendly_ai_error, is_rate_limit
from desktalk.domain import AIUnavailable


@pytest.mark.parametrize('text', [
    'Error code: 429 - Too Many Requests', 'You exceeded your current quota', 'rate limit reached',
    'RESOURCE_EXHAUSTED', 'Quota exhausted for today',
])
def test_rate_limits_are_recognised(text):
    assert is_rate_limit(text)


@pytest.mark.parametrize('text', ['Connection refused', 'Error code: 500', 'Request timed out.'])
def test_other_errors_are_not_rate_limits(text):
    assert not is_rate_limit(text)


@pytest.mark.parametrize('error, label, words', [
    ('Error code: 429', 'Gemini gemini-2.5-flash', 'Gemini hit its free usage limit'),
    ('Error code: 401 - invalid api key', 'Groq llama', 'Groq rejected the key in .env'),
    ('Error code: 404 - model not found', 'OpenAI gpt-4o-mini', 'could not find the model'),
    ('Request timed out.', 'Gemini gemini-2.5-flash', 'Gemini took too long to answer'),
    ('Request timed out.', 'Ollama llama3.2', 'Ollama took too long to answer'),
    ('Connection refused', 'Ollama llama3.2', 'Could not reach Ollama. Open the Ollama app'),
    ('Connection refused', 'Gemini gemini-2.5-flash', 'Check the internet connection'),
    ('something odd', '', 'The AI returned an error: something odd'),
])
def test_friendly_errors(error, label, words):
    assert words in friendly_ai_error(error, label)


def test_a_rate_limit_becomes_a_rate_limited_ai_unavailable():
    err = error_from(RuntimeError('Error code: 429 - quota'), 'Gemini gemini-2.5-flash')
    assert isinstance(err, AIUnavailable) and not isinstance(err, AIRefused)
    assert err.rate_limited and 'usage limit' in err.reason


@pytest.mark.parametrize('error', ['Error code: 401 - Unauthorized', 'Error code: 404 - model not found'])
def test_a_bad_key_or_unknown_model_is_refused_so_nobody_tries_again(error):
    err = error_from(RuntimeError(error), 'OpenAI gpt-4o-mini')
    assert isinstance(err, AIRefused) and not err.rate_limited


def test_a_connection_problem_can_be_tried_again():
    err = error_from(ConnectionError('Connection refused'), 'Gemini gemini-2.5-flash')
    assert type(err) is AIUnavailable and not err.rate_limited
