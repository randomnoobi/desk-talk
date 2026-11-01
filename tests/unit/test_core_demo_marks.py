"""Everything the Demo tab makes is marked `demo`, so nothing it fires is ever sent to the cloud server (the sip
and the camera warning used to go out unmarked)."""

from __future__ import annotations

import pytest

from desktalk.config import load_config
from desktalk_shared.cloud_api import is_demo, to_cloud_event
from tests.fakes import RecordingCameraWarnings, RecordingHabitLog
from tests.integration.app_kit import DeskRig

SENDABLE = {'object_state', 'habit', 'nudge', 'rule_outcome', 'alert', 'camera'}
SCENARIOS = [item.name for item in load_config(environ={}).dev.scenarios.items]
AI_LINE = 'Hey! All good over here on the desk.'


@pytest.fixture
def rig(tmp_path):
    return DeskRig(tmp_path, ai_reply=AI_LINE)


def day_events(rig: DeskRig) -> list[dict]:
    return rig.app.event_log.events(rig.clock.local_day(rig.clock.now()), None, None)


def fire(rig: DeskRig, name: str) -> list[dict]:
    assert rig.app.demo is not None
    rig.app.demo.start(name, 1)
    rig.tick(rig.app.demo, after=2)
    return [e for e in day_events(rig) if e['type'] in SENDABLE]


@pytest.mark.parametrize('name', SCENARIOS)
def test_every_event_a_scenario_makes_is_demo_and_never_sent(rig, name):
    events = fire(rig, name)
    assert events, f'{name} made nothing the cloud filter looks at'
    assert all(is_demo(e) for e in events), events
    assert all(to_cloud_event(e) is None for e in events)


def test_the_real_thing_is_still_sent(rig):
    """The same sip, logged for real, is not marked."""
    rig.app.habits.log('hydration', 'sip', how='camera')
    [sip] = [e for e in day_events(rig) if e['type'] == 'habit']
    assert not is_demo(sip) and to_cloud_event(sip) is not None


def test_a_logged_sip_marked_demo_carries_the_mark_into_the_event(rig):
    rig.app.habits.log('hydration', 'sip', how='camera', demo=True)
    [sip] = [e for e in day_events(rig) if e['type'] == 'habit']
    assert sip['demo'] is True and sip['how'] == 'camera' and is_demo(sip)


def test_a_camera_warning_said_for_the_demo_is_marked_demo(rig):
    rig.app.lens.say_problem('covered', 'Everything just went black.', rig.clock.now(), demo=True)
    [notice] = [e for e in day_events(rig) if e['type'] == 'camera']
    assert notice['demo'] is True and notice['key'] == 'covered'


def test_a_real_camera_warning_is_not_marked(rig):
    rig.app.lens.say_problem('covered', 'Everything just went black.', rig.clock.now())
    [notice] = [e for e in day_events(rig) if e['type'] == 'camera']
    assert not is_demo(notice)


def test_the_stand_ins_take_the_mark_too():
    habits, camera = RecordingHabitLog(), RecordingCameraWarnings()
    habits.log('hydration', 'sip', how='camera', demo=True)
    camera.say_problem('covered', 'x', 1.0, demo=True)
    assert habits.logged == [('hydration', 'sip', {'how': 'camera', 'demo': True})]
    assert camera.said_for_demo == [True]
