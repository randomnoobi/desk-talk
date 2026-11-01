from __future__ import annotations

import time

import pytest

from desktalk_cloud.clock import SystemClock
from desktalk_cloud.ingest import ingest
from desktalk_cloud.rate_limit import RateLimiter
from desktalk_cloud.store_memory import MemoryStore
from desktalk_cloud.tokens import hash_secret, new_code, new_token, normal_code
from tests.fakes import FakeClock
from tests.fakes.cloud import cloud_config, link_device, make_parts

BIG = 10 ** 30


@pytest.fixture
def parts():
    return make_parts()


@pytest.fixture
def device(parts):
    return link_device(parts).device


def run(parts, device, events, caught_up=False, config=None):
    config = config or parts.config
    return ingest(parts.store, parts.clock, config.ingest, config.messages, device.id, events, caught_up)


def habit(event_id='h1', ts=100.0, **more):
    return {'id': event_id, 'ts': ts, 'type': 'habit', 'habit': 'water', 'action': 'sip', 'ml': 30, **more}


def stored(parts, device):
    return parts.store.events_after(device.id, 0.0, None, 100)


def test_events_totals_and_state_are_sorted_and_saved(parts, device):
    state = {'id': 's1', 'ts': 50.0, 'type': 'object_state', 'change': 'moved', 'object': 'cup',
             'prev_state': 'empty', 'new_state': 'full', 'bbox': [1, 2, 3, 4]}
    daily = {'id': 'whatever', 'ts': 60.0, 'type': 'daily_totals', 'day': '2026-10-20', 'totals': {'water_ml': 500}}
    reply = run(parts, device, [habit(), state, daily])
    assert (reply.accepted, reply.repeats, reply.refused) == (3, 0, ())
    assert [e['id'] for e in stored(parts, device)] == ['s1', 'h1']
    assert 'bbox' not in stored(parts, device)[0]
    assert parts.store.totals_between(device.id, '2026-10-20', '2026-10-20')[0]['id'] == 'daily-2026-10-20'
    cup = parts.store.object_states(device.id)[0]
    assert (cup.label, cup.state, cup.since) == ('cup', 'full', 50.0)


def test_repeats_are_counted_not_kept_twice(parts, device):
    run(parts, device, [habit()])
    reply = run(parts, device, [habit(), habit('h2', 101.0)])
    assert (reply.accepted, reply.repeats) == (1, 1)


def test_the_filter_runs_again_on_the_server(parts, device):
    run(parts, device, [habit(say='english essay', picture_jpeg=5, extra_text='x')])
    event = stored(parts, device)[0]
    assert 'say' not in event and 'extra_text' not in event and 'picture_jpeg' not in event
    assert event['ml'] == 30


def test_events_that_stay_home_are_refused_by_id(parts, device):
    reply = run(parts, device, [{'id': 'd1', 'ts': 1.0, 'type': 'detection'}, habit('h1')])
    assert reply.accepted == 1
    assert [(r.id, bool(r.why)) for r in reply.refused] == [('d1', True)]


def test_entries_that_are_not_events_are_refused(parts, device):
    reply = run(parts, device, ['text', 5, None, {'ts': 1.0}, habit()])
    assert reply.accepted == 1
    assert len(reply.refused) == 4
    assert all(r.why for r in reply.refused)


@pytest.mark.parametrize('bad', [BIG, -BIG, 10 ** 13, 1e13])
def test_a_number_beyond_the_limit_refuses_that_event_only(parts, device, bad):
    reply = run(parts, device, [habit('big', ml=bad), habit('ok', 101.0)])
    assert reply.accepted == 1 and [r.id for r in reply.refused] == ['big']
    assert 'number' in reply.refused[0].why
    assert [e['id'] for e in stored(parts, device)] == ['ok']


def test_a_huge_number_inside_totals_or_goals_is_refused(parts, device):
    daily = {'id': 'x', 'ts': 1.0, 'type': 'daily_totals', 'day': '2026-10-20', 'totals': {'water_ml': BIG}}
    goals = {'id': 'goals-1', 'ts': 1.0, 'type': 'goals', 'wake': '07:00', 'bed': '23:00', 'goals': {'water': BIG}}
    huge_time = habit('late', 10 ** 15)
    reply = run(parts, device, [daily, goals, huge_time])
    assert reply.accepted == 0 and len(reply.refused) == 3


def test_the_limit_comes_from_settings(parts, device):
    config = cloud_config(ingest={'max_number': 100})
    reply = run(parts, device, [habit('a', ml=101), habit('b', 5.0, ml=100)], config=config)
    assert reply.accepted == 1


def test_a_filter_that_crashes_refuses_the_event_and_the_batch_goes_on(parts, device, monkeypatch):
    from desktalk_cloud import ingest as module
    real = module.to_cloud_event

    def flaky(event):
        if event.get('id') == 'boom':
            raise RuntimeError('odd value')
        return real(event)
    monkeypatch.setattr(module, 'to_cloud_event', flaky)
    reply = run(parts, device, [habit('boom'), habit('fine', 101.0)])
    assert reply.accepted == 1 and [r.id for r in reply.refused] == ['boom']


def test_a_very_long_id_is_cut_in_the_reply(parts, device):
    reply = run(parts, device, [{'id': 'x' * 5000, 'ts': 1.0, 'type': 'detection'}])
    assert len(reply.refused[0].id) < 500


def test_caught_up_is_saved(parts, device):
    parts.clock.advance(50)
    run(parts, device, [], caught_up=True)
    assert parts.store.get_device(device.id).caught_up_at == parts.clock.now()


# ── the small parts the core is built from ────────────────────────────────
def test_tokens_codes_and_hashes():
    token, other = new_token(), new_token()
    assert token != other and token.startswith('dtk_') and len(token) > 40
    code = new_code()
    assert code != new_code() and len(code) == 8 and code == code.upper()
    assert set(code) <= set('ABCDEFGHJKLMNPQRSTUVWXYZ23456789')
    assert hash_secret(token) == hash_secret(token) != hash_secret(other)
    assert token not in hash_secret(token) and len(hash_secret(token)) == 64


def test_people_may_type_a_code_with_dashes_spaces_and_small_letters():
    assert normal_code(' abcd-efgh ') == 'ABCDEFGH'
    assert hash_secret(normal_code('ab cd-ef gh')) == hash_secret('ABCDEFGH')


def test_system_clock_tells_the_time():
    clock = SystemClock()
    assert abs(clock.now() - time.time()) < 5
    assert clock.monotonic() <= clock.monotonic()


def test_limiter_counts_per_key_and_forgets_after_the_window():
    clock = FakeClock()
    limiter = RateLimiter(2, 60.0, clock)
    assert [limiter.hit('a'), limiter.hit('a'), limiter.hit('a')] == [True, True, False]
    assert limiter.hit('b') is True
    clock.advance(30)
    assert limiter.hit('a') is False            # the refused try does not count, but the first two still do
    clock.advance(31)
    assert limiter.hit('a') is True
    clock.advance(1000)
    assert limiter.hit('c') is True              # old keys are swept
    assert limiter.size() == 1


def test_a_refused_try_does_not_extend_the_wait():
    clock = FakeClock()
    limiter = RateLimiter(1, 60.0, clock)
    assert limiter.hit('a')
    for _ in range(5):
        clock.advance(10)
        assert not limiter.hit('a')
    clock.advance(11)
    assert limiter.hit('a')


def test_memory_store_is_a_store():
    from desktalk_cloud.ports import Store
    assert isinstance(MemoryStore(), Store)


def test_an_object_state_without_an_object_is_kept_but_changes_no_state(parts, device):
    reply = run(parts, device, [{'id': 's1', 'ts': 5.0, 'type': 'object_state', 'change': 'moved'}])
    assert reply.accepted == 1 and parts.store.object_states(device.id) == []


def test_an_event_whose_id_is_not_text_is_refused_with_an_empty_id(parts, device):
    reply = run(parts, device, [{'id': 5, 'ts': 1.0, 'type': 'habit'}])
    assert [r.id for r in reply.refused] == ['']


def test_limiter_forgets_old_tries_one_by_one():
    clock = FakeClock()
    limiter = RateLimiter(2, 60.0, clock)
    assert limiter.hit('a')
    clock.advance(40)
    assert limiter.hit('a')
    clock.advance(30)                         # the first try is now 70 seconds old, the second 30
    assert limiter.hit('a') and not limiter.hit('a')


def test_memory_store_refuses_what_a_real_store_would():
    store = MemoryStore()
    with pytest.raises(LookupError):
        store.save_batch('nobody', [], [], [], False, 1.0)
    store.add_code('c', 'u', None, 'UTC', 1.0)
    with pytest.raises(ValueError, match='already exists'):
        store.add_code('c', 'u', None, 'UTC', 1.0)
    with pytest.raises(ValueError, match='no_data'):
        store.finish_report('r', 'pending', None, None, None, 1.0)


def test_a_true_or_false_flag_is_not_a_number_for_the_limit(parts, device):
    reply = run(parts, device, [habit('h1', healthy=True), habit('h2', 101.0, healthy=False)])
    assert reply.accepted == 2
