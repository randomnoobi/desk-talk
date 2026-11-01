"""What is specific to each report writer: what it asks the AI company for, and how it reads the answers.
(What both must do is in tests/contracts/test_cloud_report_writer_contract.py.)"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx
import httpx2
import pytest
from pydantic import SecretStr

from desktalk_cloud.llm_anthropic import AnthropicReportWriter
from desktalk_cloud.llm_openai import OpenAIReportWriter
from desktalk_cloud.ports import ReportFailed
from desktalk_cloud.report_data import load_report_words
from desktalk_cloud.report_errors import ReportWillNotWork
from tests.fakes.cloud import cloud_config

KEY = 'sk-test-key-0123456789'
CONFIG = cloud_config(reports={'provider': 'openai_compatible', 'url': 'https://ai.test/v1/chat/completions',
                               'model': 'test-model', 'max_tokens': 777, 'timeout_sec': 9})
SETTINGS = CONFIG.reports
ERRORS = load_report_words(CONFIG.prompts, CONFIG.messages).errors


# ── Anthropic ────────────────────────────────────────────────────────────────────────────────────────────────────
class Recorder:
    def __init__(self, outcome: Any):
        self.outcome, self.requests = outcome, []
        self.messages = self

    def create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def text_answer(text: str = '# Week') -> Any:
    return SimpleNamespace(content=[SimpleNamespace(type='text', text=text)], stop_reason='end_turn')


def test_anthropic_asks_for_one_user_message_with_the_settings_and_nothing_extra():
    client = Recorder(text_answer())
    AnthropicReportWriter(SETTINGS, SecretStr(KEY), ERRORS, client=client).write('RULES', 'DATA')
    (request,) = client.requests
    assert request['model'] == 'test-model' and request['max_tokens'] == 777 and request['timeout'] == 9
    assert request['messages'] == [{'role': 'user', 'content': 'RULES\n\n<data>\nDATA\n</data>'}]
    assert set(request) == {'model', 'max_tokens', 'timeout', 'messages'}     # no sampling settings, no thinking switch


def test_anthropic_gets_its_client_with_the_key_from_the_settings_not_from_the_environment(monkeypatch):
    made: list[dict[str, Any]] = []

    class FakeAnthropic(Recorder):
        def __init__(self, **kwargs: Any):
            super().__init__(text_answer())
            made.append(kwargs)

    monkeypatch.setattr(anthropic, 'Anthropic', FakeAnthropic)
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-from-the-environment')
    writer = AnthropicReportWriter(SETTINGS, SecretStr(KEY), ERRORS)
    assert made == []                                                         # no client until a report is written
    writer.write('RULES', 'DATA')
    writer.write('RULES', 'DATA')
    assert len(made) == 1 and made[0]['api_key'] == KEY and made[0]['max_retries'] == 0
    assert KEY not in repr(writer)


def test_anthropic_without_a_key_makes_no_client(monkeypatch):
    def boom(**kwargs: Any):
        raise AssertionError('a client was made')

    monkeypatch.setattr(anthropic, 'Anthropic', boom)
    with pytest.raises(ReportWillNotWork):
        AnthropicReportWriter(SETTINGS, None, ERRORS).write('RULES', 'DATA')


def test_anthropic_a_refusal_is_a_plain_failure():
    answer = SimpleNamespace(content=[], stop_reason='refusal')
    with pytest.raises(ReportFailed) as caught:
        AnthropicReportWriter(SETTINGS, SecretStr(KEY), ERRORS, client=Recorder(answer)).write('R', 'D')
    assert str(caught.value) == ERRORS['refused']


@pytest.mark.parametrize(('status', 'kind', 'fatal'), [
    (400, anthropic.BadRequestError, True), (403, anthropic.PermissionDeniedError, True),
    (404, anthropic.NotFoundError, True), (429, anthropic.RateLimitError, False),
    (529, anthropic.APIStatusError, False)])
def test_anthropic_which_errors_are_worth_trying_again(status, kind, fatal):
    response = httpx2.Response(status, request=httpx2.Request('POST', 'https://api.anthropic.test/v1/messages'))
    error = kind('body words', response=response, body=None)
    with pytest.raises(ReportFailed) as caught:
        AnthropicReportWriter(SETTINGS, SecretStr(KEY), ERRORS, client=Recorder(error)).write('R', 'D')
    expected = ERRORS['key_refused'] if status == 403 else ERRORS['status'].format(status=status)
    assert str(caught.value) == expected and isinstance(caught.value, ReportWillNotWork) is fatal


# ── OpenAI-compatible ────────────────────────────────────────────────────────────────────────────────────────────
def openai_writer(handler, key: str | None = KEY) -> OpenAIReportWriter:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return OpenAIReportWriter(SETTINGS, SecretStr(key) if key else None, ERRORS, client=client)


def ok(text: str = '# Week') -> httpx.Response:
    return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': text}}]})


def test_openai_posts_to_the_settings_address_with_the_key_as_a_bearer_token():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return ok()

    openai_writer(handler).write('RULES', 'DATA')
    (request,) = seen
    assert str(request.url) == 'https://ai.test/v1/chat/completions' and request.method == 'POST'
    assert request.headers['authorization'] == f'Bearer {KEY}'
    assert json.loads(request.content) == {'model': 'test-model', 'max_tokens': 777,
                                           'messages': [{'role': 'user', 'content': 'RULES\n\n<data>\nDATA\n</data>'}]}


@pytest.mark.parametrize('reply', [
    httpx.Response(200, text='not json at all'), httpx.Response(200, json={'choices': []}),
    httpx.Response(200, json={'choices': [{'message': 'not an object'}]}), httpx.Response(200, json=['a list'])])
def test_openai_an_answer_in_a_strange_shape_is_a_plain_failure(reply):
    with pytest.raises(ReportFailed) as caught:
        openai_writer(lambda request: reply).write('R', 'D')
    assert str(caught.value) == ERRORS['bad_reply'] and not isinstance(caught.value, ReportWillNotWork)


@pytest.mark.parametrize(('status', 'fatal'), [(400, True), (404, True), (422, True), (429, False), (503, False)])
def test_openai_which_errors_are_worth_trying_again(status, fatal):
    with pytest.raises(ReportFailed) as caught:
        openai_writer(lambda request: httpx.Response(status, text='words')).write('R', 'D')
    assert str(caught.value) == ERRORS['status'].format(status=status)
    assert isinstance(caught.value, ReportWillNotWork) is fatal


def test_openai_makes_its_own_client_when_it_is_not_given_one(monkeypatch):
    real = httpx.Client
    monkeypatch.setattr(httpx, 'Client', lambda: real(transport=httpx.MockTransport(lambda request: ok('# Made'))))
    writer = OpenAIReportWriter(SETTINGS, SecretStr(KEY), ERRORS)
    assert writer.write('RULES', 'DATA') == '# Made'


@pytest.mark.parametrize(('finish', 'words', 'fatal'), [
    ('length', 'cut_off', True), ('content_filter', 'refused', False), ('tool_calls', 'bad_reply', False),
    ('something_new', 'bad_reply', False)])
def test_openai_any_finish_other_than_stop_is_a_failure(finish, words, fatal):
    choice = {'finish_reason': finish, 'message': {'content': '# Half a report'}}
    reply = httpx.Response(200, json={'choices': [choice]})
    with pytest.raises(ReportFailed) as caught:
        openai_writer(lambda request: reply).write('R', 'D')
    assert str(caught.value) == ERRORS[words] and isinstance(caught.value, ReportWillNotWork) is fatal


@pytest.mark.parametrize('finish', ['stop', None])
def test_openai_a_missing_finish_reason_is_fine(finish):
    choice: dict[str, Any] = {'message': {'content': '# Done'}}
    if finish:
        choice['finish_reason'] = finish
    assert openai_writer(lambda request: httpx.Response(200, json={'choices': [choice]})).write('R', 'D') == '# Done'


@pytest.mark.parametrize(('stop', 'words'), [('pause_turn', 'bad_reply'), ('tool_use', 'bad_reply')])
def test_anthropic_a_stop_that_is_not_an_ending_is_a_failure(stop, words):
    answer = SimpleNamespace(content=[SimpleNamespace(type='text', text='# Half')], stop_reason=stop)
    with pytest.raises(ReportFailed) as caught:
        AnthropicReportWriter(SETTINGS, SecretStr(KEY), ERRORS, client=Recorder(answer)).write('R', 'D')
    assert str(caught.value) == ERRORS[words]
