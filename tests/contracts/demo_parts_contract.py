"""What the parts behind the Demo tab's pretend warnings must do (desktalk/ports.py): ScenarioFirer, CameraWarnings
and ReminderSender. The real ones (DemoFirer, Lens, HabitReminders) and the recording stand-ins in
tests/fakes/demo.py run these tests (test_demo_parts_contracts.py)."""

from __future__ import annotations

import pytest

from desktalk.domain import Nudge
from desktalk.ports import CameraWarnings, ReminderSender, ScenarioFirer

NOW = 1_790_000_000.0
DRINK = Nudge('hydration', 'demo.drink_reminder', evidence='No sip for 50 minutes.', text='Time for some water?',
              repeat_after_sec=0)


class ScenarioFirerContract:
    """Subclass this and give four fixtures:

    - `firer`: a ScenarioFirer;
    - `ready_name`: a scenario it can fire;
    - `blocked_name`: a scenario it knows but can't fire with the settings as they are;
    - `sent`: a function that says how many things it has sent so far (warnings, sips, reminders ...).
    """

    def test_it_is_a_scenario_firer(self, firer):
        assert isinstance(firer, ScenarioFirer)

    def test_one_that_can_go_off_has_no_problem(self, firer, ready_name):
        assert firer.problem(ready_name) is None

    def test_one_that_cant_go_off_says_why_in_words(self, firer, blocked_name):
        why = firer.problem(blocked_name)
        assert isinstance(why, str) and why.strip()

    def test_firing_one_sends_it(self, firer, ready_name, sent):
        before = sent()
        firer.fire(ready_name, NOW)
        assert sent() > before

    def test_firing_one_with_a_problem_is_refused_with_its_reason_and_sends_nothing(self, firer, blocked_name, sent):
        before = sent()
        with pytest.raises(ValueError) as refused:
            firer.fire(blocked_name, NOW)
        assert str(refused.value) == firer.problem(blocked_name)
        assert sent() == before


class CameraWarningsContract:
    """Subclass this and give two fixtures: `warnings`, a CameraWarnings that has said nothing yet, and `heard`, a
    function that gives the texts it has said so far, in order."""

    def test_it_is_camera_warnings(self, warnings):
        assert isinstance(warnings, CameraWarnings)

    def test_a_problem_is_said_now(self, warnings, heard):
        warnings.say_problem('covered', 'Everything just went black.', NOW)
        assert heard() == ['Everything just went black.']

    def test_each_one_is_said_even_right_after_another(self, warnings, heard):
        warnings.say_problem('covered', 'Everything just went black.', NOW)
        warnings.say_problem('dark', 'It got dark.', NOW)
        assert heard() == ['Everything just went black.', 'It got dark.']


class ReminderSenderContract:
    """Subclass this and give two fixtures: `sender`, a ReminderSender that has said nothing yet (where nobody puts
    a nudge in their own words, so its own text is said), and `heard`, a function that gives (habit, text) for
    each nudge it has said so far, in order."""

    def test_it_is_a_reminder_sender(self, sender):
        assert isinstance(sender, ReminderSender)

    def test_a_nudge_is_said_now(self, sender, heard):
        sender.say(DRINK, NOW)
        assert heard() == [('hydration', DRINK.text)]

    def test_nothing_waits_because_of_it(self, sender, heard):
        sender.say(DRINK, NOW)
        sender.say(DRINK, NOW + 1)
        assert heard() == [('hydration', DRINK.text)] * 2
