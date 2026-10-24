"""Backoff: the wait after each failure in a row, doubling up to a most (shared by the Pi and this computer)."""

from __future__ import annotations

from desktalk_shared.backoff import Backoff


def test_backoff_doubles_up_to_most_and_resets():
    backoff = Backoff(first=1, most=10)
    assert [backoff.next() for _ in range(6)] == [1, 2, 4, 8, 10, 10]
    backoff.reset()
    assert backoff.next() == 1


def test_the_first_wait_is_never_more_than_the_most():
    assert Backoff(first=30, most=5).next() == 5


def test_no_first_wait_stays_at_no_wait():
    backoff = Backoff(first=0, most=0)
    assert [backoff.next() for _ in range(3)] == [0, 0, 0]


def test_it_never_overflows_however_long_it_keeps_failing():
    backoff = Backoff(first=1, most=60)
    for _ in range(5000):
        wait = backoff.next()
    assert wait == 60
