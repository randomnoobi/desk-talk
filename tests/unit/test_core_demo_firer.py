"""The Demo tab's pretend warnings, each sent the way the real one goes out (desktalk/core/demo/firer.py:
DemoFirer). tests/integration/test_demo_scenarios.py follows them through the whole app to the chat and the Mac's
pop-ups."""

from __future__ import annotations

from pathlib import Path

import pytest

from desktalk.config import DemoSettings, load_config
from desktalk.core.demo.firer import DemoFirer
from desktalk.domain import LENS, AgentProfile, Alert
from desktalk.ports import ScenarioFirer
from tests.fakes import FakeProfiles, RecordingCameraWarnings, RecordingHabitLog, RecordingReminders, SyncEventBus

CONFIG = load_config(environ={}, dotenv=Path('/nonexistent'))
PROMPTS = CONFIG.prompts
NOW = 1_790_000_000.0
LOST_NAMES = {item_id: item.name for item_id, item in CONFIG.lost_items.items.items()}
PROFILES = [AgentProfile(id='cup', name='Cup', habits=['hydration']),
            AgentProfile(id='computer', name='Computer', fragile=True),
            AgentProfile(id='paper', name='Paper')]


class Rig:
    def __init__(self, settings=CONFIG.dev.scenarios, profiles=PROFILES):
        self.bus = SyncEventBus()
        self.habits = RecordingHabitLog()
        self.camera = RecordingCameraWarnings()
        self.reminders = RecordingReminders()
        self.firer = DemoFirer(settings, self.bus, self.habits, self.camera, self.reminders, FakeProfiles(profiles),
                               prompts=PROMPTS, lost_item_names=LOST_NAMES)

    def alerts(self) -> list[Alert]:
        return self.bus.of_type(Alert)


def with_items(**changes) -> DemoSettings:
    """The settings' scenarios, with some fields of the first one of each kind changed: with_items(edge_warning=
    {'object': 'paper'})."""
    items = []
    for item in CONFIG.dev.scenarios.items:
        items.append(item.model_copy(update=changes.pop(item.fires, {})))
    return CONFIG.dev.scenarios.model_copy(update={'items': tuple(items)})


def test_it_is_a_scenario_firer():
    assert isinstance(Rig().firer, ScenarioFirer)


def test_the_edge_warning_is_the_proximity_alert_the_desk_monitor_sends():
    rig = Rig()
    rig.firer.fire('cup_at_edge', NOW)
    [alert] = rig.alerts()
    assert (alert.ts, alert.kind, alert.agent) == (NOW, 'proximity', 'cup')
    assert alert.extra['severity'] == 'medium'
    assert alert.extra['rule_label'] == PROMPTS.desk_alerts.edge_reason.format(edge='front', distance=12)
    assert alert.extra['demo'] is True                      # the event log can tell it was pretend


def test_the_danger_zone_warning_is_said_by_the_fragile_one():
    rig = Rig()
    rig.firer.fire('drink_by_laptop', NOW)
    [alert] = rig.alerts()
    assert (alert.kind, alert.agent, alert.extra['near']) == ('proximity', 'computer', 'cup')
    assert (alert.extra['severity'], alert.extra['rule_label']) == ('high', PROMPTS.dev.scenarios.zone)


def test_with_nothing_fragile_the_one_too_close_says_it():
    rig = Rig(with_items(danger_zone={'near': 'paper'}))
    rig.firer.fire('drink_by_laptop', NOW)
    [alert] = rig.alerts()
    assert (alert.agent, alert.extra['near']) == ('cup', 'paper')


def test_the_drink_reminder_goes_through_the_habit_reminders():
    rig = Rig()
    rig.firer.fire('drink_reminder', NOW)
    [(nudge, when)] = rig.reminders.said
    assert when == NOW
    assert (nudge.habit, nudge.key) == ('hydration', 'demo.drink_reminder')
    assert nudge.evidence == PROMPTS.dev.scenarios.drink_evidence.format(minutes=50)
    assert nudge.text == PROMPTS.dev.scenarios.drink_text.format(minutes=50)
    assert rig.alerts() == []


def test_camera_covered_is_said_by_lens_in_its_own_words():
    rig = Rig()
    rig.firer.fire('camera_covered', NOW)
    assert rig.camera.said == [('covered', PROMPTS.camera.problems.covered, NOW)]


def test_the_sip_is_logged_as_one_the_camera_saw():
    rig = Rig()
    rig.firer.fire('sip', NOW)
    assert rig.habits.logged == [('hydration', 'sip', {'how': 'camera'})]


def test_the_lost_object_is_the_alert_lost_items_sends():
    rig = Rig()
    rig.firer.fire('keys_missing', NOW)
    [alert] = rig.alerts()
    assert (alert.kind, alert.scenario, alert.feature, alert.agent) == ('scenario', 'lost_object', 'keys', LENS)
    assert alert.text == PROMPTS.lost_items.alert_plain.format(name='Keys', minutes=20, hint='')
    assert alert.extra['demo'] is True


def test_an_unknown_name_is_a_key_error():
    with pytest.raises(KeyError):
        Rig().firer.fire('fireworks', NOW)


def test_every_scenario_in_the_settings_can_go_off():
    firer = Rig().firer
    assert [firer.problem(item.name) for item in CONFIG.dev.scenarios.items] == [None] * 6


@pytest.mark.parametrize(('changes', 'name', 'named'), [
    ({'edge_warning': {'object': 'unicorn'}}, 'cup_at_edge', 'unicorn'),
    ({'danger_zone': {'near': 'unicorn'}}, 'drink_by_laptop', 'unicorn'),
    ({'lost_object': {'object': 'umbrella'}}, 'keys_missing', 'umbrella')])
def test_a_scenario_naming_something_unknown_has_a_problem_but_the_app_still_starts(changes, name, named):
    """Someone took the keys out of lost_items.items in their own settings: the app must still start, and the Demo
    tab says why that one can't go off."""
    rig = Rig(with_items(**changes))
    problem = rig.firer.problem(name)
    assert problem is not None and named in problem
    with pytest.raises(ValueError, match=named):
        rig.firer.fire(name, NOW)
    assert rig.alerts() == []
