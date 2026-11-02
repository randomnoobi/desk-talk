"""The whole app as the Pi runs it (speaker on, chat.ai_writes_warnings off, a short llm.reminder_wait_sec): warnings
and reminders are said in ready-made words, with no AI at all or with one that has gone away. Review Focus 5 of the
Pi standalone plan: warnings never wait for an AI, and a reminder waits only briefly."""

from __future__ import annotations

import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from desktalk.adapters.notify.speaker import SpeakerNotifications
from desktalk.domain import Alert, Nudge
from tests.integration.app_kit import AFTERNOON, CUP_FAR, LAPTOP, DeskRig, changed, desk_config

WAIT_SEC = 0.05                 # the test's llm.reminder_wait_sec, in real time
PLENTY_SEC = 5                  # longer than any of this should take
EVIDENCE = '35 minutes of sitting'
READY_WORDS = '35 minutes of sitting. Time for a 5-minute walk?'


def pi_config(tmp_path: Path, *, ai: bool = False, reminder_wait_sec: float = WAIT_SEC):
    config = desk_config(tmp_path, ai=ai)
    config = changed(config, 'chat', ai_writes_warnings=False)
    config = changed(config, 'llm', reminder_wait_sec=reminder_wait_sec)
    return changed(config, 'notifications', output='speaker', sound_card='')


class Words:
    """Stands in for the speak command."""

    def __init__(self) -> None:
        self.said: list[str] = []

    def __call__(self, args: Sequence[str], text: str, time_limit: float) -> None:
        self.said.append(text)


def on_the_desk(rig: DeskRig) -> DeskRig:
    """The laptop and the cup join the chat, so the laptop can say the warning and the reminder."""
    rig.watch(10, LAPTOP, CUP_FAR)
    rig.messages()
    return rig


def rig_with_speaker(tmp_path: Path, **kw: Any) -> tuple[DeskRig, Words, SpeakerNotifications]:
    words = Words()
    config = kw.pop('config')
    speaker = SpeakerNotifications(config.notifications, queue_size=4, run_command=words)
    return on_the_desk(DeskRig(tmp_path, config=config, desktop=speaker, **kw)), words, speaker


def walk_nudge() -> Nudge:
    return Nudge(habit='breaks', key='sitting', evidence=EVIDENCE, text=READY_WORDS, repeat_after_sec=0)


def warning() -> Alert:
    return Alert(ts=AFTERNOON, kind='proximity', agent='computer', extra={'near': 'cup'})


def remind(rig: DeskRig) -> None:
    """A reminder goes out, the way the Demo tab sends one."""
    reminders: Any = rig.app.reminders
    reminders.say(walk_nudge(), rig.clock.now())


def fire_warning_and_reminder(rig: DeskRig) -> None:
    rig.app.bus.publish(warning())
    remind(rig)


def test_warning_and_reminder_speak_with_no_ai(tmp_path):
    rig, words, speaker = rig_with_speaker(tmp_path, config=pi_config(tmp_path))
    fire_warning_and_reminder(rig)
    speaker.close()
    assert not rig.app.ai.ready
    assert len(words.said) == 2 and words.said[1] == READY_WORDS
    laptop, cup = rig.app.profiles.get('computer'), rig.app.profiles.get('cup')
    assert laptop and cup and words.said[0] == laptop.too_close_text(cup.name)


def test_no_ai_makes_no_network_calls(tmp_path):
    rig, _, speaker = rig_with_speaker(tmp_path, config=pi_config(tmp_path))
    fire_warning_and_reminder(rig)
    speaker.close()
    assert rig.ai_clients == []


def test_warning_does_not_wait_for_a_missing_ai(tmp_path):
    rig = on_the_desk(DeskRig(tmp_path, config=pi_config(tmp_path, ai=True),
                              ai_reply=ConnectionError('Connection refused')))
    before = rig.clock.now()
    rig.app.bus.publish(warning())
    assert rig.clock.now() == before, 'the words went out at the same moment'
    assert len(rig.desktop.shown) == 1
    assert all(not client.requests for client in rig.ai_clients), 'the AI was never asked'


def test_a_computer_still_has_the_ai_write_its_warnings(tmp_path):
    rig = on_the_desk(DeskRig(tmp_path, ai_reply='Careful there, that cup is creeping up on my keyboard today.'))
    rig.app.bus.publish(warning())
    assert rig.desktop.shown[0][1] == 'Careful there, that cup is creeping up on my keyboard today.'


def test_reminder_waits_at_most_reminder_wait_sec(tmp_path):
    never = threading.Event()

    def never_answers(_request: Any) -> str:
        never.wait(PLENTY_SEC)
        return 'Too late, sorry about that, friend.'

    rig = on_the_desk(DeskRig(tmp_path, config=pi_config(tmp_path, ai=True), ai_reply=never_answers))
    started = time.monotonic()
    remind(rig)
    took = time.monotonic() - started
    never.set()
    assert took < 1
    assert rig.desktop.shown[-1][1] == READY_WORDS
    assert any(client.requests for client in rig.ai_clients), 'it did ask'


def test_a_reminder_uses_the_ais_words_when_they_come_in_time(tmp_path):
    rig = on_the_desk(DeskRig(tmp_path, config=pi_config(tmp_path, ai=True, reminder_wait_sec=PLENTY_SEC),
                              ai_reply='Time to stretch those legs, you have been here ages.'))
    remind(rig)
    assert rig.desktop.shown[-1][1] == 'Time to stretch those legs, you have been here ages.'


def test_quiet_hours_stay_silent(tmp_path):
    rig, words, speaker = rig_with_speaker(tmp_path, config=pi_config(tmp_path))
    hour = rig.clock.local_hour(rig.clock.now())
    rig.app.preferences.update({'quiet_enabled': True, 'quiet_start_hour': hour - 1, 'quiet_end_hour': hour + 1})
    assert rig.app.preferences.quiet_now(rig.clock.now())
    fire_warning_and_reminder(rig)
    speaker.close()
    assert words.said == []


def test_the_pictures_stay_home_when_the_setting_says_so(tmp_path):
    config = changed(desk_config(tmp_path, ollama=True), 'llm', send_pictures=False)
    rig = DeskRig(tmp_path, config=config, ai_reply='Hello there, this desk looks good.', ollama=True)
    assert rig.app.ai.ready and not rig.app.ai.local_vision
    lost_items: Any = rig.app.lost_items
    assert lost_items._vision is None                                  # noqa: SLF001
    assert len(rig.ai_clients) == 1, 'no client for pictures'


def test_ollama_still_looks_at_pictures_on_a_computer(tmp_path):
    rig = DeskRig(tmp_path, ai_reply='Hello there, this desk looks good.', ollama=True)
    lost_items: Any = rig.app.lost_items
    assert lost_items._vision is not None                              # noqa: SLF001


