"""
Object states in the whole app built by bootstrap: the tracker follows the pictures, saves to its SQLite file,
picks up where it left off after a restart, and the person's state switches reminders on and off.
"""

from __future__ import annotations

from datetime import date

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


def test_a_restart_before_the_clock_has_synced_starts_over_with_no_boot_id(tmp_path):
    """The Pi's clock may be days behind until it reaches the internet. A computer that can't say which boot it is
    in can't tell a restart from a power cut, so nothing saved is trusted before then."""
    rig = DeskRig(tmp_path)
    rig.watch(settle_sec(rig), CUP_FAR)
    rig.app.stop(timeout=1)
    config = changed(rig.config, 'object_states', clock_synced_file=tmp_path / 'not-synced-yet')
    rig2 = DeskRig(tmp_path, config=config, start=rig.clock.now() + 10)
    assert 'cup' not in rig2.app.object_states.snapshot().objects
    rig2.app.stop(timeout=1)


def boot(tmp_path, name):
    """A boot id file saying the computer is in boot `name`."""
    file = tmp_path / 'boot_id'
    file.write_text(f'{name}\n')
    return file


def test_a_restart_in_the_same_boot_keeps_the_states_before_the_clock_has_synced(tmp_path):
    """A Pi with no internet: only the app restarted, so its clock ran on and what it saved is still good."""
    config = changed(desk_config(tmp_path), 'object_states', clock_synced_file=tmp_path / 'not-synced-yet',
                     boot_id_file=boot(tmp_path, 'boot-a'))
    rig = DeskRig(tmp_path, config=config)
    rig.watch(settle_sec(rig), CUP_FAR)
    rig.app.stop(timeout=1)
    rig2 = DeskRig(tmp_path, config=config, start=rig.clock.now() + 10)
    assert rig2.app.object_states.snapshot().objects['cup'].state == 'PRESENT'
    rig2.app.stop(timeout=1)


def test_a_new_boot_starts_over_before_the_clock_has_synced(tmp_path):
    """A power cut: the clock may be days behind, so what the boot before saved isn't trusted."""
    config = changed(desk_config(tmp_path), 'object_states', clock_synced_file=tmp_path / 'not-synced-yet',
                     boot_id_file=boot(tmp_path, 'boot-a'))
    rig = DeskRig(tmp_path, config=config)
    rig.watch(settle_sec(rig), CUP_FAR)
    rig.app.stop(timeout=1)
    boot(tmp_path, 'boot-b')
    rig2 = DeskRig(tmp_path, config=config, start=rig.clock.now() + 10)
    assert 'cup' not in rig2.app.object_states.snapshot().objects
    rig2.app.stop(timeout=1)


def test_a_restart_after_the_clock_has_synced_keeps_the_states(tmp_path):
    rig = DeskRig(tmp_path)
    rig.watch(settle_sec(rig), CUP_FAR)
    rig.app.stop(timeout=1)
    flag = tmp_path / 'synchronized'
    flag.touch()
    rig2 = DeskRig(tmp_path, config=changed(rig.config, 'object_states', clock_synced_file=flag),
                   start=rig.clock.now() + 10)
    assert rig2.app.object_states.snapshot().objects['cup'].state == 'PRESENT'
    rig2.app.stop(timeout=1)


def test_saved_rows_say_the_clock_was_synced(tmp_path):
    rig = DeskRig(tmp_path)
    rig.watch(settle_sec(rig), CUP_FAR)
    assert {row.clock_synced for row in rig.app.state_store.changes(0)} == {True}
    rig.app.stop(timeout=1)


def test_a_restart_with_a_clock_that_is_plainly_wrong_starts_over(tmp_path):
    """The clock set to 1990 by hand: before clock.earliest_believable_date, it counts as not synced, flag file or no
    flag file, and the rows written meanwhile say so."""
    rig = DeskRig(tmp_path)
    rig.watch(settle_sec(rig), CUP_FAR)
    rig.app.stop(timeout=1)
    start = rig.clock.now() + 10
    after_start = date.fromtimestamp(start + 2 * 24 * 3600)
    rig2 = DeskRig(tmp_path, config=changed(rig.config, 'clock', earliest_believable_date=after_start), start=start)
    assert 'cup' not in rig2.app.object_states.snapshot().objects
    rig2.watch(settle_sec(rig2), CUP_FAR)
    newest = [row for row in rig2.app.state_store.changes(0) if row.label == 'cup'][-1]
    assert newest.ts >= start and newest.clock_synced is False
    rig2.app.stop(timeout=1)


def test_object_states_are_saved_every_minute(tmp_path):
    rig = DeskRig(tmp_path)
    [job] = [j for j in rig.app.scheduler.status() if j['name'] == 'save object states']
    assert job['every_sec'] == rig.config.object_states.snapshot_every_sec


def test_object_states_older_than_keep_days_are_deleted_on_a_timer(tmp_path):
    config = changed(desk_config(tmp_path), 'object_states', keep_days=1)
    rig = DeskRig(tmp_path, config=config)
    rig.watch(settle_sec(rig), CUP_FAR)
    assert [row.type for row in rig.app.state_store.changes(0) if row.label == 'cup'] == ['appeared']
    [job] = [j for j in rig.app.scheduler.status() if j['name'] == 'delete old object states']
    assert job['every_sec'] == config.object_states.prune_every_min * 60
    saved = rig.app.state_store.changes(0)
    rig.tick(rig.app.scheduler, after=0.5 * 24 * 3600)              # half a day: still kept
    assert rig.app.state_store.changes(0) == saved
    rig.tick(rig.app.scheduler, after=0.6 * 24 * 3600)              # more than a day: deleted
    assert rig.app.state_store.changes(0) == []


def test_no_object_states_are_deleted_while_the_clock_cant_be_right(tmp_path):
    """A clock before clock.earliest_believable_date is plainly wrong, so the timer deletes nothing by date until
    it is right, as for the event log's days."""
    config = changed(desk_config(tmp_path), 'object_states', keep_days=1)
    config = changed(config, 'clock', earliest_believable_date=date(2100, 1, 1))
    rig = DeskRig(tmp_path, config=config)
    rig.watch(settle_sec(rig), CUP_FAR)
    saved = rig.app.state_store.changes(0)
    assert saved
    rig.tick(rig.app.scheduler, after=2 * 24 * 3600)
    assert rig.app.state_store.changes(0) == saved
