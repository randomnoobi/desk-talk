"""The real line to the Pi's numbers (HttpPiStatsLink) passes the same contract as the fake, with a fake opener
standing in for the Wi-Fi (no network)."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse

import pytest

from desktalk.adapters.dev.pi_stats_link import HttpPiStatsLink
from tests.contracts.system_stats_contract import PiStatsLinkContract

ONE_SAMPLE = {'collecting': True, 'every_sec': 2, 'keep_min': 30, 'info': {}, 'samples': [{'at': 1.0}]}
HEALTH = {'uptime_sec': 60, 'checks': {'camera': {'level': 'ok', 'value': 2.0, 'unit': 'pictures/s'}}}
LOG_STATUS = {'folder': '/data/events', 'today': {'events': {}, 'pictures_waiting': 0}}
REPLIES = {'/api/system/stats': ONE_SAMPLE, '/api/health': HEALTH, '/api/log/status': LOG_STATUS}


def answers(url: str, timeout_sec: float) -> bytes:
    """Answers like the Pi's own server: each route its own reply."""
    return json.dumps(REPLIES[urllib.parse.urlsplit(url).path]).encode()


def never_answers(url: str, timeout_sec: float) -> bytes:
    raise urllib.error.URLError(TimeoutError('timed out'))


class TestHttpPiStatsLink(PiStatsLinkContract):
    @pytest.fixture
    def link(self):
        return HttpPiStatsLink('desktalk.local:5000', 3, opener=answers)

    @pytest.fixture
    def dead_link(self):
        return HttpPiStatsLink('desktalk.local:5000', 3, opener=never_answers)
