"""
Tests against a real Raspberry Pi running all three containers. Nothing here is faked.

    DESKTALK_PI=<host> uv run pytest -m pi -v

They need no cable, keyboard or screen, only SSH with a key. A test that needs a person (a cup on the desk, the power
cut, the Wi-Fi off) skips and says what to do; docs/PI_CHECKS.md has the steps and which of these to run after each.
Tests that stop or change containers use the `stack` fixture, which puts everything back even if the test fails.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import time
from typing import Any

import pytest
import yaml

from tests.pi.conftest import SERVICES, Pi, wait_for
from tests.pi.on_the_pi import (
    CLOCK_PROBE,
    RESULTS,
    app_stopped_cleanly,
    clock_behind_warning_sec,
    clock_not_synced_at_this_start,
    expected_restore,
    first_wifi_after_boot,
    how_boot_ended,
    off_the_pi,
    pages_on_the_wifi,
    problems,
    real_start_time,
    restore_time_text,
    restored_at_this_start,
    speak_result,
)

pytestmark = pytest.mark.pi

OBJECT_ON_DESK = 'DESKTALK_PI_OBJECT_ON_DESK'
JUST_POWER_CUT = 'DESKTALK_PI_JUST_POWER_CUT'
NO_WIFI_AT_BOOT = 'DESKTALK_PI_NO_WIFI_AT_BOOT'
STEPS = 'docs/PI_CHECKS.md'

OBJECT_WAIT_SEC = 180               # how long the cup has to be noticed, from when the test starts
OBJECT_LOOKS_BACK_SEC = 300         # a cup put down this long before the test started counts too
JUST_BOOTED_SEC = 1800              # "just cut the power": the Pi has been up less than this
STARTED_AFTER_BOOT_SEC = 300        # each container's start, counted from the boot, if no one started it by hand
BROKEN_LINES_ALLOWED = 1            # a power cut may cut off the last line of a file
RESTORE_TIME_SLACK_SEC = 30         # how far from "start minus restore_max_age_sec" a saved state may be either way
APP_START_WAIT_SEC = 120
ALLOWED_POLLING_SEC = 10            # room for this test's own slowness: it looks every 2 s over SSH
STATUS_LINES_WAIT_SEC = 65          # two of the camera's status lines (pi.status_every_sec = 30)
DETECT_TRIES = 100                  # the detector is usually busy: how many times to offer it one picture
DETECTOR_BACK_WAIT_SEC = 240        # a stopped detector has to start and load its model again
WARNING_SLACK_SEC = 90              # on top of Lens's wait: the camera has to notice first, and Lens looks now and then
LOOK_FOR_CONNECTIONS_SEC = 90
# All the Wi-Fi may send in the whole look. Measured on a quiet Pi: about 25 bytes a second, so about 2 KB. One picture
# is about 30 KB at the smallest seen, so this is half of one in 90 s.
MOST_WIFI_SENT_BYTES = 15_000
SHORT_DELETE_EVERY_MIN = 1          # for the disk test; the real 10 minutes would take half an hour
DISK_CYCLES = 3.5
DISK_ROOM = 1.25                    # one cycle's pictures may be this much over fps x time
RESULTS_FRESH_SEC = 24 * 3600

CAMERA_STATUS = re.compile(r'Pictures taken (\d+), sent (\d+), failed (\d+), skipped (\d+); '
                           r'detector ok (\d+), busy (\d+), off (\d+)')


def pictures_arriving(pi: Pi, *, timeout: float) -> None:
    """Fails if the app's count of pictures doesn't go up within `timeout`."""
    first = pi.pictures_received()

    def more() -> bool:
        now = pi.pictures_received()
        return now is not None and now > (first or 0)
    wait_for('new pictures reaching the app', more, timeout=timeout)


def assert_data_readable(pi: Pi, *, broken_allowed: int = 0) -> dict[str, Any]:
    data = pi.on_the_pi('data', '--data', pi.data_folder())
    assert data['event_files'], 'the event log has no files'
    for name, counts in data['event_files'].items():
        assert counts['broken'] <= broken_allowed, f'{name}: {counts["broken"]} lines cannot be read'
    # the app's own reader, through the page's route
    assert pi.get('/api/log/events?limit=5') is not None, "the app can't read its own event log"
    # No file yet is fine (nothing has been saved); a file SQLite can't open fails the command on the Pi.
    if data['states']['readable']:
        assert data['states']['integrity'] == 'ok', f"SQLite says: {data['states']['integrity']}"
    return data


def latest_camera_status(pi: Pi) -> tuple[int, ...]:
    """The numbers in the camera's newest status line: taken, sent, failed, skipped, ok, busy, off."""
    found = CAMERA_STATUS.findall(pi.run(f'docker logs --tail 40 {pi.container("camera")} 2>&1'))
    assert found, "the camera's log has no status line"
    return tuple(int(n) for n in found[-1])


def restored_count(pi: Pi) -> int | None:
    """How many object states the app says it picked up at its last start. The log outlives restarts, but this is read
    only once the app is healthy, and the app restores before it can be healthy, so the last "Picked up" line is this
    start's."""
    return restored_at_this_start(pi.app_log())


def _power_cut_evidence(pi: Pi) -> str | None:
    """Fails if the last boot ended with a clean shutdown, so a plain `sudo reboot` can't pass for a power cut. The
    earlier boot's journal says best, but Raspberry Pi OS keeps the journal in memory only
    (/usr/lib/systemd/journald.conf.d/40-rpi-volatile-storage.conf), so it's normally gone. Then the app's own log
    tells: on a clean shutdown Docker stops the app and it says goodbye. Returns why it can't be told, or None."""
    ended = how_boot_ended(pi.run('journalctl -b -1 -n 200 --no-pager 2>&1', check=False))
    assert ended != 'clean', 'The journal shows the boot before this one shut down cleanly, so this was no power cut'
    if ended == 'not clean':
        return None
    stopped = app_stopped_cleanly(pi.app_log())
    assert stopped is not True, ("The app's log shows it was stopped cleanly before this start, so this was a reboot "
                                 'or a shut-down and not a power cut')
    if stopped is None:
        return "there's no earlier journal, and the app's log has no earlier run"
    return None


# ── nothing else on ──
def test_works_with_nothing_else_on(pi: Pi):
    """A new object_state event for a real object, with only the Pi running."""
    if not os.environ.get(OBJECT_ON_DESK):
        pytest.skip(f'Needs a real object in view, and the camera can\'t put one there. Put a cup on the desk and run '
                    f'with {OBJECT_ON_DESK}=1 ({STEPS}, "A cup on the desk").')
    pi.wait_until_healthy()
    since = float(pi.run('date +%s')) - OBJECT_LOOKS_BACK_SEC

    def real_object_seen() -> dict[str, Any] | None:
        for event in reversed(pi.today_lines('events.jsonl')):
            if event.get('type') == 'object_state' and event.get('ts', 0) >= since and not event.get('demo') and \
                    event.get('object') != 'person' and event.get('new_state') == 'PRESENT':
                return event
        return None

    event = wait_for('an object_state event for a real object', real_object_seen, timeout=OBJECT_WAIT_SEC, every=5)
    assert event['device_id'] == 'pi'


def test_pictures_reach_the_app_and_the_detector_answers_for_each(pi: Pi):
    """The same with no object and no person: every picture got an answer from the detector."""
    pi.wait_until_healthy()
    status = pi.get('/api/status')
    assert status['camera_source'] == 'pi' and status['camera'] is True
    before = latest_camera_status(pi)
    time.sleep(STATUS_LINES_WAIT_SEC)
    after = latest_camera_status(pi)
    taken, sent, _, _, ok, busy, off = (a - b for a, b in zip(after, before, strict=True))
    assert taken > 0 and sent > 0, f'no pictures: {before} then {after}'
    assert ok > 0, f'the detector looked at no picture: {before} then {after}'
    assert off == 0, f'{off} pictures got no answer from the detector'
    # Busy is an answer too: it's on another picture, so this one goes without. One may be under way as a line is made.
    assert abs(ok + busy - taken) <= 2, f'{taken} pictures but {ok} + {busy} answers'

    picture = pi.run(f"docker exec {pi.container('camera')} sh -c "
                     "\"find /photos -name '*.jpg' -mmin -1 | sort | tail -n 1\"").strip()
    assert picture, 'no new picture in /photos'
    offer = (f"import json, time, urllib.error, urllib.request\n"
             f"body = open({picture!r}, 'rb').read()\n"
             f"for _ in range({DETECT_TRIES}):\n"
             f"    try:\n"
             f"        ask = urllib.request.Request('http://detector:8100/detect', data=body, method='POST')\n"
             f"        print(urllib.request.urlopen(ask, timeout=10).read().decode()); break\n"
             f"    except urllib.error.HTTPError as error:\n"
             f"        if error.code != 429: raise\n"
             f"        time.sleep(0.2)\n")
    reply = pi.run(f'docker exec {pi.container("camera")} python3 -c {shlex.quote(offer)}')
    assert reply.strip(), f'the detector stayed busy for {DETECT_TRIES} tries'
    assert 'detections' in json.loads(reply), reply


# ── after a power cut (a person does the cutting) ──
def assert_started_by_itself(pi: Pi) -> None:
    uptime = pi.uptime_sec()
    assert uptime < JUST_BOOTED_SEC, f'The Pi has been up {uptime / 60:.0f} min, so this is not just after a power cut'
    pi.wait_until_healthy()
    for service in SERVICES:
        started = pi.started_after_boot_sec(service)
        assert started < STARTED_AFTER_BOOT_SEC, (f'{service} started {started:.0f} s after boot. It should start '
                                                  'by itself, soon after boot')
    pictures_arriving(pi, timeout=60)


def test_starts_after_power_cut(pi: Pi):
    """Reads what the power cut left behind. It doesn't cut anything."""
    if not os.environ.get(JUST_POWER_CUT):
        pytest.skip(f'Someone has to pull the plug. Do the steps in {STEPS} ("A power cut"), then run with '
                    f'{JUST_POWER_CUT}=1.')
    assert_started_by_itself(pi)
    unknown = _power_cut_evidence(pi)
    data = assert_data_readable(pi, broken_allowed=BROKEN_LINES_ALLOWED)
    if unknown:
        pytest.skip(f"Started and readable, but nothing shows the power was really cut: {unknown}.")
    if not data['states']['readable']:
        pytest.skip('Started and readable, but there was no SQLite file, so restored states could not be checked.')

    settings = pi.settings()
    log = pi.app_log()
    restored, restored_at = restored_at_this_start(log), restore_time_text(log)
    assert restored is not None and restored_at is not None, 'The app\'s log has no "Picked up" line'
    warned = clock_behind_warning_sec(log)
    restored_epoch = _local_time_to_epoch(pi, restored_at)
    # How far behind the app's clock was when it restored: the real start is a little before the restore, so a clock
    # that was right by then comes out at about 0 or below, even if it was still behind when the container started.
    # It also comes out low by the app's start-up time (Python starting), which the slack covers.
    lag = _real_start_time(pi) - restored_epoch
    not_synced = clock_not_synced_at_this_start(log)
    # Before the clock has synced only rows saved in this boot may come back; the rows from before the cut have the
    # boot before's id, so none of those
    this_boot = pi.app_boot_id()
    expected = expected_restore(data['states']['rows'], restored_at=restored_epoch,
                                warned_behind_sec=warned, not_synced=not_synced, lag_sec=lag,
                                restore_max_age_sec=settings['restore_max_age_sec'], slack_sec=RESTORE_TIME_SLACK_SEC,
                                this_boot=this_boot)
    if expected.case == 'clock behind':
        assert lag > -RESTORE_TIME_SLACK_SEC, (f'The app said its clock was about {warned} s behind the saved states, '
                                               f'but it was measured {lag:.0f} s behind when it restored (the start-up '
                                               f'time can make this a little low)')
    # The Pi makes its clock flag only once the clock is right, so a clock still behind must have been caught
    assert expected.case != 'clock behind, not noticed', (
        f"The app's clock was {lag:.0f} s behind when it restored, and it didn't say its clock hadn't synced: is "
        f"object_states.clock_synced_file set, and its folder shared in compose.yaml? Picked up {restored}")
    if expected.case == 'nothing saved':
        pytest.skip('Started and readable, but nothing was saved in the 5 minutes before the cut, so restored states '
                    f'could not be checked. Put a cup on the desk first and plug back in sooner ({STEPS}).')
    assert len(expected.must_be) <= restored <= len(expected.could_be), (
        f'{expected.case}: the app warned its clock was {warned} s behind (not synced: {not_synced}, this boot: '
        f'{this_boot}), measured {lag:.0f} s; picked up '
        f'{restored}; saved: {expected.could_be}, sure: {expected.must_be}')


def _real_start_time(pi: Pi) -> float:
    """When the app really started by the Pi's clock now. A clock jump after the start can't change it."""
    return real_start_time(pi.run(CLOCK_PROBE), pi.started_after_boot_sec('app'))


def _local_time_to_epoch(pi: Pi, text: str) -> float:
    """A time the way the app's log writes it, in seconds since 1970. `date` on the Pi uses its time zone, as the
    log does."""
    return float(pi.run(f'date -d {shlex.quote(text)} +%s'))


def _first_wifi_after_boot_sec(pi: Pi) -> float | None:
    """When NetworkManager first connected the Wi-Fi this boot, counted from the boot. None if the journal can't say
    (the user must be in the adm or systemd-journal group to read it)."""
    return first_wifi_after_boot(pi.run('journalctl -b -u NetworkManager -o short-monotonic --no-pager 2>&1',
                                        check=False))


def test_no_wifi_at_boot(pi: Pi):
    """Reads what a boot with no Wi-Fi left behind. Everything started before the Wi-Fi came."""
    if not os.environ.get(NO_WIFI_AT_BOOT):
        pytest.skip(f'Someone has to boot it with the Wi-Fi off. Do the steps in {STEPS} ("No Wi-Fi at boot"), then '
                    f'run with {NO_WIFI_AT_BOOT}=1.')
    assert_started_by_itself(pi)
    assert_data_readable(pi, broken_allowed=BROKEN_LINES_ALLOWED)
    newest = pi.today_lines('detections.jsonl')[-1]
    assert float(pi.run('date +%s')) - newest['ts'] < 120, 'no new pictures in the event log'
    wifi = _first_wifi_after_boot_sec(pi)
    if wifi is None:
        pytest.skip("Started and readable, but the journal doesn't say when the Wi-Fi came, so nothing shows it was "
                    f'off at boot. On the Pi: journalctl -b -u NetworkManager | grep wlan0 ({STEPS}).')
    for service in SERVICES:
        started = pi.started_after_boot_sec(service)
        assert started < wifi, (f'Wi-Fi came {wifi:.0f} s after boot and {service} started at {started:.0f} s. '
                                'So the Wi-Fi was there at boot, and this shows nothing')


# ── no network at all: a person turns it off, and a script on the Pi takes notes ──
def _no_network_results(pi: Pi) -> dict[str, Any]:
    path = f'{pi.data_folder()}/{RESULTS}'
    text = pi.run(f'cat {shlex.quote(path)} 2>/dev/null || true')
    if not text.strip():
        pytest.skip(f'No notes from a run with no network. Only a person can do it: {STEPS}, "No network at all".')
    results = json.loads(text)
    age = float(pi.run('date +%s')) - results['started_at']
    if age > RESULTS_FRESH_SEC:
        pytest.skip(f'The notes are {age / 3600:.0f} hours old. Do it again: {STEPS}, "No network at all".')
    if results.get('pretend'):
        pytest.skip(f'The notes are from a try with the network on. Do the real one: {STEPS}, "No network at all".')
    return results


def test_runs_with_no_network_at_all(pi: Pi):
    """The Pi noted how it did with no network, with no AI set; this only reads that once Wi-Fi is back."""
    assert problems(_no_network_results(pi)) == []


def test_speak_command_ran_with_no_network(pi: Pi):
    results = _no_network_results(pi)
    result = speak_result(results)
    if result == 'no card':
        pytest.skip('The app tried to say something, but no USB speaker was plugged in during the run, so the speak '
                    f'command has not run. Plug one in and do it again: {STEPS}, "No network at all".')
    if result == 'nothing to say':
        pytest.skip(f'Nothing in that run was worth saying. Cover the lens during it: {STEPS}, "No network at all".')
    assert result == 'said', f'the app tried and failed, in its log: {results["speak"]}'


# ── one container restarting ──
def test_app_restart_needs_no_camera_restart(stack: Pi):
    pi = stack
    camera_before = pi.state('camera')
    wait = pi.settings()['pi']['upload_retry_max_sec']
    pi.run(f'docker restart {pi.container("app")}')
    wait_for('the app answering after its restart', lambda: pi.get('/api/status') is not None,
             timeout=APP_START_WAIT_SEC, every=1)
    answering = time.monotonic()

    def received() -> bool:
        return (pi.pictures_received() or 0) > 0
    wait_for('pictures after the app came back', received, timeout=wait + ALLOWED_POLLING_SEC, every=1)
    print(f'Pictures came back {time.monotonic() - answering:.1f} s after the app answered (limit {wait:g} s + '
          f'{ALLOWED_POLLING_SEC} s for looking)')
    camera_after = pi.state('camera')
    assert camera_after['StartedAt'] == camera_before['StartedAt'], 'the camera got restarted'
    assert camera_after['RestartCount'] == camera_before['RestartCount'], 'the camera got restarted'


def test_app_restart_before_the_clock_has_synced_keeps_this_boots_states(stack: Pi):
    """A Pi that hasn't reached the internet since it booted: restarting only the app (the watchdog, the memory limit)
    still brings back what this boot saved, since the clock ran on without a break."""
    pi = stack
    settings = pi.settings()
    synced_file = settings['clock_synced_file']
    not_synced = (f"The Pi's clock has been set from the internet this boot, so this would show nothing. Do the steps "
                  f'in {STEPS} ("An app restart before the clock has synced").')
    if synced_file is None or pi.app_sees_file(synced_file):
        pytest.skip(not_synced)
    this_boot = pi.app_boot_id()
    assert this_boot, "The app's container can't read a boot id: is object_states.boot_id_file set?"
    pi.run(f'docker restart {pi.container("app")}')
    pi.wait_until_healthy()
    if pi.app_sees_file(synced_file):       # the clock was set while the app restarted: Docker shows the new flag
        pytest.skip(not_synced)
    log = pi.app_log()
    restored, restored_at = restored_at_this_start(log), restore_time_text(log)
    assert restored is not None and restored_at is not None, 'The app\'s log has no "Picked up" line'
    assert clock_not_synced_at_this_start(log), ("The app didn't say its clock hadn't synced, though the Pi's flag "
                                                 "isn't there")
    data = assert_data_readable(pi)
    expected = expected_restore(data['states']['rows'], restored_at=_local_time_to_epoch(pi, restored_at),
                                warned_behind_sec=clock_behind_warning_sec(log), not_synced=True, lag_sec=0.0,
                                restore_max_age_sec=settings['restore_max_age_sec'], slack_sec=RESTORE_TIME_SLACK_SEC,
                                this_boot=this_boot)
    assert expected.case == 'clock not synced', f'{expected.case}: the app said its clock was behind its own rows'
    if not expected.could_be:
        pytest.skip('Nothing was saved in this boot in the last 5 minutes, so there was nothing to keep. Put a cup on '
                    f'the desk 2 minutes before ({STEPS}).')
    assert len(expected.must_be) <= restored <= len(expected.could_be), (
        f'picked up {restored}; saved in this boot ({this_boot}): {expected.could_be}, sure: {expected.must_be}')


def test_detector_restart_keeps_live_view(stack: Pi):
    """Off for long enough to get Lens's warning (a plain `docker restart` is back before it would say anything)."""
    pi = stack
    pi.run(f'docker restart {pi.container("app")}')      # so Lens hasn't just said this and isn't waiting to repeat it
    wait_for('the app answering', lambda: pi.get('/api/status') is not None, timeout=APP_START_WAIT_SEC, every=1)
    pictures_arriving(pi, timeout=60)
    app_before, camera_before = pi.state('app'), pi.state('camera')
    settings = pi.settings()

    stopped_at = float(pi.run('date +%s'))
    pi.run(f'docker stop {pi.container("detector")}')
    assert not pi.state('detector')['Running']

    def warning() -> dict[str, Any] | None:
        pictures_arriving(pi, timeout=30)       # all through it, the live view must keep going
        answer = pi.get('/api/log/events?types=camera&limit=20') or {'events': []}
        for event in answer['events']:
            if event.get('key') == 'pi_detector' and event.get('kind') == 'problem' and event['ts'] >= stopped_at:
                return event
        return None

    quiet = (pi.get('/api/preferences') or {}).get('quiet_now')
    if not quiet:
        said = wait_for("Lens's warning that the detector is off", warning,
                        timeout=settings['pi_detector_wait'] + WARNING_SLACK_SEC)
        print(f"Lens said after {said['ts'] - stopped_at:.0f} s: {said['text']}")
    pictures_arriving(pi, timeout=30)
    assert 'image/jpeg' in pi.content_type('/api/latest_frame_raw/0'), 'no live picture'
    assert (pi.get('/api/health') or {})['pi']['detector'] == 'off'

    pi.run(f'docker start {pi.container("detector")}')
    started_again = float(pi.run('date +%s'))

    def found_by_the_detector_again() -> bool:
        return any(line.get('detected_on') == 'pi' and line['ts'] > started_again
                   for line in pi.today_lines('detections.jsonl')[-20:]) and \
            (pi.get('/api/health') or {'pi': {}})['pi'].get('detector') in ('ok', 'busy')

    wait_for('the detector answering again', found_by_the_detector_again, timeout=DETECTOR_BACK_WAIT_SEC, every=5)
    for service, before in (('app', app_before), ('camera', camera_before)):
        assert pi.state(service)['StartedAt'] == before['StartedAt'], f'{service} got restarted'
    if quiet:
        pytest.skip('All good except the warning: it is quiet hours on the Pi, so Lens says nothing. Run it by day.')


def test_all_three_killed_come_back_readable(stack: Pi):
    """Not a power cut: the Pi keeps running, so what it has already written still reaches the card. It's the nearest
    thing a test can do alone. `docker kill` counts as stopped by hand, so Docker doesn't restart them by itself as it
    would after a crash: `up -d` does it here. test_starts_after_power_cut is the real one."""
    pi = stack
    pictures_arriving(pi, timeout=60)
    before = pi.on_the_pi('data', '--data', pi.data_folder())
    pi.run('docker kill ' + ' '.join(pi.container(service) for service in SERVICES))
    assert not any(pi.state(service)['Running'] for service in SERVICES)
    pi.restore_stack()
    pictures_arriving(pi, timeout=60)
    after = assert_data_readable(pi, broken_allowed=BROKEN_LINES_ALLOWED)
    for name in before['event_files']:
        assert name in after['event_files'], f'{name} is gone'
    assert restored_count(pi) is not None, 'the app\'s log has no "Picked up" line'


# ── what the containers see ──
def test_times_match_local_time(pi: Pi):
    """Containers default to UTC. The app must work out the same local hour for an event as the Pi does."""
    pi.wait_until_healthy()
    lines = pi.today_lines('detections.jsonl') + pi.today_lines('events.jsonl')
    assert lines, 'no events today: is the camera running?'
    newest = max(line['ts'] for line in lines)
    local = pi.in_app('import json; from desktalk.runtime import SystemClock; c = SystemClock(); '
                      f'print(json.dumps({{"hour": c.local_hour({newest}), "day": c.local_day({newest})}}))')
    hour, day = pi.run(f'date -d @{newest} "+%H %F"').split()
    assert local['hour'] == int(hour), f"the app says hour {local['hour']}, the Pi says {hour}"
    assert local['day'] == day

    offset = pi.run('date +%z').strip()
    for service in SERVICES:
        inside = pi.run(f'docker exec {pi.container(service)} date +%z').strip()
        assert inside == offset, f'{service} is on {inside}, the Pi on {offset}'
    assert (pi.get('/api/log/days') or {}).get('today') == pi.run('date +%F').strip()
    assert (pi.get('/api/habits/today') or {}).get('day') == pi.run('date +%F').strip()


def test_disk_stays_under_limit(stack: Pi):
    """With pi.delete_every_min cut to 1 by a throwaway copy of the settings: the real 10 minutes would take half an
    hour. The `stack` fixture takes it away again (checked at the end). A nearly full disk isn't tried here: the card
    has too much room to fill, so only unit tests cover pi.min_free_mb's early delete."""
    pi = stack
    real = pi.settings()['pi']
    settings = yaml.safe_load(pi.in_compose_folder('cat settings.pi.yaml'))
    settings.setdefault('pi', {})['delete_every_min'] = SHORT_DELETE_EVERY_MIN
    folder = pi.run('mktemp -d').strip()
    override = {'services': {service: {'volumes': [f'{folder}/settings.yaml:/desk-talk/config/settings.local.yaml:ro']}
                             for service in ('app', 'camera')}}
    pi.run(f'cat > {folder}/settings.yaml', stdin=yaml.safe_dump(settings))
    pi.run(f'cat > {folder}/override.yaml', stdin=yaml.safe_dump(override))
    try:
        pi.compose('-f', 'compose.yaml', '-f', f'{folder}/override.yaml', 'up', '-d')
        pi.wait_until_healthy()
        assert pi.settings()['pi']['delete_every_min'] == SHORT_DELETE_EVERY_MIN

        count = (f"docker exec {pi.container('camera')} sh -c \"find /photos -name '*.jpg' | wc -l\"")
        cycle = SHORT_DELETE_EVERY_MIN * 60
        counts = []
        end = time.monotonic() + DISK_CYCLES * cycle
        while time.monotonic() < end:
            counts.append(int(pi.run(count)))
            time.sleep(5)

        drops = [i for i in range(1, len(counts)) if counts[i] < counts[i - 1]]
        print(f'Stored pictures every 5 s: {counts}')
        assert len(drops) >= 2, f'expected a cleanup each minute, saw {len(drops)}: {counts}'
        limit = real['fps'] * cycle * DISK_ROOM
        after_first = counts[drops[0]:]
        assert max(after_first) <= limit, f'stored pictures grew past one cycle ({limit:g}): {counts}'

        free_mb = int(pi.run(f"docker exec {pi.container('camera')} df -m /photos | tail -n 1 | awk '{{print $4}}'"))
        assert free_mb > real['min_free_mb'], f'{free_mb} MB free'
    finally:
        try:
            pi.restore_stack()
        finally:
            pi.run(f'rm -rf {folder}')
    assert pi.settings()['pi']['delete_every_min'] == real['delete_every_min'], 'the real settings did not come back'


def test_no_picture_goes_off_the_pi(pi: Pi):
    """With no AI set. It looks in each container about once a second, so a connection that opens and closes between
    two looks can go unseen. The Wi-Fi's byte count over the whole look covers that for pictures: one alone would use
    up the limit.

    What it still can't see: anything smaller than the limit (words, or a tiny picture) sent now and then; anything
    sent outside these 90 s; anything sent on another way out (a cable, when the Wi-Fi isn't the default route). And
    another SSH session on the Wi-Fi, like a download on the Pi, makes it fail with nothing wrong."""
    pi.wait_until_healthy()
    status = pi.get('/api/status')
    if status['llm']:
        pytest.skip('An AI is set up on this Pi, and this test is for a Pi with none.')
    docker_network = pi.run("docker network inspect desktalk_default -f '{{(index .IPAM.Config 0).Subnet}}'").strip()
    seen = pi.on_the_pi('connections', '--seconds', str(LOOK_FOR_CONNECTIONS_SEC),
                        timeout=LOOK_FOR_CONNECTIONS_SEC + 60)
    print(f"{seen['looks']} looks in {seen['seconds']:.0f} s, {len(seen['connections'])} other ends, "
          f"{seen['wifi_sent_bytes']} bytes out on {seen['wifi_device']}")
    assert seen['looks'] >= LOOK_FOR_CONNECTIONS_SEC / 10, f"only {seen['looks']} looks"
    assert seen['connections'], 'saw no connections at all, not even the camera to the app: the check is broken'
    assert off_the_pi(seen['connections'], docker_network, seen['wifi_network']) == []

    sent = seen['wifi_sent_bytes']
    if sent >= MOST_WIFI_SENT_BYTES and pages_on_the_wifi(seen['connections'], seen['wifi_network']):
        pytest.skip('No connection off the Pi, and a lot went out over the Wi-Fi, but someone has the page open, so '
                    'that may be the live picture going to them. Close the page and run it again.')
    assert sent < MOST_WIFI_SENT_BYTES, (f"{sent} bytes went out over the Wi-Fi in {seen['seconds']:.0f} s (limit "
                                         f'{MOST_WIFI_SENT_BYTES}). Is anything else busy on the Pi?')
