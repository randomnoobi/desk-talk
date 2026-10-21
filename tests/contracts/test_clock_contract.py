from __future__ import annotations

import pytest

from tests.contracts.clock_contract import ClockContract
from tests.fakes import FakeClock


class TestFakeClock(ClockContract):
    @pytest.fixture
    def clock(self):
        return FakeClock()


def test_fake_clock_moves_only_when_told():
    clock = FakeClock(start=1_700_000_000.0)
    assert clock.now() == clock.now() == 1_700_000_000.0
    clock.advance(5)
    assert clock.now() == 1_700_000_005.0
    clock.sleep(2)
    assert clock.now() == 1_700_000_007.0
    assert clock.slept == [2]
