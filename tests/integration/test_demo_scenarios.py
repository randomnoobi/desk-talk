"""The Demo tab's pretend warnings through the whole app: each one, once its countdown is over, reaches the open
app page and the Mac's pop-ups the way the real warning does (desktalk/core/demo/)."""

from __future__ import annotations

import pytest

from desktalk.domain import LENS
from desktalk.ports import DemoCountdowns
from tests.integration.app_kit import DeskRig, changed, desk_config

AI_LINE = 'Hey! All good over here on the desk.'      # what the fake AI says (app_kit.chatty_ai)


@pytest.fixture
def rig(tmp_path):
    return DeskRig(tmp_path, ai_reply=AI_LINE)


def demo(rig: DeskRig) -> DemoCountdowns:
    """The app's Demo tab countdowns (there when the developer page is on, as it is in git)."""
    assert rig.app.demo is not None
    return rig.app.demo


def fire(rig: DeskRig, name: str, after: float = 3) -> list[dict]:
    """Start `name` with a countdown of `after` seconds, let the background job run once it's over, and give
    what the open app page got."""
    demo(rig).start(name, after)
    rig.tick(rig.app.demo, after=after - 0.5)
    assert rig.messages() == [], 'nothing before the countdown is over'
    rig.tick(rig.app.demo, after=0.5)
    return rig.messages()


def chat(messages: list[dict]) -> list[dict]:
    return [m for m in messages if m.get('type') == 'group_message']


def test_the_runner_is_a_background_job_when_the_developer_page_is_on(rig):
    assert rig.app.demo is not None
    assert rig.app.demo in [job for job, _ in rig.app.background_jobs()]


def test_no_demo_without_the_developer_page(tmp_path):
    rig = DeskRig(tmp_path, config=changed(desk_config(tmp_path), 'dev', enabled=False))
    assert rig.app.demo is None
    assert all(job.name != 'demo scenarios' for job, _ in rig.app.background_jobs())


def test_the_edge_warning_is_said_by_the_cup_and_pops_up(rig):
    [said] = chat(fire(rig, 'cup_at_edge'))
    assert (said['character'], said['alert']) == ('cup', 'proximity')
    assert rig.desktop.shown == [('Cup', said['text'])]
    [alert] = rig.app.event_log.events(rig.clock.local_day(rig.clock.now()), {'alert'}, None)
    assert alert['demo'] is True and alert['agent'] == 'cup'


def test_the_drink_by_the_laptop_is_said_by_the_computer(rig):
    [said] = chat(fire(rig, 'drink_by_laptop'))
    assert (said['character'], said['alert']) == ('computer', 'proximity')
    assert rig.desktop.shown == [('Computer', said['text'])]


def test_the_drink_reminder_is_a_reminder_pop_up(rig):
    [said] = chat(fire(rig, 'drink_reminder'))
    assert said['habit'] == 'hydration' and said['toast'] is True
    assert said['character'] == LENS                 # no cup in the chat yet: Lens says the nudge's own words
    assert '50 minutes' in said['text']
    assert rig.desktop.shown == [(rig.config.prompts.lens.card.name, said['text'])]


def test_camera_covered_is_lens_s_warning(rig):
    [said] = chat(fire(rig, 'camera_covered'))
    covered = rig.config.prompts.camera.problems.covered
    assert (said['character'], said['lens'], said['text']) == (LENS, 'problem', covered)
    assert rig.desktop.shown == [(rig.config.prompts.lens.card.name, said['text'])]


def test_the_sip_counts_and_pops_up_as_a_sip(rig):
    before = rig.app.habits.today(rig.clock.now())['hydration']['sips']
    messages = fire(rig, 'sip')
    assert rig.app.habits.today(rig.clock.now())['hydration']['sips'] == before + 1
    [said] = chat(messages)
    assert said['habit'] == 'hydration'
    assert rig.desktop.shown == [(rig.config.prompts.notifications.sip_title, said['text'])]


def test_the_lost_keys_are_lens_s_warning(rig):
    [said] = chat(fire(rig, 'keys_missing'))
    assert (said['character'], said['alert']) == (LENS, 'keys')
    assert 'Keys' in said['text'] and '20 min' in said['text']
    assert len(rig.desktop.shown) == 1


def test_a_cancelled_one_says_nothing(rig):
    waiting = demo(rig).start('cup_at_edge', 2)
    demo(rig).cancel(waiting.id)
    rig.tick(rig.app.demo, after=5)
    assert chat(rig.messages()) == [] and rig.desktop.shown == []
