"""The Pi's numbers on the web: GET /api/system/stats (desktalk/web/routers/system.py, on the Pi) and
GET /api/dev/pi_stats and /api/dev/health (desktalk/web/routers/dev.py, the developer page on
a computer, which fetches the Pi's). The routers are built here with fakes; the same routes on the whole app (and
the computer's own health when the Pi isn't elsewhere) are in tests/integration/test_web_dev.py, and the
characterization cases in tests/characterization/."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from desktalk.web.app import _error_reply
from desktalk.web.deps import dev_only
from desktalk.web.routers import dev, system
from tests.fakes import FakePiStatsLink, FakeStats
from tests.unit.test_web_dev_trace import app_config


def client_for(router, **parts) -> TestClient:
    parts.setdefault('pi', object())                 # the camera is the Pi (only whether it's there matters here)
    web = FastAPI()
    web.include_router(router)
    web.dependency_overrides[dev_only] = lambda: None
    web.add_exception_handler(HTTPException, cast(Any, _error_reply))     # replies {"error": ...}, like the app
    web.state.desktalk = SimpleNamespace(config=app_config(), **parts)
    return TestClient(web)


def test_system_stats_sends_the_history():
    stats = FakeStats(every_sec=2.0)
    stats.tick(10.0)
    stats.tick(12.0)
    reply = client_for(system.router, system_stats=stats).get('/api/system/stats').json()
    assert reply['collecting'] is True
    assert reply['every_sec'] == 2.0
    assert [s['at'] for s in reply['samples']] == [10.0, 12.0]


def test_system_stats_since_keeps_only_newer_samples():
    stats = FakeStats()
    stats.tick(10.0)
    stats.tick(12.0)
    reply = client_for(system.router, system_stats=stats).get('/api/system/stats', params={'since': 10.0}).json()
    assert [s['at'] for s in reply['samples']] == [12.0]


def test_system_stats_says_when_it_is_not_collecting():
    reply = client_for(system.router, system_stats=FakeStats(collecting=False)).get('/api/system/stats').json()
    assert reply['collecting'] is False and reply['samples'] == []


def test_the_dev_route_passes_the_pi_reply_on_with_its_address():
    link = FakePiStatsLink()
    client = client_for(dev.router, pi_stats_link=link)
    reply = client.get('/api/dev/pi_stats', params={'since': 5.5}).json()
    assert reply['address'] == app_config().dev.pi_address
    assert reply['samples'][0]['at'] == 10.0
    assert link.asked == [5.5]
    client.get('/api/dev/pi_stats')
    assert link.asked == [5.5, None]


def test_the_dev_route_says_in_plain_words_when_the_pi_does_not_answer():
    client = client_for(dev.router, pi_stats_link=FakePiStatsLink(problem='no answer in 3 s'))
    response = client.get('/api/dev/pi_stats')
    assert response.status_code == 502
    words = app_config().prompts.dev.pi_stats_unreachable.format(address=app_config().dev.pi_address,
                                                                 problem='no answer in 3 s')
    assert response.json() == {'error': words}


def test_the_dev_route_is_404_without_the_developer_page():
    assert client_for(dev.router, pi_stats_link=None).get('/api/dev/pi_stats').status_code == 404


def test_the_pi_health_comes_from_the_pi_with_its_address():
    """A computer (web.public_address empty) whose camera is the Pi: the Pi's own reply, passed on."""
    link = FakePiStatsLink()
    reply = client_for(dev.router, pi_stats_link=link).get('/api/dev/health').json()
    assert reply['from'] == 'pi'
    assert reply['address'] == app_config().dev.pi_address
    assert {k: v for k, v in reply.items() if k not in ('from', 'address')} == link.health_reply


def test_the_pi_health_says_in_plain_words_when_the_pi_does_not_answer():
    """Its own words: the System tab asked for health checks, not numbers."""
    client = client_for(dev.router, pi_stats_link=FakePiStatsLink(problem='no answer in 3 s'))
    response = client.get('/api/dev/health')
    assert response.status_code == 502
    words = app_config().prompts.dev.pi_health_unreachable.format(address=app_config().dev.pi_address,
                                                                  problem='no answer in 3 s')
    assert response.json() == {'error': words}
    assert 'numbers' not in words
