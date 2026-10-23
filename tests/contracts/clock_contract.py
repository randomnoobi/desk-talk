"""What every Clock must do. Real and fake clocks both run these tests (see test_clock_contract.py)."""

from __future__ import annotations

import pytest


class ClockContract:
    """Subclass this and give a `clock` fixture."""

    @pytest.fixture
    def clock(self):
        raise NotImplementedError

    def test_now_is_seconds_since_1970(self, clock):
        assert clock.now() > 1_600_000_000

    def test_sleep_moves_now_and_monotonic_forward(self, clock):
        wall, mono = clock.now(), clock.monotonic()
        clock.sleep(0.02)
        assert clock.now() - wall >= 0.019
        assert clock.monotonic() - mono >= 0.019

    def test_monotonic_never_goes_back(self, clock):
        seen = [clock.monotonic() for _ in range(50)]
        assert seen == sorted(seen)
