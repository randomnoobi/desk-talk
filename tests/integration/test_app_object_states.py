"""
Object states in the whole app built by bootstrap: the tracker follows the pictures, saves to its SQLite file,
picks up where it left off after a restart, and the person's state switches reminders on and off.
"""

from __future__ import annotations

from tests.integration.app_kit import CUP_FAR, LAPTOP, DeskRig, changed, desk_config


def only_breaks(config, *, warmup_min=2, sit_limit_min=3):
    """Settings where only the breaks rule is on, with a short warm-up and sitting limit (so tests run quickly)."""
    for rule in ('hydration', 'light', 'phone_free', 'food', 'tasks'):
        config = changed(config, f'protocols.{rule}', enabled=False)
    config = changed(config, 'protocols.breaks', sit_limit_min=sit_limit_min)
    return changed(config, 'reminders', warmup_min=warmup_min)


def settle_sec(rig):
    """Long enough for an object to appear or go away (object_states.appear_sec / gone_sec), with room to spare."""
    states = rig.config.object_states
    return max(states.appear_sec, states.gone_sec) + 2


def cup_changes(rig):
    """The cup's object_state events in the event log, oldest first."""
    day = rig.app.event_log.days()[0]
    events = reversed(rig.app.event_log.events(day, {'object_state'}, 100))
    return [e['change'] for e in events if e['object'] == 'cup']


def test_object_states_follow_the_pictures(tmp_path):
    rig = DeskRig(tmp_path)
    rig.watch(settle_sec(rig), CUP_FAR)
    rig.watch(settle_sec(rig))
    assert cup_changes(rig) == ['appeared', 'removed']
    assert rig.app.object_states.snapshot().objects['cup'].state == 'ABSENT'
    newest = [row for row in rig.app.state_store.changes(0) if row.label == 'cup'][-1]
    assert newest.new_state == 'ABSENT'


def test_no_reminders_when_you_are_away(tmp_path):
    # The switch turns off quickly here, so you're away for it while the habit tracker still counts you as
    # sitting (presence.user_active_window_sec): only the switch keeps the break nudge back.
    config = only_breaks(desk_config(tmp_path), warmup_min=2, sit_limit_min=3)
    rig = DeskRig(tmp_path, config=changed(config, 'object_states', person_gone_sec=10))
    away = 2 * rig.config.object_states.person_gone_sec
    assert away < rig.config.presence.user_active_window_sec
    rig.watch(4 * 60, LAPTOP, step=1)
    rig.watch(away, LAPTOP, person=False, step=1)
    rig.messages()
    assert not rig.app.desk_status.user_active(rig.clock.now())
    rig.tick(rig.app.reminders)
    assert [m for m in rig.messages() if m['type'] == 'group_message'] == []
    rig.watch(rig.config.object_states.person_appear_sec + 2, LAPTOP)
    assert rig.app.desk_status.user_active(rig.clock.now())


def test_a_restart_keeps_the_states(tmp_path):
    rig = DeskRig(tmp_path)
    rig.watch(settle_sec(rig), CUP_FAR)
    rig.app.stop(timeout=1)
    rig2 = DeskRig(tmp_path, start=rig.clock.now() + 10)
    assert rig2.app.object_states.snapshot().objects['cup'].state == 'PRESENT'      # before any picture
    rig2.watch(settle_sec(rig2), CUP_FAR)
    appeared = [row for row in rig2.app.state_store.changes(0) if row.label == 'cup' and row.type == 'appeared']
    assert len(appeared) == 1                                                      # only the first run's
    rig2.app.stop(timeout=1)


def test_object_states_are_saved_every_minute(tmp_path):
    rig = DeskRig(tmp_path)
    [job] = [j for j in rig.app.scheduler.status() if j['name'] == 'save object states']
    assert job['every_sec'] == rig.config.object_states.snapshot_every_sec
