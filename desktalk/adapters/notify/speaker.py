"""
Warnings and reminders said out loud (on the Pi, through Piper and a USB speaker).

show() never waits (queued.py): one background thread says one thing at a time, by running
notifications.speak_command with the words on its standard input. The words never go on the command line, so nothing
in them can be taken for a command. A new program for each message, so a speaker plugged in later needs no restart
and a stuck one can be stopped: the notes on the Pi's first run measured 1.52 s at worst to the first sound.

The sound card is looked for by name in notifications.sound_cards_file each time. No such card, or a command that
fails, is only logged, once until it next works, so the log doesn't fill.
"""

from __future__ import annotations

import logging
import os
import re
import signal
import subprocess
import unicodedata
from collections.abc import Callable, Sequence

from desktalk.adapters.notify.queued import QueuedNotifications
from desktalk.config import NotificationsSettings
from desktalk.core.chat.tracker_actions import strip_action_tags

log = logging.getLogger(__name__)

RunCommand = Callable[[Sequence[str], str, float], None]     # (the command, the words, the time limit)

ALSA_DEFAULT = 'default'                                     # the system's own choice, with no card named
CARD_LINE = re.compile(r'\s*\d+\s+\[([^\]\s]+)\s*\]')        # " 2 [Device   ]: USB-Audio - USB PnP Sound Device"
NOT_SAID = frozenset('*_`#~')                                # marks a message may use for bold and so on
EMOJI_PARTS = ('So', 'Sk', 'Cf', 'Cs', 'Co', 'Cn')           # Unicode's kinds of picture and invisible letter
EMOJI_VARIANTS = range(0xFE00, 0xFE10)                       # "show the last one as a picture"


def run_speak_command(args: Sequence[str], text: str, time_limit: float) -> None:
    """Run the command with `text` on its standard input. It gets a process group of its own, so when it's stuck
    every program in its pipeline is stopped, not only the first. Raises TimeoutError or RuntimeError when it fails."""
    process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                               text=True, start_new_session=True)
    try:
        _, errors = process.communicate(f'{text}\n', timeout=time_limit)
    except subprocess.TimeoutExpired as error:
        os.killpg(process.pid, signal.SIGKILL)
        process.communicate()
        raise TimeoutError(f'still going after {time_limit:g} s') from error
    if process.returncode:
        last_line = (errors or '').strip().splitlines()[-1:]
        raise RuntimeError(f'it stopped with code {process.returncode}: {" ".join(last_line)}')


def words_to_say(text: str) -> str:
    """`text` as it should be read out: no action tags, emoji or bold marks, all on one line."""
    kept = (c for c in strip_action_tags(text) if c not in NOT_SAID and ord(c) not in EMOJI_VARIANTS
            and unicodedata.category(c) not in EMOJI_PARTS)
    return ' '.join(''.join(kept).split())


class SpeakerNotifications(QueuedNotifications):
    """`run_command` is run_speak_command when not given (a test gives its own, so nothing is really said;
    tests/conftest.py makes sure of it)."""

    def __init__(self, settings: NotificationsSettings, *, queue_size: int, run_command: RunCommand | None = None):
        self._command = settings.speak_command
        self._voice = settings.voice_model
        self._card = settings.sound_card.strip().lower()
        self._cards_file = settings.sound_cards_file
        self._speak_limit = settings.speak_time_limit_sec
        self._run_command = run_command if run_command is not None else run_speak_command
        self._said_problem = ''         # the kind of problem already in the log, until it next works
        super().__init__(queue_size=queue_size, time_limit_sec=settings.speak_time_limit_sec)

    def _say(self, title: str, text: str) -> None:
        words = words_to_say(text)
        if not words:
            return
        device = self._device()
        if device is None:
            self._log_once('no card', 'No sound card with "%s" in its name, so nothing can be said out loud. It will '
                                      'be used as soon as it is plugged in.', self._card)
            return
        args = [part.replace('{voice}', self._voice).replace('{device}', device) for part in self._command]
        try:
            self._run_command(args, words, self._speak_limit)
        except Exception as error:      # noqa: BLE001  (any failure is only logged: the chat must go on)
            self._log_once(type(error).__name__, 'Could not say a message out loud (%s: %s). This is only logged '
                                                 'once until it works again.', type(error).__name__, error)
        else:
            self._said_problem = ''

    def _device(self) -> str | None:
        """Where to send the sound right now, or None when the card isn't there."""
        if not self._card:
            return ALSA_DEFAULT
        try:
            lines = self._cards_file.read_text(encoding='utf-8', errors='replace').splitlines()
        except OSError:
            lines = []
        card = None
        for line in lines:                  # each card has a line with its number and name, then one more about it
            found = CARD_LINE.match(line)
            card = found[1] if found else card
            if card and self._card in line.lower():
                return f'plughw:CARD={card}'
        return None

    def _log_once(self, kind: str, message: str, *args: object) -> None:
        if kind != self._said_problem:
            log.warning(message, *args)
        self._said_problem = kind
