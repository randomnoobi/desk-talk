"""The real line to the Pi's numbers (HttpPiStatsLink) passes the same contract as the fake, with a fake opener
standing in for the Wi-Fi (no network)."""

from __future__ import annotations

import json
import urllib.error

import pytest

from desktalk.adapters.dev.pi_stats_link import HttpPiStatsLink
from tests.contracts.system_stats_contract import PiStatsLinkContract

ONE_SAMPLE = {'collecting': True, 'every_sec': 2, 'keep_min': 30, 'info': {}, 'samples': [{'at': 1.0}]}


def answers(url: str, timeout_sec: float) -> bytes:
    return json.dumps(ONE_SAMPLE).encode()


def never_answers(url: str, timeout_sec: float) -> bytes:
    raise urllib.error.URLError(TimeoutError('timed out'))


class TestHttpPiStatsLink(PiStatsLinkContract):
    @pytest.fixture
    def link(self):
        return HttpPiStatsLink('desktalk.local:5000', 3, opener=answers)

    @pytest.fixture
    def dead_link(self):
        return HttpPiStatsLink('desktalk.local:5000', 3, opener=never_answers)
