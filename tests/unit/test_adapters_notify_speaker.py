"""Saying warnings and reminders out loud through the Pi's speaker, with a stand-in for the speak command (only the
last tests run a real program, a small shell line, to check the time limit)."""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path

import pytest

from desktalk.adapters.notify import speaker as speaker_module
from desktalk.adapters.notify.mac import MacNotifications, NoNotifications, make_notifications
from desktalk.adapters.notify.queued import STOP, QueuedNotifications
from desktalk.adapters.notify.speaker import SpeakerNotifications, run_speak_command
from desktalk.config import load_config
from tests.fakes import FakeClock

SETTINGS = load_config(environ={}, dotenv=Path('/nonexistent')).notifications
USB_CARD = (' 0 [vc4hdmi0       ]: vc4-hdmi - vc4-hdmi-0\n'
            '                      vc4-hdmi-0\n'
            ' 2 [Device         ]: USB-Audio - USB PnP Sound Device\n'
            '                      C-Media Electronics Inc. USB PnP Sound Device at usb-xhci-hcd.0-1\n')
HDMI_ONLY = ' 0 [vc4hdmi0       ]: vc4-hdmi - vc4-hdmi-0\n                      vc4-hdmi-0\n'
WAIT_SEC = 5


class Recorder:
    """Stands in for the speak command, and keeps what it was asked to say."""

    def __init__(self, fail: Exception | None = None):
        self.calls: list[tuple[list[str], str, float]] = []
        self.fail = fail

    def __call__(self, args, text, time_limit):
        self.calls.append((list(args), text, time_limit))
        if self.fail:
            raise self.fail

    @property
    def said(self) -> list[str]:
        return [text for _, text, _ in self.calls]


def speaker(run_command, *, queue_size=4, clock=None, **changes):
    settings = SETTINGS.model_copy(update={'output': 'speaker', 'sound_card': '',
                                           'speak_command': ('speak', '--voice', '{voice}', '--on', '{device}'),
                                           'voice_model': '/voices/test.onnx', 'speak_time_limit_sec': 7.0, **changes})
    return SpeakerNotifications(settings, queue_size=queue_size, clock=clock or FakeClock(), run_command=run_command)


def said_by(run, *shows, **changes):
    """Everything the speaker gets asked to say after these show() calls."""
    voice = speaker(run, **changes)
    for text in shows:
        voice.show('Cup', text)
    voice.close()
    return run


def test_show_never_waits_or_raises():
    stuck, let_go = threading.Event(), threading.Event()

    def stuck_command(args, text, time_limit):
        stuck.set()
        let_go.wait(WAIT_SEC)

    voice = speaker(stuck_command)
    voice.show('Cup', 'one')
    assert stuck.wait(WAIT_SEC)
    started = time.monotonic()
    assert voice.show('Cup', 'two') is None
    assert time.monotonic() - started < 1
    let_go.set()
    voice.close()


def test_says_one_thing_at_a_time():
    inside, most = [0], [0]
    lock = threading.Lock()
    said = []

    def slow(args, text, time_limit):
        with lock:
            inside[0] += 1
            most[0] = max(most[0], inside[0])
        time.sleep(0.02)
        said.append(text)
        with lock:
            inside[0] -= 1

    said_by(slow, 'first', 'second', 'third')
    assert said == ['first', 'second', 'third'] and most[0] == 1


def test_full_queue_drops_and_logs(caplog):
    """Full: the oldest waiting one goes, not the new one, so a new warning isn't lost behind old reminders."""
    stuck, let_go = threading.Event(), threading.Event()
    said = []

    def stuck_command(args, text, time_limit):
        stuck.set()
        let_go.wait(WAIT_SEC)
        said.append(text)

    voice = speaker(stuck_command, queue_size=2)
    voice.show('Cup', 'being said')
    assert stuck.wait(WAIT_SEC)
    voice.show('Book', 'old reminder')
    voice.show('Plant', 'other reminder')
    with caplog.at_level(logging.WARNING):
        voice.show('Lens', 'new warning')
    assert 'dropped' in caplog.text and 'Book' in caplog.text
    let_go.set()
    voice.close()
    assert said == ['being said', 'other reminder', 'new warning']


def test_show_after_close_with_a_full_queue_never_raises():
    stuck, let_go = threading.Event(), threading.Event()

    def stuck_command(args, text, time_limit):
        stuck.set()
        let_go.wait(WAIT_SEC)

    voice = speaker(stuck_command, queue_size=1, speak_stop_wait_sec=0.01)
    voice.show('Cup', 'being said')
    assert stuck.wait(WAIT_SEC)
    voice._queue.put_nowait(STOP)        # noqa: SLF001  (as close() leaves it)
    assert voice.show('Cup', 'too late') is None
    let_go.set()
    voice._thread.join(WAIT_SEC)                                            # noqa: SLF001
    assert not voice._thread.is_alive()                                     # noqa: SLF001


def test_a_message_waiting_too_long_is_skipped_and_logged(caplog):
    clock = FakeClock()
    stuck, let_go = threading.Event(), threading.Event()
    said = []

    def stuck_command(args, text, time_limit):
        stuck.set()
        let_go.wait(WAIT_SEC)
        said.append(text)

    voice = speaker(stuck_command, clock=clock, speak_max_age_sec=20.0)
    voice.show('Cup', 'being said')
    assert stuck.wait(WAIT_SEC)
    voice.show('Book', 'too old by the time its turn comes')
    clock.advance(21)
    voice.show('Lens', 'new warning')
    with caplog.at_level(logging.WARNING):
        let_go.set()
        voice.close()
    assert said == ['being said', 'new warning']
    assert 'Book' in caplog.text and 'too old' not in caplog.text, 'names who, not the words'


def test_one_exactly_at_the_age_limit_is_still_said():
    clock = FakeClock()
    run = Recorder()
    stuck, let_go = threading.Event(), threading.Event()

    def held_up(args, text, time_limit):
        if not stuck.is_set():
            stuck.set()
            let_go.wait(WAIT_SEC)
        run(args, text, time_limit)

    voice = speaker(held_up, clock=clock, speak_max_age_sec=20.0)
    voice.show('Cup', 'first')
    assert stuck.wait(WAIT_SEC)
    voice.show('Cup', 'second')
    clock.advance(20)
    let_go.set()
    voice.close()
    assert run.said == ['first', 'second']


def test_a_bad_message_does_not_end_speech_for_good(monkeypatch, caplog):
    said = Recorder()
    real = speaker_module.words_to_say

    def breaks_on_bad(text):
        if text == 'bad':
            raise ValueError('cannot read this')
        return real(text)

    monkeypatch.setattr(speaker_module, 'words_to_say', breaks_on_bad)
    with caplog.at_level(logging.ERROR):
        said_by(said, 'bad', 'good')
    assert said.said == ['good']
    assert 'cannot read this' in caplog.text


def test_the_thread_guard_is_for_every_kind_of_queue(caplog):
    done = threading.Event()

    class Odd(QueuedNotifications):
        def _say(self, title, text):
            if title == 'bad':
                raise RuntimeError('odd')
            done.set()

    queued = Odd(queue_size=2, time_limit_sec=1)
    with caplog.at_level(logging.ERROR):
        queued.show('bad', 'x')
        queued.show('good', 'x')
        assert done.wait(WAIT_SEC)
    queued.close()


@pytest.mark.parametrize('text, words', [
    ('Time for a sip! [LOG_SIP] 💧', 'Time for a sip!'),
    ('[ADD_TODO:buy milk] Added 👍🏽 it', 'Added it'),
    ('Careful ⚠️ the cup!', 'Careful the cup!'),
    ('**Move** the *cup*', 'Move the cup'),
    ('Two\nlines', 'Two lines'),
    ('Sip logged [log_sip] there', 'Sip logged there'),
    ('Done [done_todo:milk]!', 'Done !'),
    ('Café déjà-vu, 3 sips', 'Café déjà-vu, 3 sips'),
])
def test_action_tags_and_emoji_are_not_read_out(text, words):
    assert said_by(Recorder(), text).said == [words]


def test_nothing_left_to_say_runs_nothing():
    assert said_by(Recorder(), '💧 [LOG_SIP] ✨', '').calls == []


def test_no_sound_device_is_only_logged(tmp_path, caplog):
    cards = tmp_path / 'cards'
    cards.write_text(HDMI_ONLY)
    run = Recorder()
    with caplog.at_level(logging.WARNING):
        said_by(run, 'one', 'two', 'three', sound_card='usb', sound_cards_file=cards)
    assert run.calls == []
    assert len([r for r in caplog.records if 'usb' in r.getMessage()]) == 1


def test_a_cards_file_that_is_not_there_counts_as_no_sound_device(tmp_path, caplog):
    run = Recorder()
    with caplog.at_level(logging.WARNING):
        said_by(run, 'one', 'two', sound_card='usb', sound_cards_file=tmp_path / 'missing')
    assert run.calls == [] and len(caplog.records) == 1


def test_a_speaker_plugged_in_later_is_found_with_no_restart(tmp_path, caplog):
    cards = tmp_path / 'cards'
    cards.write_text(HDMI_ONLY)
    spoke, run = threading.Event(), Recorder()

    def noting(args, text, time_limit):
        run(args, text, time_limit)
        spoke.set()

    voice = speaker(noting, sound_card='usb', sound_cards_file=cards)
    voice.show('Cup', 'nobody hears this')
    voice.close()
    voice = speaker(noting, sound_card='usb', sound_cards_file=cards)
    voice.show('Cup', 'still nothing')
    cards.write_text(USB_CARD)
    voice.show('Cup', 'now you hear me')
    assert spoke.wait(WAIT_SEC)
    voice.close()
    assert run.said[-1] == 'now you hear me'
    assert '--on' in run.calls[-1][0] and 'plughw:CARD=Device' in run.calls[-1][0]


def test_the_card_going_away_is_said_again_after_it_came_back(tmp_path, caplog):
    cards = tmp_path / 'cards'
    run = Recorder()

    def run_then_unplug(args, text, time_limit):
        run(args, text, time_limit)
        cards.write_text(HDMI_ONLY)

    cards.write_text(HDMI_ONLY)
    voice = speaker(run_then_unplug, sound_card='usb', sound_cards_file=cards)
    with caplog.at_level(logging.WARNING):
        voice._say('Cup', 'gone')              # noqa: SLF001  (one at a time, in order, with no thread timing)
        cards.write_text(USB_CARD)
        voice._say('Cup', 'back')              # noqa: SLF001
        voice._say('Cup', 'gone again')        # noqa: SLF001
    voice.close()
    assert run.said == ['back']
    assert len(caplog.records) == 2


def test_the_words_go_in_on_standard_input_and_the_blanks_get_filled():
    run = said_by(Recorder(), 'Mind the "cup"; $(rm -rf /)')
    [(args, text, limit)] = run.calls
    assert args == ['speak', '--voice', '/voices/test.onnx', '--on', 'default']
    assert text == 'Mind the "cup"; $(rm -rf /)' and limit == 7.0


def test_a_failing_command_is_logged_once_without_the_words(caplog):
    run = Recorder(fail=RuntimeError('aplay said no'))
    with caplog.at_level(logging.WARNING):
        said_by(run, 'private words', 'more private words')
    assert len(run.calls) == 2 and len(caplog.records) == 1
    assert 'aplay said no' in caplog.text and 'private' not in caplog.text


# ── choosing by notifications.output ──────────────────────────────────
def test_mac_output_on_a_mac_gives_pop_ups():
    chosen = make_notifications(SETTINGS, 'darwin', queue_size=2, clock=FakeClock(), runner=lambda args, **kw: None)
    assert isinstance(chosen, MacNotifications)
    chosen.close()


def test_a_computer_keeps_mac_pop_ups_by_default():
    assert SETTINGS.output == 'mac' and SETTINGS.command == 'osascript'


def test_mac_output_anywhere_else_gives_nothing_and_says_so(caplog):
    with caplog.at_level(logging.INFO):
        chosen = make_notifications(SETTINGS, 'linux', queue_size=2, clock=FakeClock())
    assert isinstance(chosen, NoNotifications)
    assert 'this computer' not in caplog.text and caplog.text


@pytest.mark.parametrize('platform', ['linux', 'darwin'])
def test_speaker_output_works_on_any_system(platform):
    chosen = make_notifications(SETTINGS.model_copy(update={'output': 'speaker'}), platform, queue_size=2,
                                clock=FakeClock(), run_command=Recorder())
    assert isinstance(chosen, SpeakerNotifications)
    chosen.close()


@pytest.mark.parametrize('changes', [{'output': 'none'}, {'output': 'speaker', 'enabled': False},
                                     {'output': 'mac', 'enabled': False}])
def test_none_or_switched_off_gives_nothing(changes):
    assert isinstance(make_notifications(SETTINGS.model_copy(update=changes), 'darwin', queue_size=2,
                                         clock=FakeClock()), NoNotifications)


def test_the_guard_stops_a_test_from_really_speaking(speak_calls):
    """A SpeakerNotifications made without its own command gets the recorder in tests/conftest.py."""
    chosen = make_notifications(SETTINGS.model_copy(update={'output': 'speaker', 'sound_card': ''}), 'linux',
                                queue_size=2, clock=FakeClock())
    assert isinstance(chosen, SpeakerNotifications)
    chosen.show('Cup', 'hello')
    chosen.close()
    assert [call[1] for call in speak_calls] == ['hello']
    speak_calls.clear()


# ── the real way of running the command ──────────────────────────────
def test_the_real_runner_gives_the_words_on_standard_input(tmp_path):
    out = tmp_path / 'said.txt'
    run_speak_command(['sh', '-c', 'cat > "$1"', 'speak', str(out)], 'Hello there', 5, stop_wait_sec=1)
    assert out.read_text().strip() == 'Hello there'


def test_the_real_runner_raises_with_the_last_line_of_what_went_wrong():
    with pytest.raises(RuntimeError, match='no such card'):
        run_speak_command(['sh', '-c', 'echo first >&2; echo no such card >&2; exit 3'], 'Hello', 5, stop_wait_sec=1)


def test_the_real_runner_stops_every_program_of_a_stuck_pipeline(tmp_path):
    alive = tmp_path / 'still-alive'
    line = f'(sleep 1; touch {alive}) | cat; sleep 30'
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        run_speak_command(['sh', '-c', line], 'Hello', 0.2, stop_wait_sec=1)
    assert time.monotonic() - started < 5
    time.sleep(1.2)
    assert not alive.exists()


ESCAPES = """
import subprocess, sys, time
subprocess.Popen(['sleep', '20'], start_new_session=True)      # out of reach of the stop, and holds stderr open
time.sleep(int(sys.argv[1]))
"""


def stop_sleepers():
    os.system("pkill -f '^sleep 20$'")                             # noqa: S605, S607  (this test's own stray program)


def test_a_child_that_escapes_and_holds_stderr_doesnt_hold_up_the_runner():
    started = time.monotonic()
    try:
        with pytest.raises(TimeoutError):
            run_speak_command([sys.executable, '-c', ESCAPES, '20'], 'Hello', 0.3, stop_wait_sec=0.2)
    finally:
        stop_sleepers()
    assert time.monotonic() - started < 3


def test_a_program_that_has_finished_but_left_a_child_holding_stderr_doesnt_either():
    started = time.monotonic()
    try:
        with pytest.raises(TimeoutError):
            run_speak_command([sys.executable, '-c', ESCAPES, '0'], 'Hello', 0.3, stop_wait_sec=0.2)
    finally:
        stop_sleepers()
    assert time.monotonic() - started < 3


def test_a_command_that_wont_die_is_walked_away_from(monkeypatch, tmp_path):
    """A program stuck deep in the system (aplay when a USB speaker is pulled out) can't be killed. Here the kill does
    nothing, which looks the same to the runner."""
    real_kill = os.killpg
    pids = []
    monkeypatch.setattr(speaker_module.os, 'killpg', lambda pid, sig: pids.append(pid))
    started = time.monotonic()
    try:
        with pytest.raises(TimeoutError, match='would not stop'):
            run_speak_command(['sleep', '30'], 'Hello', 0.2, stop_wait_sec=0.2)
    finally:
        for pid in pids:
            real_kill(pid, signal.SIGKILL)
    assert pids and time.monotonic() - started < 3


def test_the_speech_thread_goes_on_after_walking_away(monkeypatch):
    """Through the whole speaker: after the stuck one it says the next."""
    real_kill = os.killpg
    pids = []
    monkeypatch.setattr(speaker_module.os, 'killpg', lambda pid, sig: pids.append(pid))
    said = []

    def real_then_note(args, text, limit, stop_wait_sec=None):
        if text == 'stuck':
            run_speak_command(['sleep', '30'], text, 0.2, stop_wait_sec=0.2)
        said.append(text)

    voice = speaker(real_then_note)
    voice.show('Cup', 'stuck')
    voice.show('Cup', 'next')
    voice.close()
    for pid in pids:
        real_kill(pid, signal.SIGKILL)
    assert said == ['next']
