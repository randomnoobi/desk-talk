"""The Pi's own linker (CloudLinker) passes the CloudLinking contract, with a stand-in for the cloud server's claim
route (no network)."""

from __future__ import annotations

from pathlib import Path

import pytest

from desktalk.config import load_config
from desktalk.core.cloud.linker import CloudLinker
from desktalk.domain import CloudNotLinked, CloudUnavailable
from desktalk_shared.cloud_api import ClaimReply
from tests.contracts.cloud_linking_contract import CloudLinkingContract
from tests.fakes import FakeClock, FakeDesktopNotifications, InMemoryKeyValueStore

CONFIG = load_config(environ={}, dotenv=Path('/nonexistent'))


def claims_with(error: Exception | None):
    def claim(server: str, code: str, name: str, *, timeout_sec: float, error_text_chars: int) -> ClaimReply:
        if error is not None:
            raise error
        return ClaimReply(device_id='dev-1', token='a-token')
    return claim


def linker(error: Exception | None = None) -> CloudLinker:
    return CloudLinker(InMemoryKeyValueStore(), claims_with(error), CONFIG.cloud, CONFIG.prompts.cloud,
                       FakeDesktopNotifications(), FakeClock(), public_address='desktalk.local:5000', saved=True,
                       host_name=lambda: 'desktalk')


class TestCloudLinker(CloudLinkingContract):
    @pytest.fixture
    def linking(self):
        return linker()

    @pytest.fixture
    def refusing_linking(self):
        return linker(CloudNotLinked('that code has run out'))

    @pytest.fixture
    def dead_linking(self):
        return linker(CloudUnavailable('no answer within 10 s'))
