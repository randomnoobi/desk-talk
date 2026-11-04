"""The Demo tab's countdowns (desktalk/core/demo/scenarios.py: ScenarioRunner): a pretend warning fires once its
countdown is over, from the background job, and only once."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from desktalk.config import load_config
from desktalk.core.demo import ScenarioRunner
from desktalk.ports import DemoScenarios, PeriodicWorker
from tests.fakes import FakeClock, RecordingFirer

CONFIG = load_config(environ={}, dotenv=Path('/nonexistent'))
DEMO = CONFIG.dev.scenarios
EDGE, SIP = 'cup_at_edge', 'sip'


class Rig:
    def __init__(self, settings=DEMO, broken='', problems=None):
        self.clock = FakeClock()
        self.firer = RecordingFirer(broken, problems)
        self.runner = ScenarioRunner(settings, self.firer, self.clock, words=CONFIG.prompts.dev)

    def tick(self, after: float = 0.0) -> float:
        self.clock.advance(after)
        return self.runner.tick(self.clock.now())


def test_it_is_the_demo_scenarios_and_a_background_job():
    runner = Rig().runner
    assert isinstance(runner, DemoScenarios) and isinstance(runner, PeriodicWorker)
    assert runner.name == 'demo scenarios'


def test_the_list_comes_from_the_settings():
    assert [(s.name, s.label, s.fires) for s in Rig().runner.scenarios()] == [
        (item.name, item.label, item.fires) for item in DEMO.items]


def test_it_checks_every_check_every_sec():
    assert Rig().tick() == DEMO.check_every_sec


def test_nothing_fires_before_the_countdown_is_over():
    rig = Rig()
    rig.runner.start(EDGE, 10)
    rig.tick(9.9)
    assert rig.firer.fired == []
    assert len(rig.runner.waiting()) == 1


def test_it_fires_once_the_countdown_is_over_and_only_once():
    rig = Rig()
    started = rig.runner.start(EDGE, 10)
    rig.tick(10)
    assert rig.firer.fired == [(EDGE, started.fires_at)]
    assert rig.runner.waiting() == []
    rig.tick(10)
    assert len(rig.firer.fired) == 1


def test_a_zero_countdown_fires_on_the_next_round():
    rig = Rig()
    rig.runner.start(SIP, 0)
    rig.tick()
    assert [name for name, _ in rig.firer.fired] == [SIP]


def test_two_due_fire_in_countdown_order():
    rig = Rig()
    rig.runner.start(SIP, 8)
    rig.runner.start(EDGE, 3)
    rig.tick(10)
    assert [name for name, _ in rig.firer.fired] == [EDGE, SIP]


def test_a_cancelled_one_never_fires():
    rig = Rig()
    started = rig.runner.start(EDGE, 5)
    rig.runner.cancel(started.id)
    rig.tick(10)
    assert rig.firer.fired == []
    with pytest.raises(KeyError, match=started.id):
        rig.runner.cancel(started.id)


def test_one_that_went_off_cannot_be_cancelled():
    rig = Rig()
    started = rig.runner.start(EDGE, 1)
    rig.tick(1)
    with pytest.raises(KeyError):
        rig.runner.cancel(started.id)


def test_the_seconds_left_never_go_below_zero():
    rig = Rig()
    rig.runner.start(EDGE, 1)
    rig.clock.advance(5)                    # due, but the job hasn't had its round yet
    assert rig.runner.waiting()[0].seconds_left == 0


def test_only_waiting_max_count_down_at_once():
    rig = Rig(DEMO.model_copy(update={'waiting_max': 2}))
    rig.runner.start(EDGE, 10)
    rig.runner.start(EDGE, 10)
    with pytest.raises(ValueError, match='2 pretend warnings'):
        rig.runner.start(EDGE, 10)
    rig.tick(10)                            # both went off: room again
    rig.runner.start(EDGE, 10)


def test_the_refusals_say_why_in_the_settings_words():
    runner = Rig().runner
    with pytest.raises(ValueError, match="There's no pretend warning called fireworks"):
        runner.start('fireworks', 5)
    with pytest.raises(ValueError, match='from 0 to 600 seconds'):
        runner.start(EDGE, 601)


def test_one_that_fails_is_logged_and_the_others_still_fire(caplog):
    rig = Rig(broken=EDGE)
    rig.runner.start(EDGE, 1)
    rig.runner.start(SIP, 2)
    with caplog.at_level(logging.ERROR):
        rig.tick(5)
    assert [name for name, _ in rig.firer.fired] == [SIP]
    assert 'cup_at_edge' in caplog.text and 'the chat is down' in caplog.text
    assert rig.runner.waiting() == []       # not tried again and again


def test_one_the_firer_cannot_fire_is_refused_with_its_reason():
    rig = Rig(problems={EDGE: "cup_at_edge names unicorn, which isn't an agent"})
    with pytest.raises(ValueError, match='unicorn'):
        rig.runner.start(EDGE, 5)
    assert rig.runner.waiting() == []
