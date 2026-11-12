"""The real SystemStats (StatsHistory, desktalk/core/system/) passes the same contract as FakeStats."""

from __future__ import annotations

from pathlib import Path

import pytest

from desktalk.config import load_config
from desktalk.core.system.stats_history import StatsHistory
from tests.contracts.system_stats_contract import SystemStatsContract
from tests.fakes import FakeStatsReader


class TestStatsHistory(SystemStatsContract):
    @pytest.fixture
    def stats(self):
        settings = load_config(environ={}, dotenv=Path('/nonexistent')).system_stats.model_copy(
            update={'enabled': True})
        return StatsHistory(FakeStatsReader(), settings)
