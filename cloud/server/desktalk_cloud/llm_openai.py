"""Writes the weekly report with any AI company that speaks the OpenAI chat completions form: Gemini, OpenAI, Groq,
Ollama ... (settings → reports.provider: openai_compatible, and reports.url)."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import httpx
from pydantic import SecretStr

from desktalk_cloud.config import ReportSettings
from desktalk_cloud.ports import ReportFailed
from desktalk_cloud.report_data import with_data
from desktalk_cloud.report_errors import ReportWillNotWork, failure_for_status

__all__ = ['OpenAIReportWriter']

log = logging.getLogger(__name__)


class OpenAIReportWriter:
    """A ReportWriter. The AI company's own error words are never kept or logged, only the status."""

    def __init__(self, settings: ReportSettings, api_key: SecretStr | None, errors: Mapping[str, str],
                 client: httpx.Client | None = None) -> None:
        self._settings, self._key, self._errors, self._client = settings, api_key, errors, client

    def write(self, prompt: str, data: str) -> str:
        if self._key is None:
            raise ReportWillNotWork(self._errors['no_key'])
        body = {'model': self._settings.model, 'max_tokens': self._settings.max_tokens,
                'messages': [{'role': 'user', 'content': with_data(prompt, data)}]}
        headers = {'Authorization': f'Bearer {self._key.get_secret_value()}'}
        try:
            if self._client is not None:
                answer = self._client.post(self._settings.url, json=body, headers=headers,
                                           timeout=self._settings.timeout_sec)
            else:
                with httpx.Client() as client:
                    answer = client.post(self._settings.url, json=body, headers=headers,
                                         timeout=self._settings.timeout_sec)
        except httpx.TimeoutException:
            raise ReportFailed(self._errors['timeout']) from None
        except httpx.TransportError:
            raise ReportFailed(self._errors['unreachable']) from None
        if not answer.is_success:
            log.warning('The AI company answered the weekly report with status %s', answer.status_code)
            raise failure_for_status(answer.status_code, self._errors)
        return self._text_of(answer)

    def _text_of(self, answer: httpx.Response) -> str:
        try:
            choice: Any = answer.json()['choices'][0]
            text, finish = choice['message'].get('content'), choice.get('finish_reason')
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise ReportFailed(self._errors['bad_reply']) from None
        if finish == 'length':
            # A report that stops half way is not kept, and the same limit would stop it again.
            raise ReportWillNotWork(self._errors['cut_off'])
        if finish == 'content_filter':
            raise ReportFailed(self._errors['refused'])
        if finish not in (None, 'stop'):                        # (a missing reason is fine: some servers leave it out)
            raise ReportFailed(self._errors['bad_reply'])
        if not isinstance(text, str) or not text.strip():
            raise ReportFailed(self._errors['empty'])
        return text.strip()
