"""What every Clock must do. Real and fake clocks both run these tests (see test_clock_contract.py)."""

from __future__ import annotations

import re

import pytest

HOUR = 3600
SOME_DAY = 1_760_000_000.0        # 2025-10-09: no clock change (summer time) near it in most places


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

    def test_local_day_is_year_month_day(self, clock):
        assert re.fullmatch(r'\d{4}-\d{2}-\d{2}', clock.local_day(SOME_DAY))

    def test_local_hour_goes_round_the_whole_day(self, clock):
        hours = [clock.local_hour(SOME_DAY + i * HOUR) for i in range(24)]
        assert all(isinstance(h, int) and 0 <= h <= 23 for h in hours)
        assert len(set(hours)) >= 23           # 23 on the day the clocks go forward

    def test_the_day_changes_when_the_hour_goes_back_to_0(self, clock):
        ts = SOME_DAY
        while clock.local_hour(ts + HOUR) != 0:
            assert clock.local_day(ts + HOUR) == clock.local_day(ts)
            ts += HOUR
        assert clock.local_day(ts + HOUR) > clock.local_day(ts)
