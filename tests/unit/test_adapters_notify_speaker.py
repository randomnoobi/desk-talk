"""Saying warnings and reminders out loud through the Pi's speaker, with a stand-in for the speak command (only the
last tests run a real program, a small shell line, to check the time limit)."""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

import pytest

from desktalk.adapters.notify.mac import MacNotifications, NoNotifications, make_notifications
from desktalk.adapters.notify.speaker import SpeakerNotifications, run_speak_command
from desktalk.config import load_config

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


def speaker(run_command, *, queue_size=4, **changes):
    settings = SETTINGS.model_copy(update={'output': 'speaker', 'sound_card': '',
                                           'speak_command': ('speak', '--voice', '{voice}', '--on', '{device}'),
                                           'voice_model': '/voices/test.onnx', 'speak_time_limit_sec': 7.0, **changes})
    return SpeakerNotifications(settings, queue_size=queue_size, run_command=run_command)


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
    stuck, let_go = threading.Event(), threading.Event()
    said = []

    def stuck_command(args, text, time_limit):
        stuck.set()
        let_go.wait(WAIT_SEC)
        said.append(text)

    voice = speaker(stuck_command, queue_size=1)
    voice.show('Cup', 'being said')
    assert stuck.wait(WAIT_SEC)
    voice.show('Cup', 'waiting')
    with caplog.at_level(logging.WARNING):
        voice.show('Lens', 'dropped')
    assert 'dropped' in caplog.text and 'Lens' in caplog.text
    let_go.set()
    voice.close()
    assert said == ['being said', 'waiting']


@pytest.mark.parametrize('text, words', [
    ('Time for a sip! [LOG_SIP] 💧', 'Time for a sip!'),
    ('[ADD_TODO:buy milk] Added 👍🏽 it', 'Added it'),
    ('Careful ⚠️ the cup!', 'Careful the cup!'),
    ('**Move** the *cup*', 'Move the cup'),
    ('Two\nlines', 'Two lines'),
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
    chosen = make_notifications(SETTINGS, 'darwin', queue_size=2, runner=lambda args, **kw: None)
    assert isinstance(chosen, MacNotifications)
    chosen.close()


def test_a_computer_keeps_mac_pop_ups_by_default():
    assert SETTINGS.output == 'mac' and SETTINGS.command == 'osascript'


def test_mac_output_anywhere_else_gives_nothing_and_says_so(caplog):
    with caplog.at_level(logging.INFO):
        chosen = make_notifications(SETTINGS, 'linux', queue_size=2)
    assert isinstance(chosen, NoNotifications)
    assert 'this computer' not in caplog.text and caplog.text


@pytest.mark.parametrize('platform', ['linux', 'darwin'])
def test_speaker_output_works_on_any_system(platform):
    chosen = make_notifications(SETTINGS.model_copy(update={'output': 'speaker'}), platform, queue_size=2,
                                run_command=Recorder())
    assert isinstance(chosen, SpeakerNotifications)
    chosen.close()


@pytest.mark.parametrize('changes', [{'output': 'none'}, {'output': 'speaker', 'enabled': False},
                                     {'output': 'mac', 'enabled': False}])
def test_none_or_switched_off_gives_nothing(changes):
    assert isinstance(make_notifications(SETTINGS.model_copy(update=changes), 'darwin', queue_size=2), NoNotifications)


def test_the_guard_stops_a_test_from_really_speaking(speak_calls):
    """A SpeakerNotifications made without its own command gets the recorder in tests/conftest.py."""
    chosen = make_notifications(SETTINGS.model_copy(update={'output': 'speaker', 'sound_card': ''}), 'linux',
                                queue_size=2)
    assert isinstance(chosen, SpeakerNotifications)
    chosen.show('Cup', 'hello')
    chosen.close()
    assert [call[1] for call in speak_calls] == ['hello']
    speak_calls.clear()


# ── the real way of running the command ──────────────────────────────
def test_the_real_runner_gives_the_words_on_standard_input(tmp_path):
    out = tmp_path / 'said.txt'
    run_speak_command(['sh', '-c', 'cat > "$1"', 'speak', str(out)], 'Hello there', 5)
    assert out.read_text().strip() == 'Hello there'


def test_the_real_runner_raises_with_the_last_line_of_what_went_wrong():
    with pytest.raises(RuntimeError, match='no such card'):
        run_speak_command(['sh', '-c', 'echo first >&2; echo no such card >&2; exit 3'], 'Hello', 5)


def test_the_real_runner_stops_every_program_of_a_stuck_pipeline(tmp_path):
    alive = tmp_path / 'still-alive'
    line = f'(sleep 1; touch {alive}) | cat; sleep 30'
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        run_speak_command(['sh', '-c', line], 'Hello', 0.2)
    assert time.monotonic() - started < 5
    time.sleep(1.2)
    assert not alive.exists()
