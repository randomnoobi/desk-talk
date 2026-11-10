"""The plain rules in tests/pi/on_the_pi.py, with no Pi: these run with the fast tests."""

from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path

import pytest

from tests.pi.on_the_pi import (
    _address,
    app_stopped_cleanly,
    check_data,
    clock_behind_warning_sec,
    clock_not_synced_at_this_start,
    expected_restore,
    first_wifi_after_boot,
    how_boot_ended,
    off_the_pi,
    pages_on_the_wifi,
    parse_proc_net,
    problems,
    read_json_lines,
    real_start_time,
    restore_time_text,
    restored_at_this_start,
    speak_result,
)

DOCKER = '172.18.0.0/16'
WIFI = '192.168.86.0/24'
HEALTHY = {'desktalk-app-1': 'healthy', 'desktalk-camera-1': 'healthy', 'desktalk-detector-1': 'healthy'}


def connection(remote: str, *, local_port: int = 40000, remote_port: int = 443) -> dict[str, object]:
    return {'kind': 'tcp', 'local': '172.18.0.4', 'local_port': local_port, 'remote': remote,
            'remote_port': remote_port}


def sample(after_sec: float, pictures: int, **changes: object) -> dict[str, object]:
    return {'after_sec': after_sec, 'pictures': pictures, 'containers': HEALTHY, 'app_answers': True, 'ai': False,
            'newest_detection_ts': 1000 + after_sec, **changes}


def good_run(**changes: object) -> dict[str, object]:
    return {'finished_at': 2000, 'offline_at': 1000, 'app_restart_ok': True,
            'samples': [sample(after, after) for after in range(120, 360, 10)], **changes}


# ── /proc/net/tcp ──
def test_an_address_reads_the_way_the_kernel_writes_it():
    assert _address('0100007F:1F90') == ('127.0.0.1', 8080)
    assert _address('00000000000000000000000001000000:0050') == ('::1', 80)


def test_a_udp_socket_with_no_other_end_is_kept_since_it_can_send_anywhere():
    text = ('  sl  local_address rem_address   st\n'
            '   0: 040012AC:9B84 00000000:0000 07 00000000\n')
    assert parse_proc_net(text, 'udp') == [{'kind': 'udp', 'local': '172.18.0.4', 'local_port': 39812,
                                            'remote': '0.0.0.0', 'remote_port': 0}]


def test_listening_sockets_are_left_out():
    text = ('  sl  local_address rem_address   st\n'
            '   0: 0B00007F:8E05 00000000:0000 0A 00000000\n'
            '   1: 040012AC:9B84 020012AC:1FA4 06 00000000\n')
    assert parse_proc_net(text, 'tcp') == [{'kind': 'tcp', 'local': '172.18.0.4', 'local_port': 39812,
                                            'remote': '172.18.0.2', 'remote_port': 8100}]


# ── what counts as leaving the Pi ──
@pytest.mark.parametrize('remote', ['127.0.0.1', '::1', '172.18.0.2', '::ffff:172.18.0.3'])
def test_the_container_and_dockers_network_stay_on_the_pi(remote):
    assert off_the_pi([connection(remote)], DOCKER, WIFI) == []


def test_a_udp_socket_that_can_send_anywhere_counts_unless_it_only_has_the_container_itself():
    socket = {'kind': 'udp', 'local': '172.18.0.4', 'local_port': 39812, 'remote': '0.0.0.0', 'remote_port': 0}
    assert off_the_pi([socket], DOCKER, WIFI) == [socket]
    assert off_the_pi([{**socket, 'local': '127.0.0.11'}], DOCKER, WIFI) == []


def test_a_page_opened_on_the_wifi_is_allowed():
    assert off_the_pi([connection('192.168.86.20', local_port=5000, remote_port=51000)], DOCKER, WIFI) == []


def test_a_page_on_the_wifi_is_told_from_the_rest():
    page = connection('192.168.86.20', local_port=5000)
    others = [connection('172.18.0.2', local_port=5000), connection('192.168.86.20')]
    assert pages_on_the_wifi([page, *others], WIFI) == [page]


@pytest.mark.parametrize('off', [connection('8.8.8.8'),                         # the internet
                                 connection('192.168.86.20'),                   # going out to the Wi-Fi by itself
                                 connection('8.8.8.8', local_port=5000),        # a page from outside the Wi-Fi
                                 connection('::ffff:8.8.8.8')])
def test_anything_else_leaves_the_pi(off):
    assert off_the_pi([off], DOCKER, WIFI) == [off]


# ── reading the data folder ──
def test_a_line_cut_off_by_a_power_cut_is_counted_not_fatal(tmp_path):
    path = tmp_path / 'events.jsonl'
    path.write_text('{"ts": 1}\n\n{"ts": 2\n[1]\n', encoding='utf-8')
    assert read_json_lines(path) == ([{'ts': 1}], 2)


def make_database(path, *, boot_ids: bool):
    with contextlib.closing(sqlite3.connect(path)) as db:
        boot = ', boot_id TEXT' if boot_ids else ''
        db.execute('CREATE TABLE events (id INTEGER PRIMARY KEY, ts REAL, type TEXT, object TEXT, '
                   f'new_state TEXT{boot})')
        db.execute("INSERT INTO events (ts, type, object, new_state) VALUES (100.0, 'snapshot', 'cup', 'PRESENT')")
        if boot_ids:
            db.execute("INSERT INTO events (ts, type, object, new_state, boot_id) "
                       "VALUES (101.0, 'appeared', 'pen', 'PRESENT', 'boot-a')")
        db.commit()


def test_the_saved_states_say_which_boot_saved_them(tmp_path):
    make_database(tmp_path / 'object_states.sqlite', boot_ids=True)
    assert check_data(tmp_path)['states']['rows'] == [
        {'object': 'cup', 'state': 'PRESENT', 'ts': 100.0, 'type': 'snapshot', 'boot_id': None},
        {'object': 'pen', 'state': 'PRESENT', 'ts': 101.0, 'type': 'appeared', 'boot_id': 'boot-a'}]


def test_a_database_from_before_boot_ids_can_still_be_read(tmp_path):
    make_database(tmp_path / 'object_states.sqlite', boot_ids=False)
    assert check_data(tmp_path)['states']['rows'] == [
        {'object': 'cup', 'state': 'PRESENT', 'ts': 100.0, 'type': 'snapshot', 'boot_id': None}]


def test_a_data_folder_with_no_database_says_so(tmp_path):
    (tmp_path / 'events' / 'day').mkdir(parents=True)
    (tmp_path / 'events' / 'day' / 'events.jsonl').write_text('{"ts": 1}\n', encoding='utf-8')
    assert check_data(tmp_path) == {'event_files': {str(Path('events/day/events.jsonl')): {'lines': 1, 'broken': 0}},
                                    'states': {'readable': False}}


# ── the run with no network ──
def test_a_good_run_has_no_problems():
    assert problems(good_run()) == []


@pytest.mark.parametrize(('changes', 'words'), [
    ({'never_offline': True}, 'never went away'),
    ({'finished_at': None}, 'never finished'),
    ({'pretend': True}, 'only a try'),
    ({'network_came_back': True}, 'came back'),
    ({'app_restart_ok': False}, "couldn't be restarted"),
    ({'samples': []}, 'settle'),
    ({'samples': [sample(after, 5) for after in range(120, 360, 10)]}, 'new pictures'),
    ({'samples': [sample(after, after, containers={'desktalk-app-1': 'unhealthy'}) for after in range(120, 360, 10)]},
     'new pictures'),
    ({'samples': [sample(after, after, ai=True) for after in range(120, 360, 10)]}, 'an AI is set up'),
    ({'samples': [sample(after, after, newest_detection_ts=900) for after in range(120, 360, 10)]},
     'event log'),
])
def test_each_way_a_run_can_go_wrong_is_named(changes, words):
    assert any(words in problem for problem in problems(good_run(**changes)))


def test_one_look_is_not_enough_to_say_it_stayed_healthy():
    assert any('settle' in problem for problem in problems(good_run(samples=[sample(120, 120)])))


def test_a_look_or_two_going_wrong_is_allowed():
    samples = [sample(after, after) for after in range(120, 360, 10)]
    samples[5] = sample(170, 170, app_answers=False)
    assert problems(good_run(samples=samples)) == []


@pytest.mark.parametrize(('counts', 'result'), [
    ({'said': 1, 'no_card': 1, 'could_not': 1}, 'said'),
    ({'said': 0, 'no_card': 1, 'could_not': 1}, 'could not'),
    ({'said': 0, 'no_card': 1, 'could_not': 0}, 'no card'),
    ({'said': 0, 'no_card': 0, 'could_not': 0}, 'nothing to say'),
    ({}, 'nothing to say'),
])
def test_what_the_log_shows_about_speaking(counts, result):
    assert speak_result({'speak': counts}) == result


# ── when the Wi-Fi came, from NetworkManager's lines in the journal ──
NM_START = '[    7.74] pi NetworkManager[885]: <info> device (lo): state change\n'
WIFI_UP = "[   13.12] pi NetworkManager[885]: <info> device (wlan0): Activation: successful, device activated.\n"


def test_the_wifi_time_comes_from_the_journal():
    assert first_wifi_after_boot(NM_START + WIFI_UP + WIFI_UP.replace('13.12', '90.00')) == 13.12


@pytest.mark.parametrize('journal', [
    '',                                                 # can't be read
    'Hint: You are currently not seeing messages from other users.\n',
    NM_START,                                           # no such line: a new wording, or it never connected
    WIFI_UP.replace('13.12', '900.00'),                 # the start of the boot has already gone from the journal
])
def test_when_the_journal_cant_say_the_wifi_time_is_unknown(journal):
    assert first_wifi_after_boot(journal) is None


# ── how the boot before this one ended ──
@pytest.mark.parametrize('line', ['systemd[1]: Reached target shutdown.target - System Shutdown.',
                                  'systemd[1]: Reached target reboot.target - System Reboot.',
                                  'systemd-shutdown[1]: Syncing filesystems and block devices.',
                                  'systemd-journald[300]: Journal stopped'])
def test_a_journal_that_got_to_the_shutdown_shows_it_was_clean(line):
    assert how_boot_ended(f'Nov 01 10:00:00 pi sshd[9]: something\nNov 01 10:00:01 pi {line}\n') == 'clean'


def test_a_journal_that_just_stops_shows_it_was_not():
    assert how_boot_ended('Nov 01 10:00:00 pi sshd[9]: something\n') == 'not clean'


@pytest.mark.parametrize('text', ['', '-- No entries --\n', 'Specifying boot ID: no persistent journal was found.\n'])
def test_no_earlier_journal_says_nothing(text):
    assert how_boot_ended(text) is None


# ── the app's log, which outlives each start ──
# Each run says "Picked up" once, early, before it's healthy. The tests only read the log once the app is healthy, so
# the last "Picked up" line starts this run. The times can't be used to tell runs apart: after a power cut the clock
# starts behind, so the run before can carry later times than this one.
RESTORED = 'INFO desktalk.core.object_states.tracker: Picked up {} object states from before the restart\n'
WARNED = ('{} WARNING desktalk.core.object_states.tracker: The clock is behind the newest saved object state by '
          'about {} seconds, so every object starts over\n')
RUN_1 = ('2026-11-01 10:00:00 ' + RESTORED.format(3) +
         '2026-11-01 10:00:01 INFO uvicorn.error: Started server process [7]\n')
STOPPED = '2026-11-01 10:05:00 INFO uvicorn.error: Shutting down\n'
RUN_2 = ('2026-11-01 10:06:00 ' + RESTORED.format(1) +
         '2026-11-01 10:06:01 INFO uvicorn.error: Started server process [7]\n')
# The run before a power cut, started (by update.sh, say) a few minutes before it, by a clock that was right
RUN_BEFORE_CUT = ('2026-11-01 10:20:00 ' + RESTORED.format(2) +
                  '2026-11-01 10:20:01 INFO uvicorn.error: Started server process [7]\n')
STOPPED_LATE = '2026-11-01 10:25:00 INFO uvicorn.error: Shutting down\n'
NOT_SYNCED = ("{} WARNING desktalk.core.object_states.tracker: The clock hasn't been set from the internet since "
              'this computer started, so every object starts over\n')


def test_only_the_line_from_this_start_counts():
    assert restored_at_this_start(RUN_1 + STOPPED + RUN_2) == 1


def test_this_start_is_the_last_one_in_the_file_even_when_the_run_before_has_later_times():
    assert restored_at_this_start(RUN_1 + RUN_BEFORE_CUT + RUN_2) == 1


def test_no_picked_up_line_says_nothing():
    assert restored_at_this_start('2026-11-01 10:06:01 INFO uvicorn.error: Started server process [7]\n') is None


def test_lines_with_no_time_on_them_dont_count():
    assert restored_at_this_start('Picked up 3 object states from before the restart\n') is None


@pytest.mark.parametrize(('log', 'stopped'), [
    (RUN_1 + STOPPED + RUN_2, True),                    # stopped before this start: a reboot or `docker stop`
    (RUN_1 + RUN_2, False),                             # no goodbye: the plug, or a kill
    (STOPPED + RUN_1 + RUN_2, False),                   # an older run's goodbye doesn't count
    (RUN_2, None),                                      # nothing ran before this
    (RUN_1 + RUN_BEFORE_CUT + RUN_2, False),            # a power cut: the run before has later times than this one
    (RUN_BEFORE_CUT + STOPPED_LATE + RUN_2, True),      # the same, with a goodbye
])
def test_did_the_run_before_this_start_stop_cleanly(log, stopped):
    assert app_stopped_cleanly(log) is stopped


def test_the_restore_time_is_the_time_on_this_runs_picked_up_line():
    assert restore_time_text(RUN_BEFORE_CUT + RUN_2) == '2026-11-01 10:06:00'
    assert restore_time_text(STOPPED) is None


def test_the_clock_behind_warning_comes_just_before_this_runs_picked_up_line():
    log = RUN_BEFORE_CUT + WARNED.format('2026-11-01 10:06:00', 840) + RUN_2
    assert clock_behind_warning_sec(log) == 840


def test_a_warning_from_an_earlier_run_doesnt_count():
    log = WARNED.format('2026-11-01 09:59:59', 300) + RUN_1 + RUN_2
    assert clock_behind_warning_sec(log) is None
    assert clock_behind_warning_sec(RUN_1 + RUN_2) is None


def test_the_not_synced_warning_comes_just_before_this_runs_picked_up_line():
    assert clock_not_synced_at_this_start(RUN_BEFORE_CUT + NOT_SYNCED.format('2026-11-01 10:06:00') + RUN_2) is True
    assert clock_not_synced_at_this_start(RUN_BEFORE_CUT + RUN_2) is False


def test_a_not_synced_warning_from_an_earlier_run_doesnt_count():
    assert clock_not_synced_at_this_start(NOT_SYNCED.format('2026-11-01 09:59:59') + RUN_1 + RUN_2) is False
    assert clock_not_synced_at_this_start(NOT_SYNCED.format('2026-11-01 09:59:59') + RUN_2) is True   # the only run


# ── when the app really started, whatever the Pi's clock says ──
def test_real_start_is_now_minus_uptime_plus_start_since_boot():
    # /proc/uptime then `date +%s.%N`: booted 1000.5 s ago; the app started 40 s after the boot
    assert real_start_time('1000.50 3000.0\n5000.5\n', 40.0) == pytest.approx(5000.5 - 1000.5 + 40.0)


# ── what the app should have picked up, all by the app's own clock ──
RESTORED_AT = 10_000.0      # the time on this run's "Picked up" line
MAX_AGE = 300.0
SLACK = 30.0


THIS_BOOT = 'boot-b'


def rows(boot_id: str | None = 'boot-a', **ages: float) -> list[dict[str, float | str | None]]:
    """Saved states, each `age` seconds before the restore, saved in `boot_id` (the boot before this one)."""
    return [{'object': name, 'ts': RESTORED_AT - age, 'boot_id': boot_id} for name, age in ages.items()]


def expect(saved, *, warned=None, lag=0.0, not_synced=False, this_boot: str | None = THIS_BOOT):
    return expected_restore(saved, restored_at=RESTORED_AT, warned_behind_sec=warned, not_synced=not_synced,
                            lag_sec=lag, restore_max_age_sec=MAX_AGE, slack_sec=SLACK, this_boot=this_boot)


def test_normal_start_uses_the_window_from_the_restore():
    e = expect(rows(fresh=20, edge=300, stale=400, old=900))
    assert e.case == 'normal'
    assert e.must_be == {'fresh'}               # 'edge' is within the slack either way
    assert e.could_be == {'fresh', 'edge'}      # 'stale' (400 s) is past 300 + 30


def test_normal_start_could_be_stops_at_the_slack():
    e = expect(rows(a=100, b=320, c=340))
    assert e.could_be == {'a', 'b'}
    assert e.must_be == {'a'}


def test_rows_this_run_wrote_after_its_restore_are_left_out():
    e = expect([{'object': 'new', 'ts': RESTORED_AT + 5}, {'object': 'old', 'ts': RESTORED_AT - 5}])
    assert e.could_be == {'old'} and e.must_be == {'old'}


def test_a_row_in_the_same_second_as_the_restore_may_be_old_or_new():
    # The log's times are whole seconds, so the restore was somewhere in the second after RESTORED_AT
    e = expect([{'object': 'same_second', 'ts': RESTORED_AT + 0.5}])
    assert e.could_be == {'same_second'}
    assert e.must_be == set()


def test_the_clock_behind_warning_means_exactly_zero():
    e = expect(rows(fresh=20), warned=840, lag=900.0)
    assert e.case == 'clock behind'
    assert e.must_be == set() and e.could_be == set()


def test_a_clock_not_yet_synced_means_exactly_zero_from_an_earlier_boot():
    """However slow the clock, and whatever looks recent by it. Rows saved before boot ids count as an earlier boot."""
    for lag in (0.0, 482_612.0):
        for saved in (rows(fresh=20), rows(None, fresh=20)):
            e = expect(saved, lag=lag, not_synced=True)
            assert e.case == 'clock not synced'
            assert e.must_be == set() and e.could_be == set()


def test_a_clock_not_yet_synced_still_brings_back_this_boots_rows():
    """Only the app restarted since the boot: what this boot saved comes back, by the window, whatever the lag."""
    saved = rows(THIS_BOOT, fresh=20, stale=400) + rows(earlier=10)
    e = expect(saved, lag=482_612.0, not_synced=True)
    assert e.case == 'clock not synced'
    assert e.must_be == {'fresh'} and e.could_be == {'fresh'}


def test_a_clock_behind_this_boots_rows_means_exactly_zero_even_before_it_has_synced():
    e = expect(rows(THIS_BOOT, fresh=20), warned=60, not_synced=True)
    assert e.case == 'clock behind'
    assert e.must_be == set() and e.could_be == set()


def test_with_no_boot_id_a_clock_not_yet_synced_means_exactly_zero():
    e = expect(rows(None, fresh=20), not_synced=True, this_boot=None)
    assert e.must_be == set() and e.could_be == set()


def test_a_slow_clock_the_app_didnt_notice_is_its_own_case():
    e = expect(rows(fresh=20), lag=SLACK + 1)
    assert e.case == 'clock behind, not noticed'


def test_a_lag_within_the_slack_is_a_normal_start():
    assert expect(rows(fresh=20), lag=SLACK - 1).case == 'normal'


def test_nothing_saved_in_the_window_is_its_own_case():
    e = expect(rows(old=900))
    assert e.case == 'nothing saved'
    assert e.could_be == set()
