"""The real ScenarioRunner (desktalk/core/demo/scenarios.py) passes the DemoScenarios contract."""

from __future__ import annotations

from pathlib import Path

import pytest

from desktalk.config import load_config
from desktalk.core.demo import ScenarioRunner
from tests.contracts.demo_scenarios_contract import DemoScenariosContract
from tests.fakes import FakeClock

CONFIG = load_config(environ={}, dotenv=Path('/nonexistent'))


class NoFiring:
    def problem(self, name: str) -> str | None:
        return None

    def fire(self, name: str, now: float) -> None:
        raise AssertionError('the contract never lets a countdown run out')


class TestScenarioRunner(DemoScenariosContract):
    @pytest.fixture
    def clock(self):
        return FakeClock()

    @pytest.fixture
    def demo(self, clock):
        return ScenarioRunner(CONFIG.dev.scenarios, NoFiring(), clock, words=CONFIG.prompts.dev)

    @pytest.fixture
    def most_sec(self):
        return CONFIG.dev.scenarios.countdown_max_sec
