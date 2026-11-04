"""The plain rules in tests/pi/on_the_pi.py, with no Pi: these run with the fast tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.pi.on_the_pi import (
    _address,
    app_stopped_cleanly,
    check_data,
    expected_restore,
    first_wifi_after_boot,
    how_boot_ended,
    off_the_pi,
    pages_on_the_wifi,
    parse_proc_net,
    parse_start_probe,
    problems,
    process_start_ticks,
    read_json_lines,
    real_start_time,
    restored_since,
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
RESTORED = 'INFO desktalk.core.object_states.tracker: Picked up {} object states from before the restart\n'
RUN_1 = ('2026-11-01 10:00:00 ' + RESTORED.format(3) +
         '2026-11-01 10:00:01 INFO uvicorn.error: Started server process [7]\n')
STOPPED = '2026-11-01 10:05:00 INFO uvicorn.error: Shutting down\n'
RUN_2 = ('2026-11-01 10:06:00 ' + RESTORED.format(1) +
         '2026-11-01 10:06:01 INFO uvicorn.error: Started server process [7]\n')


def test_only_a_line_from_this_start_counts():
    assert restored_since(RUN_1 + STOPPED + RUN_2, '2026-11-01 10:06:00') == 1


def test_an_older_line_doesnt_stand_in_for_this_start():
    assert restored_since(RUN_1 + STOPPED, '2026-11-01 10:06:00') is None


def test_lines_with_no_time_on_them_dont_count():
    assert restored_since('Picked up 3 object states from before the restart\n', '2026-11-01 10:06:00') is None


@pytest.mark.parametrize(('log', 'stopped'), [
    (RUN_1 + STOPPED + RUN_2, True),                    # stopped before this start: a reboot or `docker stop`
    (RUN_1 + RUN_2, False),                             # no goodbye: the plug, or a kill
    (STOPPED + RUN_1 + RUN_2, False),                   # an older run's goodbye doesn't count
    (RUN_2, None),                                      # nothing ran before this
])
def test_did_the_run_before_this_start_stop_cleanly(log, stopped):
    assert app_stopped_cleanly(log, '2026-11-01 10:06:00') is stopped


# ── when the app really started, whatever the Pi's clock says ──
def stat_line(name: str, ticks: int) -> str:
    """A /proc/<pid>/stat line: field 22 is the start time. After the name come 19 more fields (3 to 21)."""
    return f'123 ({name}) S ' + ' '.join(['1'] * 18) + f' {ticks} 5000 6000 18446744073709551615'


@pytest.mark.parametrize('name', ['python3', 'my app', 'a) b (c', 'x)'])
def test_process_start_ticks_ignores_the_brackets_in_the_name(name):
    assert process_start_ticks(stat_line(name, 98765)) == 98765


def test_real_start_is_now_minus_uptime_plus_start_since_boot():
    # booted 1000 s ago; the app started 40 s after the boot (4000 ticks at 100 per second)
    assert real_start_time(now=5000.0, uptime=1000.0, start_ticks=4000, ticks_per_sec=100) == pytest.approx(4040.0)


def test_parse_start_probe_reads_the_four_answers():
    out = f'{stat_line("python3", 4000)}\n1000.50 3000.0\n5000.5\n100\n'
    assert parse_start_probe(out) == pytest.approx(5000.5 - 1000.5 + 40.0)


# ── what the app should have picked up ──
START = 10_000.0        # when the app really started
MAX_AGE = 300.0
SLACK = 30.0


def rows(**ages: float) -> list[dict[str, float | str]]:
    """Saved states, each `age` seconds before the real start."""
    return [{'object': name, 'ts': START - age} for name, age in ages.items()]


def expect(saved, own_start=START):
    return expected_restore(saved, START, own_start, MAX_AGE, SLACK)


def test_normal_start_uses_the_window_from_the_real_start():
    e = expect(rows(fresh=20, edge=300, stale=400, old=900))
    assert e.case == 'normal'
    assert e.must_be == {'fresh'}               # 'edge' is within the slack either way
    assert e.could_be == {'fresh', 'edge'}      # 'stale' (400 s) is past 300 + 30
    assert e.behind_sec == 0


def test_normal_start_could_be_stops_at_the_slack():
    e = expect(rows(a=100, b=320, c=340))
    assert e.could_be == {'a', 'b'}
    assert e.must_be == {'a'}


def test_rows_after_the_real_start_are_left_out():
    e = expect([{'object': 'late', 'ts': START + 5}, {'object': 'ok', 'ts': START - 5}])
    assert e.could_be == {'ok'}


def test_clock_behind_means_exactly_zero():
    # the app's own clock thought it started 540 s earlier, so rows from 100 s before the real start look "newer"
    e = expect(rows(fresh=100), own_start=START - 540)
    assert e.case == 'clock behind'
    assert e.must_be == set() and e.could_be == set()
    assert e.behind_sec == pytest.approx(540)


def test_clock_behind_when_any_saved_row_is_newer_than_the_own_start():
    e = expect(rows(newer=100, older=600), own_start=START - 200)
    assert e.case == 'clock behind'


def test_nothing_saved_in_the_window_is_its_own_case():
    e = expect(rows(old=900))
    assert e.case == 'nothing saved'
    assert e.could_be == set()
