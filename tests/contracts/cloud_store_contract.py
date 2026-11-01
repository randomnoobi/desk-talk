"""What every cloud server Store must do (desktalk_cloud.ports.Store).

The in-memory store and the Postgres store run this same suite. Nothing here may depend on one of them, and the
time is always passed in, never read from the wall clock."""

from __future__ import annotations

import threading
from typing import Any

from desktalk_cloud.domain import Device, StateChange, User

NOW = 1000.0
LATER = 5000.0


def make_user(store, name: str = 'ann') -> User:
    return store.upsert_user(f'google-{name}', f'{name}@example.com', name.title(), NOW)


def link(store, user: User, label: str = 'a', *, now: float = NOW, device_id: str | None = None,
         time_zone: str = 'UTC') -> tuple[Device, str]:
    """Make a code and use it. The code is `code-<label>`, the token `token-<label>`."""
    store.add_code(f'code-{label}', user.id, device_id, time_zone, now + 600)
    device = store.claim_code(f'code-{label}', f'Pi {label}', f'token-{label}', now)
    assert device is not None
    return device, f'token-{label}'


def habit(event_id: str, ts: float) -> dict[str, Any]:
    return {'id': event_id, 'ts': ts, 'type': 'habit', 'habit': 'water', 'action': 'sip', 'ml': 30}


def totals(day: str, ts: float, water: float) -> dict[str, Any]:
    return {'id': f'daily-{day}', 'ts': ts, 'type': 'daily_totals', 'day': day, 'totals': {'water_ml': water}}


def save(store, device: Device, events=(), totals_=(), changes=(), *, caught_up: bool = False, now: float = NOW):
    return store.save_batch(device.id, list(events), list(totals_), list(changes), caught_up, now)


class CloudStoreContract:
    """Subclass and give a `store` fixture (empty, new for each test)."""

    # ── linking a Pi ──────────────────────────────────────────────────────
    def test_two_claims_one_winner(self, store):
        user = make_user(store)
        store.add_code('code', user.id, None, 'UTC', 2000.0)
        results: list[Device | None] = []
        barrier = threading.Barrier(8)

        def claim(i: int) -> None:
            barrier.wait()
            results.append(store.claim_code('code', f'Pi {i}', f'token-{i}', NOW))

        threads = [threading.Thread(target=claim, args=(i,)) for i in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert len([r for r in results if r is not None]) == 1
        assert len(store.list_devices(user.id)) == 1
        working = [i for i in range(8) if store.device_for_token(f'token-{i}', NOW + 1) is not None]
        assert len(working) == 1

    def test_expired_or_used_code_fails_alike(self, store):
        user = make_user(store)
        store.add_code('good', user.id, None, 'UTC', 2000.0)
        store.add_code('old', user.id, None, 'UTC', 500.0)
        store.add_code('edge', user.id, None, 'UTC', NOW)
        assert store.claim_code('never-made', 'Pi', 't0', NOW) is None
        assert store.claim_code('old', 'Pi', 't1', NOW) is None
        assert store.claim_code('edge', 'Pi', 't1b', NOW) is None            # runs out at the moment it expires
        assert store.claim_code('good', 'Pi', 't2', NOW) is not None
        assert store.claim_code('good', 'Pi', 't3', NOW + 1) is None
        assert [store.device_for_token(t, NOW + 1) is not None for t in ('t0', 't1', 't1b', 't3')] == [False] * 4
        assert len(store.list_devices(user.id)) == 1

    def test_new_code_makes_a_device_with_the_codes_time_zone(self, store):
        user = make_user(store)
        device, token = link(store, user, time_zone='Asia/Tokyo')
        assert (device.user_id, device.name, device.time_zone) == (user.id, 'Pi a', 'Asia/Tokyo')
        assert device.created_at == NOW and device.removed_at is None
        assert store.get_device(device.id) == device
        assert store.device_for_token(token, NOW + 1) == device

    def test_link_again_keeps_device_id_and_replaces_token(self, store):
        user = make_user(store)
        device, old_token = link(store, user, 'first')
        save(store, device, [habit('e1', 10.0)])
        again, new_token = link(store, user, 'second', device_id=device.id, now=2000.0)
        assert again.id == device.id
        assert again.name == device.name                                    # the name the person chose stays
        assert store.device_for_token(old_token, 2001.0) is None
        assert store.device_for_token(new_token, 2001.0) is not None
        assert [e['id'] for e in store.events_after(device.id, 0.0, None, 10)] == ['e1']
        assert len(store.list_devices(user.id)) == 1

    def test_link_again_after_removal_brings_the_device_back(self, store):
        user = make_user(store)
        device, _ = link(store, user)
        store.remove_device(device.id, 1500.0)
        assert store.get_device(device.id).removed_at == 1500.0
        again, token = link(store, user, 'b', device_id=device.id, now=2000.0)
        assert again.removed_at is None
        assert store.device_for_token(token, 2001.0) is not None

    def test_link_again_for_a_device_that_is_gone_fails(self, store):
        user = make_user(store)
        device, _ = link(store, user)
        store.add_code('code-x', user.id, device.id, 'UTC', 2000.0)
        store.delete_device(device.id)
        assert store.claim_code('code-x', 'Pi', 'token-x', NOW) is None
        assert store.device_for_token('token-x', NOW) is None

    def test_removed_device_token_fails(self, store):
        user = make_user(store)
        device, token = link(store, user)
        assert store.device_for_token(token, NOW + 1) is not None
        store.remove_device(device.id, NOW + 2)
        assert store.device_for_token(token, NOW + 3) is None
        assert store.get_device(device.id).removed_at == NOW + 2            # the history is kept
        assert [d.id for d in store.list_devices(user.id)] == [device.id]

    def test_removing_twice_keeps_the_first_time_and_removing_nobody_is_fine(self, store):
        device, _ = link(store, make_user(store))
        store.remove_device(device.id, 1500.0)
        store.remove_device(device.id, 1600.0)
        store.remove_device('missing', 1600.0)
        assert store.get_device(device.id).removed_at == 1500.0

    def test_unknown_token_gives_nothing(self, store):
        assert store.device_for_token('nobody', NOW) is None

    def test_devices_listed_by_owner_and_all(self, store):
        ann, bob = make_user(store, 'ann'), make_user(store, 'bob')
        first, _ = link(store, ann, 'a', now=1000.0)
        second, _ = link(store, ann, 'b', now=2000.0)
        third, _ = link(store, bob, 'c', now=3000.0)
        assert [d.id for d in store.list_devices(ann.id)] == [first.id, second.id]
        assert [d.id for d in store.list_devices(bob.id)] == [third.id]
        assert [d.id for d in store.list_all_devices()] == [first.id, second.id, third.id]

    def test_rename_device(self, store):
        user = make_user(store)
        device, _ = link(store, user)
        assert store.rename_device(device.id, 'Desk').name == 'Desk'
        assert store.get_device(device.id).name == 'Desk'
        assert store.rename_device('missing', 'x') is None

    # ── people and admins ─────────────────────────────────────────────────
    def test_same_google_account_is_the_same_user(self, store):
        first = store.upsert_user('sub-1', 'a@example.com', 'A', NOW)
        again = store.upsert_user('sub-1', 'new@example.com', 'New', LATER)
        assert again.id == first.id and again.created_at == NOW
        assert (again.email, again.name) == ('new@example.com', 'New')
        assert store.get_user(first.id) == again
        other = store.upsert_user('sub-2', 'b@example.com', 'B', LATER)
        assert [u.id for u in store.list_users()] == [first.id, other.id]
        assert store.get_user('missing') is None

    def test_admin_is_made_once(self, store):
        assert store.get_admin('boss') is None
        assert store.create_admin_if_missing('boss', 'hash-1') is True
        assert store.create_admin_if_missing('boss', 'hash-2') is False
        admin = store.get_admin('boss')
        assert (admin.username, admin.password_hash) == ('boss', 'hash-1')

    def test_delete_user_removes_everything(self, store):
        ann, bob = make_user(store, 'ann'), make_user(store, 'bob')
        device, token = link(store, ann, 'a')
        keep, keep_token = link(store, bob, 'b')
        for d in (device, keep):
            save(store, d, [habit('e1', 10.0)], [totals('2026-10-20', 10.0, 100)],
                 [StateChange('cup', 'full', 10.0)])
            store.create_report(d.id, '2026-10-19', NOW)
        store.add_code('spare', ann.id, None, 'UTC', 9999.0)
        assert store.delete_user(ann.id) is True
        assert store.get_user(ann.id) is None
        assert store.list_devices(ann.id) == [] and store.get_device(device.id) is None
        assert store.device_for_token(token, NOW) is None
        assert store.events_after(device.id, 0.0, None, 10) == []
        assert store.totals_between(device.id, '2000-01-01', '2100-01-01') == []
        assert store.object_states(device.id) == []
        assert store.list_reports(device.id) == [] and store.get_report(device.id, '2026-10-19') is None
        assert store.claim_code('spare', 'Pi', 'token-spare', NOW) is None
        assert store.delete_user(ann.id) is False
        # the other person is untouched
        assert store.device_for_token(keep_token, NOW).id == keep.id
        assert len(store.events_after(keep.id, 0.0, None, 10)) == 1
        assert len(store.list_reports(keep.id)) == 1

    def test_delete_device_removes_its_data_but_not_the_person(self, store):
        user = make_user(store)
        device, token = link(store, user)
        save(store, device, [habit('e1', 10.0)], [totals('2026-10-20', 10.0, 1)], [StateChange('cup', 'full', 1.0)])
        store.create_report(device.id, '2026-10-19', NOW)
        assert store.delete_device(device.id) is True
        assert store.get_device(device.id) is None and store.device_for_token(token, NOW) is None
        assert store.events_after(device.id, 0.0, None, 10) == []
        assert store.list_reports(device.id) == []
        assert store.get_user(user.id) is not None
        assert store.delete_device(device.id) is False

    # ── events ────────────────────────────────────────────────────────────
    def test_repeat_events_change_nothing(self, store):
        device, _ = link(store, make_user(store))
        batch = [habit('e1', 10.0), habit('e2', 11.0)]
        assert save(store, device, batch) == (2, 0)
        assert save(store, device, batch, now=LATER) == (0, 2)
        assert [e['id'] for e in store.events_after(device.id, 0.0, None, 10)] == ['e1', 'e2']

    def test_repeat_inside_one_batch_counts_once(self, store):
        device, _ = link(store, make_user(store))
        assert save(store, device, [habit('e1', 10.0), habit('e1', 10.0)]) == (1, 1)

    def test_same_event_id_on_two_devices_is_two_events(self, store):
        user = make_user(store)
        one, _ = link(store, user, 'a')
        two, _ = link(store, user, 'b')
        assert save(store, one, [habit('e1', 10.0)]) == (1, 0)
        assert save(store, two, [habit('e1', 10.0)]) == (1, 0)

    def test_older_event_never_overwrites_newer_state(self, store):
        device, _ = link(store, make_user(store))
        save(store, device, changes=[StateChange('cup', 'full', 100.0), StateChange('phone', 'on_desk', 90.0)],
             now=2000.0)
        save(store, device, changes=[StateChange('cup', 'empty', 50.0)], now=3000.0)
        states = {s.label: s for s in store.object_states(device.id)}
        assert (states['cup'].state, states['cup'].since) == ('full', 100.0)
        assert (states['phone'].state, states['phone'].since) == ('on_desk', 90.0)
        save(store, device, changes=[StateChange('cup', 'empty', 200.0)], now=4000.0)
        newer = {s.label: s for s in store.object_states(device.id)}['cup']
        assert (newer.state, newer.since, newer.updated_at) == ('empty', 200.0, 4000.0)

    def test_same_time_state_keeps_the_first(self, store):
        device, _ = link(store, make_user(store))
        save(store, device, changes=[StateChange('cup', 'full', 100.0)])
        save(store, device, changes=[StateChange('cup', 'empty', 100.0)])
        assert [s.state for s in store.object_states(device.id)] == ['full']

    def test_newer_totals_replace_older(self, store):
        device, _ = link(store, make_user(store))
        assert save(store, device, totals_=[totals('2026-10-20', 100.0, 200)]) == (1, 0)
        assert save(store, device, totals_=[totals('2026-10-20', 200.0, 500)]) == (1, 0)
        assert save(store, device, totals_=[totals('2026-10-20', 150.0, 300)]) == (0, 1)       # older: ignored
        assert save(store, device, totals_=[totals('2026-10-20', 200.0, 500)]) == (0, 1)       # same: a repeat
        save(store, device, totals_=[totals('2026-10-21', 100.0, 1), totals('2026-10-25', 100.0, 2)])
        found = store.totals_between(device.id, '2026-10-20', '2026-10-21')
        assert [(t['day'], t['totals']['water_ml']) for t in found] == [('2026-10-20', 500), ('2026-10-21', 1)]

    def test_totals_are_not_listed_as_events(self, store):
        device, _ = link(store, make_user(store))
        save(store, device, [habit('e1', 10.0)], [totals('2026-10-20', 100.0, 1)])
        assert [e['id'] for e in store.events_after(device.id, 0.0, None, 10)] == ['e1']

    def test_events_after_never_skips_same_time(self, store):
        device, _ = link(store, make_user(store))
        save(store, device, [habit('c', 10.0), habit('a', 10.0), habit('d', 11.0), habit('b', 10.0), habit('z', 5.0)])
        first = store.events_after(device.id, 0.0, None, 3)
        assert [e['id'] for e in first] == ['z', 'a', 'b']
        second = store.events_after(device.id, first[-1]['ts'], first[-1]['id'], 3)
        assert [e['id'] for e in second] == ['c', 'd']
        assert [e['id'] for e in store.events_after(device.id, 10.0, None, 10)] == ['d']      # no after_id: later only

    def test_events_after_other_device_is_empty(self, store):
        user = make_user(store)
        one, _ = link(store, user, 'a')
        two, _ = link(store, user, 'b')
        save(store, one, [habit('e1', 10.0)])
        assert store.events_after(two.id, 0.0, None, 10) == []

    def test_events_between_and_latest(self, store):
        device, _ = link(store, make_user(store))
        camera = {'id': 'cam', 'ts': 30.0, 'type': 'camera', 'key': 'covered', 'kind': 'warn'}
        save(store, device, [habit('e1', 10.0), habit('e2', 20.0), camera, habit('e3', 40.0)])
        assert [e['id'] for e in store.events_between(device.id, 10.0, 40.0)] == ['e1', 'e2', 'cam']
        assert store.latest_event(device.id, 'camera')['id'] == 'cam'
        assert store.latest_event(device.id, 'habit')['id'] == 'e3'
        assert store.latest_event(device.id, 'goals') is None

    def test_returned_events_are_copies(self, store):
        device, _ = link(store, make_user(store))
        save(store, device, [habit('e1', 10.0)])
        store.events_after(device.id, 0.0, None, 10)[0]['ml'] = 999
        assert store.events_after(device.id, 0.0, None, 10)[0]['ml'] == 30

    def test_last_seen_and_caught_up(self, store):
        device, _ = link(store, make_user(store))
        assert (device.last_seen_at, device.caught_up_at) == (None, None)
        save(store, device, [habit('e1', 10.0)], now=2000.0)
        seen = store.get_device(device.id)
        assert (seen.last_seen_at, seen.caught_up_at) == (2000.0, None)
        save(store, device, caught_up=True, now=3000.0)
        done = store.get_device(device.id)
        assert (done.last_seen_at, done.caught_up_at) == (3000.0, 3000.0)
        save(store, device, now=4000.0)
        assert store.get_device(device.id).caught_up_at == 3000.0

    def test_delete_events_before(self, store):
        user = make_user(store)
        one, _ = link(store, user, 'a')
        two, _ = link(store, user, 'b')
        save(store, one, [habit('e1', 10.0), habit('e2', 100.0)])
        save(store, two, [habit('e1', 20.0)])
        assert store.delete_events_before(50.0) == 2
        assert [e['id'] for e in store.events_after(one.id, 0.0, None, 10)] == ['e2']
        assert store.events_after(two.id, 0.0, None, 10) == []
        assert store.delete_events_before(50.0) == 0

    # ── reports ───────────────────────────────────────────────────────────
    def test_one_report_row_per_week(self, store):
        device, _ = link(store, make_user(store))
        first = store.create_report(device.id, '2026-10-19', NOW)
        assert first is not None
        assert (first.status, first.tries, first.next_try_at, first.markdown) == ('pending', 0, None, None)
        assert store.create_report(device.id, '2026-10-19', LATER) is None
        assert store.create_report(device.id, '2026-10-26', LATER) is not None
        assert [r.week_start for r in store.list_reports(device.id)] == ['2026-10-26', '2026-10-19']
        assert store.get_report(device.id, '2026-10-19') == first
        assert store.get_report_by_id(first.id) == first
        assert store.get_report(device.id, '2020-01-06') is None and store.get_report_by_id('missing') is None

    def test_report_goes_through_retry_and_finish(self, store):
        device, _ = link(store, make_user(store))
        report = store.create_report(device.id, '2026-10-19', NOW)
        assert [r.id for r in store.reports_to_write(NOW)] == [report.id]
        retried = store.retry_report(report.id, 'busy', 2000.0)
        assert (retried.status, retried.tries, retried.error, retried.next_try_at) == ('pending', 1, 'busy', 2000.0)
        assert store.reports_to_write(1999.0) == []                          # not yet
        assert [r.id for r in store.reports_to_write(2000.0)] == [report.id]
        done = store.finish_report(report.id, 'done', '# Week', 'model-x', None, 2500.0)
        assert (done.status, done.markdown, done.model, done.error) == ('done', '# Week', 'model-x', None)
        assert store.reports_to_write(LATER) == []
        assert store.list_failed_reports() == []

    def test_failed_report_can_be_reset(self, store):
        device, _ = link(store, make_user(store))
        report = store.create_report(device.id, '2026-10-19', NOW)
        store.retry_report(report.id, 'busy', 2000.0)
        store.finish_report(report.id, 'failed', None, None, 'gave up', 2500.0)
        assert [r.id for r in store.list_failed_reports()] == [report.id]
        reset = store.reset_report(report.id, 3000.0)
        assert (reset.status, reset.tries, reset.error) == ('pending', 0, None)
        assert (reset.next_try_at, reset.markdown) == (None, None)
        assert reset.created_at == 3000.0
        assert store.list_failed_reports() == []
        assert [r.id for r in store.reports_to_write(3000.0)] == [report.id]

    def test_missing_report_gives_none(self, store):
        assert store.retry_report('missing', 'x', 1.0) is None
        assert store.finish_report('missing', 'done', 'x', 'm', None, 1.0) is None
        assert store.reset_report('missing', 1.0) is None
