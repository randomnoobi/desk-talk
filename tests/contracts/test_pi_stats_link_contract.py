"""The real line to the Pi's numbers (HttpPiStatsLink) passes the same contract as the fake, with a fake opener
standing in for the Wi-Fi (no network)."""

from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.parse

import pytest

from desktalk.adapters.dev.pi_stats_link import HttpPiStatsLink
from tests.contracts.system_stats_contract import PiStatsLinkContract

ONE_SAMPLE = {'collecting': True, 'every_sec': 2, 'keep_min': 30, 'info': {}, 'samples': [{'at': 1.0}]}
HEALTH = {'uptime_sec': 60, 'checks': {'camera': {'level': 'ok', 'value': 2.0, 'unit': 'pictures/s'}}}
STATES = {'objects': {'cup': {'state': 'PRESENT', 'since': 5.0}}, 'watched_since': 1.0,
          'changes': [{'ts': 5.0, 'label': 'cup', 'change': 'appeared'}]}
TRACE = {'label': 'cup', 'steps': [{'ts': 5.0, 'conf': 0.9, 'phase': 'PRESENT'}]}
PHOTO = {'id': 'cup-1', 'ts': 5.0, 'why': 'appeared', 'confidence': 0.9, 'box': [1, 2, 3, 4], 'size': [640, 480]}
STORY = {'label': 'cup', 'timeline': [{'ts': 5.0, 'source': 'state', 'kind': 'appeared', 'photo': PHOTO}],
         'photos': [PHOTO]}
REPLIES = {'/api/system/stats': ONE_SAMPLE, '/api/health': HEALTH, '/api/objects/states': STATES,
           '/api/objects/smoothing': {'labels': ['cup']}, '/api/objects/smoothing/trace': TRACE,
           '/api/objects/cup/story': STORY}
PICTURES = {'/api/objects/photo/cup-1': b'\xff\xd8 a jpeg'}


def answers(url: str, timeout_sec: float) -> bytes:
    """Answers like the Pi's own server: each route its own reply, and 404 for a story or picture it hasn't got."""
    path = urllib.parse.urlsplit(url).path
    if path in PICTURES:
        return PICTURES[path]
    if path not in REPLIES:
        raise urllib.error.HTTPError(url, 404, 'Not Found', http.client.HTTPMessage(), None)
    return json.dumps(REPLIES[path]).encode()


def never_answers(url: str, timeout_sec: float) -> bytes:
    raise urllib.error.URLError(TimeoutError('timed out'))


class TestHttpPiStatsLink(PiStatsLinkContract):
    @pytest.fixture
    def link(self):
        return HttpPiStatsLink('desktalk.local:5000', 3, opener=answers)

    @pytest.fixture
    def dead_link(self):
        return HttpPiStatsLink('desktalk.local:5000', 3, opener=never_answers)
