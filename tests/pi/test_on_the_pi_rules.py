"""The plain rules in tests/pi/on_the_pi.py, with no Pi: these run with the fast tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.pi.on_the_pi import (
    _address,
    check_data,
    off_the_pi,
    pages_on_the_wifi,
    parse_proc_net,
    problems,
    read_json_lines,
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
