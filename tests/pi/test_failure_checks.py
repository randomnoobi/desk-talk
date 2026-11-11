"""
Checks after the failure hand tests in docs/PI_CHECKS.md ("Failure hand tests"): a person pulls the plug, turns the
network off, pulls the camera's cable, fills the SD card or sets the clock to 1990, and these read what the Pi did.
Nothing here breaks anything or changes the Pi.

    DESKTALK_PI=pi DESKTALK_PI_NOTE_BEFORE=1 uv run pytest -m pi -v -rs -k note_before     first, before each one
    DESKTALK_PI=pi DESKTALK_PI_<STEP>=1 uv run pytest -m pi -v -rs tests/pi/test_failure_checks.py

Each check skips and says what to do unless its DESKTALK_PI_... says that step was just done. The note taken first is
kept on the Mac (in .pytest_cache), so the check afterwards can tell what changed even when the Pi's clock is wrong.
"""

from __future__ import annotations

import os
import shlex
import time
from datetime import date
from typing import Any

import pytest

from tests.pi.conftest import HEALTHY_WAIT_SEC, SERVICES, Pi, wait_for
from tests.pi.hand_test_rules import (
    card_level,
    clock_distrusted,
    days_gone,
    longest_quiet_sec,
    note_problem,
    parse_status_lines,
    restarted_since,
    said_clock_unbelievable,
    stuck_jobs,
    warned_since,
    wifi_dropped,
)
from tests.pi.on_the_pi import restore_time_text, restored_at_this_start
from tests.pi.test_real_pi import (
    BROKEN_LINES_ALLOWED,
    JUST_BOOTED_SEC,
    JUST_POWER_CUT,
    STARTED_AFTER_BOOT_SEC,
    assert_data_readable,
    assert_started_by_itself,
    pictures_arriving,
)

pytestmark = pytest.mark.pi

NOTE_BEFORE = 'DESKTALK_PI_NOTE_BEFORE'
JUST_NO_NETWORK = 'DESKTALK_PI_JUST_NO_NETWORK'
CAMERA_CABLE_OUT = 'DESKTALK_PI_CAMERA_CABLE_OUT'
CAMERA_CABLE_BACK = 'DESKTALK_PI_CAMERA_CABLE_BACK'
CARD_FILLED = 'DESKTALK_PI_CARD_FILLED'
CLOCK_1990 = 'DESKTALK_PI_CLOCK_1990'
CLOCK_BACK = 'DESKTALK_PI_CLOCK_BACK'
AFTER_A_HAND_TEST = (JUST_POWER_CUT, JUST_NO_NETWORK, CAMERA_CABLE_OUT, CAMERA_CABLE_BACK, CARD_FILLED, CLOCK_1990,
                     CLOCK_BACK)
STEPS = 'docs/PI_CHECKS.md, "Failure hand tests"'

NOTE_KEY = 'desktalk/pi_hand_test_note'
NOTE_FRESH_SEC = 24 * 3600
STATUS_LINE_FRESH_SEC = 90          # two of the camera's status lines (pi.status_every_sec = 30), and some room
QUIET_ALLOWED_SEC = 90              # with no network, the camera may go this long without a status line of new pictures
WARNING_WAIT_SEC = 600              # how long Lens has to say a warning, from when the check starts
SCHEDULER_SLACK_SEC = 60            # a job's next run may be this much past its interval
BELIEVABLE_YEAR = 2000              # the Pi's clock is "set back" before this year
CAMERA_COULD_NOT_START = 'the Pi camera module could not start'      # raspberry_pi/camera/cameras.py
# Lens's warning keys (the `key` of a camera event in /api/log/events?types=camera): desktalk/core/vision/
# camera_problems.py says `lost` once pictures stop, `no_pictures` when none came since the app started
CAMERA_WARNING_KEYS = {'lost', 'no_pictures'}
CARD_WARNING_KEYS = {'card'}                    # Lens's watch on the health check of the same name
# The SD card's levels, as the app on the Pi has them (settings → health_checks)
CARD_LEVELS = ('import json; from desktalk.config import load_config; h = load_config().health_checks; '
               'print(json.dumps([h.card_warning_below_mb, h.card_serious_below_mb]))')


def _just_did(name: str, what: str) -> None:
    if not os.environ.get(name):
        pytest.skip(f'Needs a person to {what}. Do the steps in {STEPS}, then run with {name}=1.')


def _containers(pi: Pi) -> dict[str, dict[str, Any]]:
    found = {}
    for service in SERVICES:
        state = pi.state(service)
        if not state.get('Missing'):
            found[service] = {'started_at': state['StartedAt'], 'restart_count': state['RestartCount']}
    return found


def _days(pi: Pi) -> list[str]:
    folder = shlex.quote(f'{pi.data_folder()}/events')
    return sorted(pi.run(f'ls -1 {folder} 2>/dev/null || true').split())


def _pi_now(pi: Pi) -> float:
    return float(pi.run('date +%s'))


def _note(pi: Pi, request: pytest.FixtureRequest) -> dict[str, Any]:
    note = request.config.cache.get(NOTE_KEY, None) if request.config.cache else None
    problem = note_problem(note, mac_now=time.time(), max_age_sec=NOTE_FRESH_SEC)
    if problem:
        pytest.skip(problem)
    assert note is not None
    if note['host'] != pi.host:
        pytest.skip(f'The note is from {note["host"]}, not {pi.host}. Take one on this Pi first.')
    return note


def _lens_warning(pi: Pi, keys: set[str], *, since: float) -> dict[str, Any] | None:
    """Waits for Lens to say one of `keys` since `since` (the Pi's clock). None if it's quiet hours on the Pi, when
    Lens says nothing."""
    if (pi.get('/api/preferences') or {}).get('quiet_now'):
        return None

    def said() -> Any:
        answer = pi.get('/api/log/events?types=camera&limit=50') or {'events': []}
        return warned_since(answer['events'], keys, since=since)
    return wait_for(f'Lens saying one of {sorted(keys)}', said, timeout=WARNING_WAIT_SEC, every=10)


def _health_checks(pi: Pi) -> dict[str, dict[str, Any]]:
    """/api/health → checks: {key: {level, value, unit}}, one per health check (desktalk/core/health/checks.py).
    Skips when the app on the Pi is older than the health checks."""
    checks = (pi.get('/api/health') or {}).get('checks')
    if not checks:
        pytest.skip("The app on the Pi has no health checks in /api/health yet. Update it first, on the Pi: "
                    'bash ~/desk-talk/raspberry_pi/update.sh')
    return checks


def _wait_until_healthy(pi: Pi, services: tuple[str, ...]) -> None:
    last: dict[str, str] = {}

    def healthy() -> bool:
        nonlocal last
        last = {service: status for service, status in pi.health().items() if service in services}
        return all(status == 'healthy' for status in last.values())
    try:
        wait_for(f'{" and ".join(services)} healthy', healthy, timeout=HEALTHY_WAIT_SEC, every=3)
    except AssertionError as error:
        raise AssertionError(f'{error}. They say: {last}') from None


def _camera_log(pi: Pi, *, since: float | None = None, tail: int | None = None) -> str:
    since_part = f' --since {since:.0f}' if since is not None else ''
    tail_part = f' --tail {tail}' if tail is not None else ''
    return pi.run(f'docker logs -t{since_part}{tail_part} {pi.container("camera")} 2>&1', check=False)


# ── always: is the Pi ready for a hand test? Read only ──
def test_ready_for_a_hand_test(pi: Pi):
    """All three healthy, the app answering, and the camera writing status lines with new pictures."""
    if os.environ.get(CAMERA_CABLE_OUT):
        pytest.skip('The camera has no cable now, so it cannot be ready: the cable-out check is the one to run.')
    pi.wait_until_healthy()
    status = pi.get('/api/status')
    assert status is not None, "the app doesn't answer /api/status"
    assert status['camera'] is True, f'the app has no camera: {status}'
    lines = parse_status_lines(_camera_log(pi, tail=40))
    assert lines, "the camera's log has no status line"
    age = _pi_now(pi) - lines[-1].at
    assert age < STATUS_LINE_FRESH_SEC, f"the camera's newest status line is {age:.0f} s old"


# ── before each hand test ──
def test_note_before_a_hand_test(pi: Pi, request: pytest.FixtureRequest):
    """Notes what the Pi has now, on the Mac, for the check afterwards. Reads only."""
    if not os.environ.get(NOTE_BEFORE):
        pytest.skip(f'Only before a hand test: run with {NOTE_BEFORE}=1 ({STEPS}).')
    if request.config.cache is None:
        pytest.fail("pytest's cache is off (-p no:cacheprovider), so the note can't be kept")
    pi.wait_until_healthy()
    note = {'host': pi.host, 'mac_at': time.time(), 'pi_at': _pi_now(pi),
            'boot_id': pi.run('cat /proc/sys/kernel/random/boot_id').strip(),
            'containers': _containers(pi), 'days': _days(pi),
            'database': pi.run(f'test -f {shlex.quote(pi.data_folder())}/object_states.sqlite && echo yes || true')
            .strip() == 'yes',
            'network_device': pi.run("ip route show default | awk '{print $5; exit}'").strip()}
    request.config.cache.set(NOTE_KEY, note)
    print(f"Noted {len(note['days'])} event-log days, the containers' starts and the network on "
          f"{note['network_device']}. Now do the hand test.")


# ── after any hand test ──
def test_nothing_deleted_after_a_hand_test(pi: Pi, request: pytest.FixtureRequest):
    """No event-log day and no object-state file went missing, apart from days old enough for the usual clean-up."""
    if not any(os.environ.get(name) for name in AFTER_A_HAND_TEST):
        pytest.skip(f'Runs after a hand test, with its DESKTALK_PI_... set ({STEPS}).')
    note = _note(pi, request)
    keep_days = pi.in_app('import json; from desktalk.config import load_config; '
                          'print(json.dumps(load_config().event_log.keep_days))')
    # The Mac's day, as the Pi's may be set back on purpose
    gone = days_gone(note['days'], _days(pi), today=date.today(), keep_days=keep_days)
    assert gone == [], f'event-log days gone since the note: {gone}'
    if note['database']:
        assert pi.run(f'test -f {shlex.quote(pi.data_folder())}/object_states.sqlite && echo yes || true').strip(), \
            'the object-state database is gone'
    assert_data_readable(pi, broken_allowed=BROKEN_LINES_ALLOWED)


# ── the network off, with the Pi on ──
def test_after_the_network_was_off(pi: Pi, request: pytest.FixtureRequest):
    """The Wi-Fi (or the cable) went away and came back. Nothing restarted, and pictures kept being taken."""
    _just_did(JUST_NO_NETWORK, 'turn the network off and on again')
    note = _note(pi, request)
    assert pi.run('cat /proc/sys/kernel/random/boot_id').strip() == note['boot_id'], (
        'The Pi has restarted since the note, and this check is for the network going with the Pi on')
    pi.wait_until_healthy()
    restarted = restarted_since(note['containers'], _containers(pi))
    assert restarted == [], f'started again while the network was off: {restarted}'
    now = _pi_now(pi)
    quiet = longest_quiet_sec(parse_status_lines(_camera_log(pi, since=note['pi_at'])), since=note['pi_at'], now=now)
    assert quiet <= QUIET_ALLOWED_SEC, f'the camera took no new pictures for {quiet:.0f} s'
    pictures_arriving(pi, timeout=60)
    device = note['network_device']
    journal = pi.run(f'journalctl -u NetworkManager --since @{note["pi_at"]:.0f} --no-pager 2>&1', check=False)
    dropped = wifi_dropped(journal, device)
    if dropped is None:
        pytest.skip(f"All good, but this user can't read the journal, so nothing shows {device} went off. Add the "
                    'user to the adm group.')
    assert dropped, f"NetworkManager's journal doesn't show {device} going off since the note, so this shows nothing"


# ── the camera's ribbon cable out (only ever with the power off) ──
def test_with_the_camera_cable_out(pi: Pi):
    """The Pi booted with no camera. The app and the detector run, the camera says why it can't start, and Lens
    says the pictures aren't coming."""
    _just_did(CAMERA_CABLE_OUT, 'switch the Pi off, take the camera cable out and switch it on again')
    uptime = pi.uptime_sec()
    assert uptime < JUST_BOOTED_SEC, f'The Pi has been up {uptime / 60:.0f} min: the cable comes out with it off'
    _wait_until_healthy(pi, ('app', 'detector'))
    for service in ('app', 'detector'):
        started = pi.started_after_boot_sec(service)
        assert started < STARTED_AFTER_BOOT_SEC, f'{service} started {started:.0f} s after boot, not by itself'
    assert CAMERA_COULD_NOT_START in _camera_log(pi, tail=200), \
        "the camera's log doesn't say it couldn't start the camera module"
    assert pi.health()['camera'] != 'healthy', 'the camera container says it is healthy with no camera'
    assert (pi.get('/api/health') or {'camera': {}})['camera'].get('connected') is False
    camera = _health_checks(pi)['camera']
    assert camera['level'] == 'serious', f"/api/health → checks → camera says {camera['level']} with no camera"
    booted_at = _pi_now(pi) - pi.uptime_sec()
    if _lens_warning(pi, CAMERA_WARNING_KEYS, since=booted_at) is None:
        pytest.skip('All good except the warning: it is quiet hours on the Pi, so Lens says nothing. Do it by day.')


def test_with_the_camera_cable_back(pi: Pi):
    """Cable back in, with the power off, and switched on: everything starts by itself and pictures come."""
    _just_did(CAMERA_CABLE_BACK, 'switch the Pi off, put the camera cable back and switch it on again')
    assert_started_by_itself(pi)


# ── a nearly full SD card ──
def test_with_the_card_filled(pi: Pi, request: pytest.FixtureRequest):
    """A big file leaves the card below the warning level: Lens warns, and everything keeps going."""
    _just_did(CARD_FILLED, 'fill the SD card with a big file')
    note = _note(pi, request)
    free_mb = int(pi.run(f'df -m --output=avail {shlex.quote(pi.data_folder())} | tail -n 1'))
    checks = _health_checks(pi)
    warning_mb, serious_mb = pi.in_app(CARD_LEVELS)
    level = card_level(free_mb, warning_mb=warning_mb, serious_mb=serious_mb)
    assert level != 'ok', (f'The card has {free_mb} MB free, above the warning level ({warning_mb} MB): the big file '
                           'is not there')
    card = checks['card']
    assert card['level'] == level, (f"/api/health → checks → card says {card['level']} ({card['value']} MB free), "
                                    f'but with {free_mb} MB free it should say {level}')
    pi.wait_until_healthy()
    restarted = restarted_since(note['containers'], _containers(pi))
    assert restarted == [], f'started again with the card nearly full: {restarted}'
    pictures_arriving(pi, timeout=60)
    assert_data_readable(pi)
    said = _lens_warning(pi, CARD_WARNING_KEYS, since=note['pi_at'])
    if said is None:
        pytest.skip(f'{free_mb} MB free ({level}) and all good, but it is quiet hours on the Pi, so Lens says '
                    'nothing. Do it by day.')
    print(f"With {free_mb} MB free ({level}) Lens said: {said['text']}")


# ── the clock set to 1990 ──
def test_with_the_clock_set_to_1990(pi: Pi, request: pytest.FixtureRequest):
    """Run with the clock still set back, after the app's restart. The app doesn't believe its clock, so it picks up
    nothing from before, and its timed jobs still run."""
    _just_did(CLOCK_1990, 'set the clock back to 1990 and restart the app')
    note = _note(pi, request)
    year = int(pi.run('date +%Y'))
    if year >= BELIEVABLE_YEAR:
        pytest.skip(f"The Pi's clock says {year}. Run this while it's still set back, before the undo step.")
    pi.wait_until_healthy()
    assert pi.get('/api/status') is not None, "the app doesn't answer"
    log = pi.app_log()
    restored_at = restore_time_text(log)
    assert restored_at is not None, 'The app\'s log has no "Picked up" line'
    if int(restored_at[:4]) >= BELIEVABLE_YEAR:
        pytest.skip(f'The app last started at {restored_at}, before the clock went back. Restart it ({STEPS}).')
    assert clock_distrusted(log), "The app started with the clock in 1990 and didn't say its clock is wrong"
    assert said_clock_unbelievable(log), ("The app didn't say its clock is before clock.earliest_believable_date. "
                                          'Is it older than that check? Update it first (update.sh)')
    assert restored_at_this_start(log) == 0, 'The app picked up saved states with its clock in 1990'
    others = {service: state for service, state in note['containers'].items() if service != 'app'}
    restarted = restarted_since(others, _containers(pi))
    assert restarted == [], f'started again when the clock went back: {restarted}'
    pictures_arriving(pi, timeout=60)
    # Jobs timed by the wall clock would wait 36 years for their next run
    stuck = stuck_jobs(pi.get('/api/health'), slack_sec=SCHEDULER_SLACK_SEC)
    assert stuck == [], f'these jobs will not run until the clock is right: {stuck}'


def test_with_the_clock_back(pi: Pi):
    """After the undo step: the clock is set from the internet again and everything runs."""
    _just_did(CLOCK_BACK, 'set the clock back to 1990, then undo it')
    year = int(pi.run('date +%Y'))
    assert year >= BELIEVABLE_YEAR, f"The Pi's clock still says {year}: turn network time on again ({STEPS})"
    synced = pi.run('timedatectl show -p NTPSynchronized --value').strip()
    assert synced == 'yes', "The Pi's clock hasn't been set from the internet yet. Wait a minute and run this again"
    pi.wait_until_healthy()
    pictures_arriving(pi, timeout=60)
    stuck = stuck_jobs(pi.get('/api/health'), slack_sec=SCHEDULER_SLACK_SEC)
    assert stuck == [], f'these jobs are waiting longer than they should: {stuck}'
