"""The daily clean-up that deletes events older than retention.events_days (desktalk_cloud.retention)."""

from __future__ import annotations

import logging

from desktalk_cloud.retention import RetentionJob
from tests.fakes import FakeClock
from tests.fakes.cloud import cloud_config, link_device, make_parts

DAY = 86400
SETTINGS = cloud_config().retention


def event(event_id: str, ts: float) -> dict:
    return {'id': event_id, 'ts': ts, 'type': 'habit', 'habit': 'hydration', 'action': 'sip', 'ml': 80}


def test_deletes_events_older_than_the_limit_and_keeps_the_rest(caplog):
    clock = FakeClock(start=1_800_000_000.0)
    parts = make_parts(clock=clock)
    ann = link_device(parts).device
    cutoff = clock.now() - SETTINGS.events_days * DAY
    parts.store.save_batch(ann.id, [event('old', cutoff - 1), event('edge', cutoff), event('new', clock.now() - DAY)],
                           [], [], True, clock.now())
    job = RetentionJob(parts.store, SETTINGS)
    with caplog.at_level(logging.INFO):
        wait = job.tick(clock.now())
    kept = [e['id'] for e in parts.store.events_after(ann.id, 0.0, None, 10)]
    assert kept == ['edge', 'new']
    assert wait == SETTINGS.check_every_hours * 3600
    assert '1 ' in caplog.text


def test_deletes_for_every_pi_and_does_nothing_when_nothing_is_old():
    clock = FakeClock(start=1_800_000_000.0)
    parts = make_parts(clock=clock)
    pis = [link_device(parts, name).device for name in ('ann', 'bob')]
    old = clock.now() - (SETTINGS.events_days + 1) * DAY
    for pi in pis:
        parts.store.save_batch(pi.id, [event(f'old-{pi.id}', old), event(f'new-{pi.id}', clock.now())], [], [], True,
                               clock.now())
    job = RetentionJob(parts.store, SETTINGS)
    job.tick(clock.now())
    assert [e['id'] for pi in pis for e in parts.store.events_after(pi.id, 0.0, None, 10)] == [
        f'new-{pi.id}' for pi in pis]
    job.tick(clock.now())                                                       # a second round finds nothing old
    assert all(len(parts.store.events_after(pi.id, 0.0, None, 10)) == 1 for pi in pis)
