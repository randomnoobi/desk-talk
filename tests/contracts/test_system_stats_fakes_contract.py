"""The Pi tab's fakes (tests/fakes/system.py) pass the same contracts as the real parts."""

from __future__ import annotations

import pytest

from desktalk.domain import RawReading
from tests.contracts.system_stats_contract import PiStatsLinkContract, SystemStatsContract, SystemStatsReaderContract
from tests.fakes import FakePiStatsLink, FakeStats, FakeStatsReader


class TestFakeStatsReader(SystemStatsReaderContract):
    @pytest.fixture
    def reader(self):
        return FakeStatsReader([RawReading(at=0.0, uptime_sec=5.0)])


class TestFakeStats(SystemStatsContract):
    @pytest.fixture
    def stats(self):
        return FakeStats()


class TestFakePiStatsLink(PiStatsLinkContract):
    @pytest.fixture
    def link(self):
        return FakePiStatsLink()

    @pytest.fixture
    def dead_link(self):
        return FakePiStatsLink(problem='no answer from desktalk.local:5000')


def test_the_fake_reader_repeats_its_last_reading():
    reader = FakeStatsReader([RawReading(at=0.0, uptime_sec=1.0), RawReading(at=0.0, uptime_sec=2.0)])
    assert [reader.read(t).uptime_sec for t in (1.0, 2.0, 3.0)] == [1.0, 2.0, 2.0]
    assert reader.asked == [1.0, 2.0, 3.0]
