"""Writes the weekly report with Claude, through the `anthropic` SDK (settings → reports.provider: anthropic)."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import anthropic
from pydantic import SecretStr

from desktalk_cloud.config import ReportSettings
from desktalk_cloud.ports import ReportFailed
from desktalk_cloud.report_data import with_data
from desktalk_cloud.report_job import ReportWillNotWork, failure_for_status

__all__ = ['AnthropicReportWriter']

log = logging.getLogger(__name__)

NO_SDK_RETRIES = 0           # the report job does the retrying (with longer and longer waits), so the SDK does none


class AnthropicReportWriter:
    """A ReportWriter. The key is passed to the SDK here, not read from the environment (the server's key is
    REPORT_API_KEY). The AI company's own error words are never kept or logged, only the status."""

    def __init__(self, settings: ReportSettings, api_key: SecretStr | None, errors: Mapping[str, str],
                 client: Any = None) -> None:
        self._settings, self._key, self._errors, self._client = settings, api_key, errors, client

    def _sdk_client(self) -> Any:
        if self._client is None:
            assert self._key is not None
            self._client = anthropic.Anthropic(api_key=self._key.get_secret_value(), max_retries=NO_SDK_RETRIES)
        return self._client

    def write(self, prompt: str, data: str) -> str:
        if self._key is None:
            raise ReportWillNotWork(self._errors['no_key'])
        try:
            answer = self._sdk_client().messages.create(
                model=self._settings.model, max_tokens=self._settings.max_tokens, timeout=self._settings.timeout_sec,
                messages=[{'role': 'user', 'content': with_data(prompt, data)}])
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as error:
            raise failure_for_status(error.status_code, self._errors) from None
        except anthropic.APITimeoutError:
            raise ReportFailed(self._errors['timeout']) from None
        except anthropic.APIConnectionError:
            raise ReportFailed(self._errors['unreachable']) from None
        except anthropic.APIStatusError as error:
            log.warning('The AI company answered the weekly report with status %s', error.status_code)
            raise failure_for_status(error.status_code, self._errors) from None
        if answer.stop_reason == 'refusal':
            raise ReportFailed(self._errors['refused'])
        if answer.stop_reason == 'max_tokens':
            raise ReportFailed(self._errors['cut_off'])       # a report that stops half way is not kept
        text = ''.join(block.text for block in answer.content if block.type == 'text').strip()
        if not text:
            raise ReportFailed(self._errors['empty'])
        return text
