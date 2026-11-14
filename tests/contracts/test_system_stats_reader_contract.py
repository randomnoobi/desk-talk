"""The real reader of Linux's files (desktalk/adapters/system/proc_reader.py: ProcReader) passes the same contract
as its fake, on a folder that looks like the Pi's /proc and /sys and on an empty one (like a Mac)."""

from __future__ import annotations

import pytest

from tests.contracts.system_stats_contract import SystemStatsReaderContract
from tests.unit.test_adapters_system_proc_reader import make_pi, reader_for


class TestProcReaderOnAPi(SystemStatsReaderContract):
    @pytest.fixture
    def reader(self, tmp_path):
        return reader_for(make_pi(tmp_path))


class TestProcReaderWithNothingToRead(SystemStatsReaderContract):
    @pytest.fixture
    def reader(self, tmp_path):
        return reader_for(tmp_path)
