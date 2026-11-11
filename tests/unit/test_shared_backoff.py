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


# ── jitter: each wait a little shorter, by chance, so many retries don't all land at once ──
class Dice:
    """A stand-in for random.Random: random() gives these numbers in turn (the last one repeats)."""

    def __init__(self, *numbers: float):
        self.numbers = list(numbers)

    def random(self) -> float:
        return self.numbers.pop(0) if len(self.numbers) > 1 else self.numbers[0]


def test_jitter_takes_up_to_that_share_off_each_wait():
    backoff = Backoff(first=10, most=100, jitter=0.2, rng=Dice(0.0, 0.5, 1.0))
    assert [backoff.next() for _ in range(3)] == [10, 20 * 0.9, 40 * 0.8]


def test_jitter_never_makes_the_doubling_lose_its_place():
    backoff = Backoff(first=1, most=8, jitter=0.5, rng=Dice(1.0))
    assert [backoff.next() for _ in range(5)] == [0.5, 1, 2, 4, 4]      # half of 1, 2, 4, 8, 8


def test_jittered_waits_stay_between_the_shortest_and_the_cap():
    import random
    backoff = Backoff(first=1, most=60, jitter=0.3, rng=random.Random(7))
    plain = Backoff(first=1, most=60)
    for _ in range(200):
        wait, full = backoff.next(), plain.next()
        assert full * 0.7 <= wait <= full <= 60


def test_no_jitter_is_exactly_the_plain_doubling():
    backoff = Backoff(first=1, most=10, jitter=0, rng=Dice(0.9))
    assert [backoff.next() for _ in range(6)] == [1, 2, 4, 8, 10, 10]


def test_a_jitter_outside_0_to_1_is_refused():
    import pytest
    for bad in (-0.1, 1.5):
        with pytest.raises(ValueError, match='jitter'):
            Backoff(first=1, most=10, jitter=bad)


def test_jittered_on_its_own_for_waits_that_dont_double():
    from desktalk_shared.backoff import jittered
    assert jittered(100, 0.25, Dice(0.4)) == 90
    assert jittered(100, 0, Dice(0.4)) == 100
