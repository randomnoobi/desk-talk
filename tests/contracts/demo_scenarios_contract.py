"""What every DemoScenarios must do (desktalk/ports.py): the developer page's Demo tab, pretend warnings counting
down. The real ScenarioRunner runs these tests (test_demo_scenarios_contract.py)."""

from __future__ import annotations

import math

import pytest

from desktalk.domain import Scenario, WaitingScenario


class DemoScenariosContract:
    """Subclass this and give three fixtures:

    - `demo`: a DemoScenarios with at least two scenarios, nothing counting down, and room for at least two;
    - `clock`: the FakeClock it reads;
    - `most_sec`: the longest countdown it takes.
    """

    def test_scenarios_are_listed(self, demo):
        listed = demo.scenarios()
        assert len(listed) >= 2 and all(isinstance(s, Scenario) for s in listed)
        assert len({s.name for s in listed}) == len(listed)

    def test_the_list_is_a_copy(self, demo):
        demo.scenarios().clear()
        assert len(demo.scenarios()) >= 2

    def test_nothing_waits_at_first(self, demo):
        assert demo.waiting() == []

    def test_a_started_one_waits_with_its_seconds(self, demo, clock):
        first = demo.scenarios()[0]
        started = demo.start(first.name, 30)
        assert isinstance(started, WaitingScenario)
        assert (started.name, started.label, started.seconds_left) == (first.name, first.label, 30)
        assert started.fires_at == pytest.approx(clock.now() + 30)
        assert demo.waiting() == [started]

    def test_the_seconds_left_go_down(self, demo, clock):
        demo.start(demo.scenarios()[0].name, 30)
        clock.advance(10)
        assert demo.waiting()[0].seconds_left == pytest.approx(20)

    def test_each_start_has_its_own_id(self, demo):
        name = demo.scenarios()[0].name
        assert demo.start(name, 10).id != demo.start(name, 10).id

    def test_the_soonest_comes_first(self, demo):
        one, two = (s.name for s in demo.scenarios()[:2])
        later = demo.start(one, 60)
        sooner = demo.start(two, 5)
        assert [w.id for w in demo.waiting()] == [sooner.id, later.id]

    def test_zero_and_the_most_are_allowed(self, demo, most_sec):
        name = demo.scenarios()[0].name
        assert demo.start(name, 0).seconds_left == 0
        assert demo.start(name, most_sec).seconds_left == most_sec

    def test_an_unknown_name_is_refused(self, demo):
        with pytest.raises(ValueError, match='nope'):
            demo.start('nope', 10)
        assert demo.waiting() == []

    @pytest.mark.parametrize('bad', [-1, math.nan, math.inf])
    def test_a_countdown_out_of_range_is_refused(self, demo, bad):
        with pytest.raises(ValueError):
            demo.start(demo.scenarios()[0].name, bad)
        assert demo.waiting() == []

    def test_one_past_the_most_is_refused(self, demo, most_sec):
        with pytest.raises(ValueError):
            demo.start(demo.scenarios()[0].name, most_sec + 1)

    def test_a_cancelled_one_stops_waiting(self, demo):
        name = demo.scenarios()[0].name
        gone, kept = demo.start(name, 10), demo.start(name, 20)
        demo.cancel(gone.id)
        assert [w.id for w in demo.waiting()] == [kept.id]

    def test_cancelling_one_not_waiting_is_a_key_error(self, demo):
        with pytest.raises(KeyError):
            demo.cancel('demo-404')
