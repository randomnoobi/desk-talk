"""desktalk/domain/cloud_link.py: the one check of a saved cloud link that the sender, the Pi's hello and its link
offers all use, so they never disagree about whether the Pi is linked."""

from __future__ import annotations

import pytest

from desktalk.domain.cloud_link import is_active_link, is_usable_link, is_well_formed_link, link_record
from tests.fakes import FakeClock

NOW = 1_760_000_000.0
DAY_OF = FakeClock(NOW).local_day


def link(**changes) -> dict:
    return {'server': 'http://192.168.86.30:8080', 'device_id': 'dev-1', 'token': 'a-token', 'stopped': False,
            'place_ts': NOW, 'start_ts': NOW, 'recent_ids': [], **changes}


DAMAGED = [
    {'recent_ids': 'oops'}, {'recent_ids': [1, 2]}, {'server': 'ftp://x'}, {'server': 'http://'},
    {'server': 'http://[bad'}, {'server': ' http://192.168.86.30:8080'}, {'place_ts': float('nan')},
    {'place_ts': float('inf')}, {'place_ts': 'soon'}, {'start_ts': float('nan')}, {'last_sent_ts': True},
    {'token': ''}, {'token': 'abc def'}, {'token': 'secret\r\nX-Evil: 1'}, {'device_id': None}, {'device_id': ''},
    {'stopped': 'maybe'}, {'server': None}]


def test_a_link_as_saved_is_usable_and_active():
    assert is_well_formed_link(link()) and is_usable_link(link(), DAY_OF) and is_active_link(link(), DAY_OF)


@pytest.mark.parametrize('changes', DAMAGED, ids=lambda c: next(iter(c)) + '=' + repr(next(iter(c.values())))[:12])
def test_a_damaged_link_is_neither_usable_nor_active(changes):
    assert not is_well_formed_link(link(**changes))
    assert not is_usable_link(link(**changes), DAY_OF) and not is_active_link(link(**changes), DAY_OF)


@pytest.mark.parametrize('saved', [None, ['not', 'a', 'link'], 'link', 5])
def test_something_that_is_not_a_link_is_not_one(saved):
    assert not is_usable_link(saved, DAY_OF) and not is_active_link(saved, DAY_OF)


@pytest.mark.parametrize('name', ['place_ts', 'start_ts', 'last_sent_ts'])
@pytest.mark.parametrize('when', [1e300, -1e300, 10 ** 400])
def test_a_time_the_clock_cannot_turn_into_a_day_is_not_usable(name, when):
    assert not is_usable_link(link(**{name: when}), DAY_OF) and not is_active_link(link(**{name: when}), DAY_OF)


def test_a_clock_that_gives_no_day_name_is_not_usable():
    assert not is_usable_link(link(), lambda ts: 'not a day')


def test_a_stopped_link_is_usable_but_not_active():
    assert is_usable_link(link(stopped=True), DAY_OF) and not is_active_link(link(stopped=True), DAY_OF)


def test_the_times_and_ids_may_be_missing():
    bare = {'server': 'https://cloud.test', 'device_id': 'dev-1', 'token': 'a-token'}
    assert is_usable_link(bare, DAY_OF) and is_active_link(bare, DAY_OF)


@pytest.mark.parametrize('name', ['place_ts', 'start_ts'])
def test_linking_the_same_device_again_drops_a_time_the_clock_cannot_use(name):
    """Else the new link would be damaged too, and the sender would never use it."""
    record = link_record('http://192.168.86.30:8080', 'dev-1', 'new-token', link(**{name: 1e300}), NOW + 60,
                         DAY_OF)
    assert is_active_link(record, DAY_OF)
    assert record['place_ts'] == (NOW + 60 if name == 'place_ts' else NOW)


def test_linking_the_same_device_again_keeps_its_good_place():
    record = link_record('http://192.168.86.30:8080', 'dev-1', 'new-token', link(place_ts=NOW - 5, start_ts=NOW - 9,
                         recent_ids=['a']), NOW + 60, DAY_OF)
    assert (record['place_ts'], record['start_ts'], record['recent_ids']) == (NOW - 5, NOW - 9, ['a'])
