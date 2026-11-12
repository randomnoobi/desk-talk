"""Breaker: stops calls to something that keeps failing, then lets one call check whether it's back."""

from __future__ import annotations

from desktalk_shared.backoff import Backoff
from desktalk_shared.breaker import Breaker


def breaker(failures: int = 3, first: float = 60, most: float = 60) -> Breaker:
    return Breaker(failures, Backoff(first, most))


def test_lets_calls_in_until_enough_fail_in_a_row():
    b = breaker()
    assert b.let_in(0)
    assert not b.failed(0) and not b.failed(1)
    assert b.failed(2)                                  # the third failure opens it
    assert b.is_open(2) and not b.let_in(30)
    assert b.seconds_left(30) == 32


def test_one_call_checks_after_the_pause_and_the_rest_wait():
    b = breaker()
    for t in range(3):
        b.failed(t)
    assert b.let_in(62)                                 # the check
    assert not b.let_in(62) and b.is_open(62)           # others wait while it checks


def test_a_check_that_works_closes_it():
    b = breaker()
    for t in range(3):
        b.failed(t)
    b.let_in(62)
    b.worked()
    assert not b.is_open(62) and b.let_in(62) and b.failures == 0


def test_a_check_that_fails_opens_it_again_at_once():
    b = breaker()
    for t in range(3):
        b.failed(t)
    b.let_in(62)
    assert b.failed(63)
    assert b.open_until == 123


def test_a_growing_pause_doubles_each_time_it_opens_and_a_call_that_works_starts_it_over():
    b = breaker(failures=1, first=5, most=20)
    assert b.failed(0) and b.open_until == 5
    assert b.failed(5) and b.open_until == 15
    assert b.failed(15) and b.open_until == 35
    assert b.failed(35) and b.open_until == 55          # the most is 20
    b.worked()
    assert b.failed(100) and b.open_until == 105


def test_the_pause_is_how_long_it_opened_for_last():
    b = breaker(failures=1, first=5, most=20)
    b.failed(0)
    b.failed(5)
    assert b.pause == 10


def test_back_without_a_call_closes_it_but_keeps_the_pause_length():
    b = breaker(failures=2, first=5, most=60)
    b.failed(0)
    b.failed(0)                                         # open for 5
    b.back()                                            # e.g. a health check says it's ready
    assert not b.is_open(1) and b.failures == 0 and b.let_in(1)
    assert not b.failed(2)                              # it takes `failures` new failures to open again ...
    assert b.failed(3) and b.open_until == 13           # ... and then the pause is longer: 10, not 5


def test_seconds_left_is_never_less_than_1():
    b = breaker(failures=1, first=0.2, most=0.2)
    b.failed(0)
    assert b.seconds_left(0.1) == 1
    assert b.seconds_left(5) == 1
