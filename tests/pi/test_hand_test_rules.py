"""The plain rules in tests/pi/hand_test_rules.py, with no Pi: these run with the fast tests."""

from __future__ import annotations

from datetime import date

import pytest

from tests.pi.hand_test_rules import (
    StatusLine,
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

STATUS = ('Pictures taken {taken}, sent 1, failed 0, skipped 0; detector ok 1, busy 0, off 0; stored 9; 250 MB free; '
          '56.2 °C')


def docker_line(stamp: str, text: str) -> str:
    return f'{stamp} 21:26:03 {text}'


# ── the camera's status lines, from `docker logs -t` ──
def test_status_lines_are_read_with_dockers_time_in_front():
    text = '\n'.join([docker_line('2026-11-11T05:26:03.260764230Z', STATUS.format(taken=10)),
                      docker_line('2026-11-11T05:26:20.000000001Z', 'Camera problem: no camera'),
                      docker_line('2026-11-11T05:26:33.5Z', STATUS.format(taken=100))])
    lines = parse_status_lines(text)
    assert [line.taken for line in lines] == [10, 100]
    assert lines[1].at - lines[0].at == pytest.approx(30.239, abs=0.001)
    assert lines[0].at == pytest.approx(1794374763.26, abs=0.01)


def test_a_line_with_no_time_in_front_is_left_out():
    assert parse_status_lines(STATUS.format(taken=3)) == []


def test_pictures_taken_all_along_leave_no_long_quiet():
    lines = [StatusLine(at, taken) for at, taken in ((130, 10), (160, 20), (190, 30))]
    assert longest_quiet_sec(lines, since=100, now=200) == 30


def test_a_count_that_stops_going_up_is_quiet_even_with_status_lines():
    lines = [StatusLine(at, taken) for at, taken in ((110, 10), (140, 20), (170, 20), (200, 20), (230, 30))]
    assert longest_quiet_sec(lines, since=100, now=240) == 90


def test_no_status_line_at_all_is_quiet_the_whole_time():
    assert longest_quiet_sec([], since=100, now=400) == 300


def test_status_lines_from_before_the_note_only_give_the_count_to_start_from():
    lines = [StatusLine(50, 10), StatusLine(120, 10), StatusLine(150, 20)]
    assert longest_quiet_sec(lines, since=100, now=160) == 50


# ── what may and may not be gone ──
def test_a_day_folder_gone_since_the_note_is_named():
    assert days_gone(['2026-11-08', '2026-11-09', '2026-11-10'], ['2026-11-08', '2026-11-10'],
                     today=date(2026, 11, 10), keep_days=30) == ['2026-11-09']


def test_a_day_old_enough_for_the_usual_clean_up_may_go():
    # keep_days 3 on the 10th keeps the 8th, 9th and 10th; one more day is allowed in case the Pi's day is behind
    assert days_gone(['2026-11-06', '2026-11-07', '2026-11-08'], ['2026-11-08'],
                     today=date(2026, 11, 10), keep_days=3) == ['2026-11-07']


def test_a_folder_that_is_not_a_day_counts_when_gone():
    assert days_gone(['notes', '2026-11-10'], ['2026-11-10'], today=date(2026, 11, 10), keep_days=30) == ['notes']


def test_new_folders_are_fine():
    assert days_gone(['2026-11-10'], ['1990-01-01', '2026-11-10'], today=date(2026, 11, 10), keep_days=30) == []


def test_a_container_that_started_again_is_named():
    before = {'app': {'started_at': 'a', 'restart_count': 0}, 'camera': {'started_at': 'b', 'restart_count': 2}}
    now = {'app': {'started_at': 'a', 'restart_count': 0}, 'camera': {'started_at': 'c', 'restart_count': 3}}
    assert restarted_since(before, now) == ['camera']


def test_a_container_missing_now_counts_as_restarted():
    assert restarted_since({'app': {'started_at': 'a', 'restart_count': 0}}, {}) == ['app']


# ── the note taken before a hand test ──
def test_a_fresh_note_is_fine():
    assert note_problem({'mac_at': 1000.0}, mac_now=1000.0 + 3600, max_age_sec=86400) is None


def test_no_note_says_how_to_take_one():
    assert 'NOTE_BEFORE' in str(note_problem(None, mac_now=0.0, max_age_sec=86400))


def test_an_old_note_says_so():
    assert 'hours old' in str(note_problem({'mac_at': 0.0}, mac_now=90000.0, max_age_sec=86400))


# ── the Wi-Fi going away ──
def test_the_wifi_dropping_shows_in_networkmanagers_journal():
    journal = ('Nov 10 21:30:01 pi NetworkManager[600]: <info>  [1794375001.1] device (wlan0): state change: '
               'activated -> unavailable (reason \'none\', sys-iface-state: \'managed\')\n')
    assert wifi_dropped(journal, 'wlan0') is True


def test_another_device_dropping_is_not_the_wifi():
    journal = 'NetworkManager[600]: device (eth0): state change: activated -> unavailable\n'
    assert wifi_dropped(journal, 'wlan0') is False


def test_a_quiet_journal_means_it_never_dropped():
    assert wifi_dropped('-- No entries --\n', 'wlan0') is False


def test_a_journal_the_user_cannot_read_cannot_say():
    journal = 'Hint: You are currently not seeing messages from other users and the system.\n'
    assert wifi_dropped(journal, 'wlan0') is None


# ── SD card levels ──
@pytest.mark.parametrize(('free_mb', 'level'), [(5000, 'ok'), (2000, 'ok'), (1999, 'warning'), (500, 'warning'),
                                                 (499, 'serious')])
def test_free_space_falls_into_a_level(free_mb, level):
    assert card_level(free_mb, warning_mb=2000, serious_mb=500) == level


# ── what Lens said ──
def test_the_newest_warning_since_the_step_is_found():
    events = [{'key': 'lost', 'kind': 'problem', 'ts': 90, 'text': 'old'},
              {'key': 'lost', 'kind': 'fixed', 'ts': 120, 'text': 'back'},
              {'key': 'lost', 'kind': 'problem', 'ts': 130, 'text': 'new'},
              {'key': 'dark', 'kind': 'problem', 'ts': 140, 'text': 'other'}]
    assert warned_since(events, {'lost'}, since=100) == events[2]


def test_no_warning_since_the_step_is_none():
    assert warned_since([{'key': 'lost', 'kind': 'problem', 'ts': 90}], {'lost'}, since=100) is None


# ── a clock the app shouldn't believe ──
PICKED_UP = '2026-11-10 21:00:{s} INFO desktalk.app: Picked up {n} object states from before the restart'


def test_a_clock_behind_the_saved_states_is_not_believed():
    log = '\n'.join([PICKED_UP.format(s='00', n=2),
                     '1990-01-01 12:00:01 WARNING desktalk.app: The clock is behind the newest saved object state by '
                     'about 1130000000 seconds',
                     PICKED_UP.format(s='02', n=0).replace('2026-11-10 21:00', '1990-01-01 12:00')])
    assert clock_distrusted(log) is True


def test_a_clock_the_app_said_nothing_about_is_believed():
    log = '\n'.join([PICKED_UP.format(s='00', n=2), PICKED_UP.format(s='30', n=2)])
    assert clock_distrusted(log) is False


def test_a_warning_from_an_earlier_start_does_not_count():
    log = '\n'.join(['2026-11-10 20:59:59 WARNING desktalk.app: The clock is behind the newest saved object state by '
                     'about 30 seconds',
                     PICKED_UP.format(s='00', n=0), PICKED_UP.format(s='30', n=2)])
    assert clock_distrusted(log) is False


# The words of ClockCheck in desktalk/runtime/clock.py
UNBELIEVABLE = ('1990-01-01 12:00:01 WARNING desktalk.runtime.clock: The clock says 1990-01-01, before '
                'clock.earliest_believable_date (2025-01-01), so it must be wrong: nothing is deleted by date, and '
                'saved object states are not picked up, until it is right')


def test_a_clock_before_the_earliest_believable_date_is_not_believed():
    log = '\n'.join([PICKED_UP.format(s='00', n=2), UNBELIEVABLE,
                     PICKED_UP.format(s='02', n=0).replace('2026-11-10 21:00', '1990-01-01 12:00')])
    assert clock_distrusted(log) is True
    assert said_clock_unbelievable(log) is True


def test_a_clock_only_behind_the_saved_states_was_not_called_unbelievable():
    log = '\n'.join([PICKED_UP.format(s='00', n=2),
                     '1990-01-01 12:00:01 WARNING desktalk.app: The clock is behind the newest saved object state by '
                     'about 1130000000 seconds',
                     PICKED_UP.format(s='02', n=0).replace('2026-11-10 21:00', '1990-01-01 12:00')])
    assert said_clock_unbelievable(log) is False


def test_an_unbelievable_clock_at_an_earlier_start_does_not_count():
    log = '\n'.join([UNBELIEVABLE, PICKED_UP.format(s='00', n=0), PICKED_UP.format(s='30', n=2)])
    assert said_clock_unbelievable(log) is False


# ── timed jobs ──
def test_a_job_whose_next_run_is_far_past_its_interval_is_stuck():
    health = {'scheduler': [{'name': 'compact event log', 'every_sec': 600.0, 'next_in_sec': 501},
                            {'name': 'save object states', 'every_sec': 60.0, 'next_in_sec': 1_136_000_000},
                            {'name': 'not run yet', 'every_sec': 60.0, 'next_in_sec': None}]}
    assert stuck_jobs(health, slack_sec=60) == ['save object states']


def test_an_app_that_does_not_answer_has_no_jobs_to_be_stuck():
    assert stuck_jobs(None, slack_sec=60) == []
