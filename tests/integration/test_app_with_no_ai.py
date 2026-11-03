"""The whole app as the Pi runs it (speaker on, chat.ai_writes_warnings off, a short llm.reminder_wait_sec): warnings
and reminders are said in ready-made words, with no AI at all or with one that has gone away. Review Focus 5 of the
Pi standalone plan: warnings never wait for an AI, and a reminder waits only briefly."""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from desktalk.adapters.notify.speaker import SpeakerNotifications
from desktalk.domain import Alert, Nudge
from tests.fakes import FakeClock
from tests.integration.app_kit import AFTERNOON, CUP_FAR, LAPTOP, DeskRig, changed, desk_config
from tests.integration.test_app_jobs import small_talk_ai

WAIT_SEC = 0.05                 # the test's llm.reminder_wait_sec, in real time
PLENTY_SEC = 5                  # longer than any of this should take
EVIDENCE = '35 minutes of sitting'
READY_WORDS = '35 minutes of sitting. Time for a 5-minute walk?'
DAY_SEC = 86_400


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
    speaker = SpeakerNotifications(config.notifications, queue_size=4, clock=FakeClock(), run_command=words)
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


def test_no_ai_makes_no_network_calls(tmp_path, monkeypatch):
    """The real client maker, and any real try at the network fails the test."""
    tried = []

    def no_network(*args: Any, **kw: Any) -> None:
        tried.append(args)
        raise OSError('no network in this test')

    monkeypatch.setattr(socket.socket, 'connect', no_network)
    monkeypatch.setattr(socket.socket, 'connect_ex', no_network)
    monkeypatch.setattr(socket, 'getaddrinfo', no_network)
    with pytest.raises(OSError, match='no network'):
        socket.create_connection(('example.org', 80))
    tried.clear()
    rig, _, speaker = rig_with_speaker(tmp_path, config=pi_config(tmp_path), client_factory=None)
    fire_warning_and_reminder(rig)
    rig.tick(rig.app.idle_chat, after=rig.config.idle_chat.max_sec)
    speaker.close()
    assert tried == [] and rig.ai_clients == []


def test_warning_does_not_wait_for_a_missing_ai(tmp_path):
    rig = on_the_desk(DeskRig(tmp_path, config=pi_config(tmp_path, ai=True),
                              ai_reply=ConnectionError('Connection refused')))
    before = rig.clock.now()
    rig.app.bus.publish(warning())
    assert rig.clock.now() == before, 'the words went out at the same moment'
    assert len(rig.desktop.shown) == 1
    assert all(not client.requests for client in rig.ai_clients), 'the AI was never asked'


def test_on_the_real_background_path_a_warning_goes_out_though_the_ai_never_answers(tmp_path):
    never = threading.Event()
    answered: list[Any] = []

    def never_answers(request: Any) -> str:
        answered.append(request)
        never.wait(PLENTY_SEC)
        return 'Too late, sorry about that, friend.'

    rig = DeskRig(tmp_path, config=pi_config(tmp_path, ai=True), ai_reply=never_answers, synchronous_events=False)
    try:
        rig.watch(10, LAPTOP, CUP_FAR)
        assert rig.app.bus.wait_idle(PLENTY_SEC)
        rig.desktop.shown.clear()
        started = time.monotonic()
        rig.app.bus.publish(warning())
        while not rig.desktop.shown and time.monotonic() - started < PLENTY_SEC:
            time.sleep(0.005)
        took = time.monotonic() - started
    finally:
        never.set()
        rig.app.bus.close(PLENTY_SEC)
    assert rig.desktop.shown and took < 1 and answered == []


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




# ── with the cloud server as the AI, only the chat you asked for goes there ──
CLOUD = 'https://cloud.example.org'


def cloud_rig(tmp_path: Path, *, linked: bool = True) -> DeskRig:
    """The characters on the desk, llm.ai_url the cloud server's, and warnings written by AI if they could be, so
    nothing but the guard keeps them from going there."""
    config = changed(desk_config(tmp_path), 'llm', ai_url=f'{CLOUD}/v1', reminder_wait_sec=PLENTY_SEC)
    config = changed(config, 'agents', invent_personality=True)
    rig = DeskRig(tmp_path, config=config, ai_reply='A fine line that is long enough to be kept, thank you very much.')
    if linked:
        link(rig)
    return on_the_desk(rig)


def link(rig: DeskRig) -> None:
    rig.app.store.save(rig.config.cloud.link_key, {'server': CLOUD, 'device_id': 'pi-1', 'token': 'device-token'})


def sent(rig: DeskRig) -> list[Any]:
    return [request for client in rig.ai_clients for request in client.requests]


def test_with_the_cloud_as_the_ai_a_reminder_uses_its_ready_made_words_and_calls_nothing(tmp_path):
    rig = cloud_rig(tmp_path)
    remind(rig)
    assert rig.desktop.shown[-1][1] == READY_WORDS and sent(rig) == []


def test_with_the_cloud_as_the_ai_a_warning_calls_nothing(tmp_path):
    rig = cloud_rig(tmp_path)
    rig.app.bus.publish(warning())
    assert rig.desktop.shown and sent(rig) == []


def small_talk_round(tmp_path: Path, ai_url: str, *, on_my_computer: bool = False) -> DeskRig:
    """Everything small talk needs, including a to-do in a character's memory, for two rounds of small talk."""
    config = changed(desk_config(tmp_path), 'llm', ai_url=ai_url, ai_on_my_computer=on_my_computer)
    config = changed(config, 'reminders', warmup_min=1)
    config = changed(config, 'idle_chat', min_sec=30, max_sec=30)
    rig = DeskRig(tmp_path, config=config, ai_reply=small_talk_ai(config.prompts.small_talk))
    link(rig)
    rig.watch(70, LAPTOP, CUP_FAR, step=1.0)
    rig.app.character_memory.remember('cup', 'Buy milk for the party', 5)
    wait = rig.tick(rig.app.idle_chat)
    for _ in range(2):
        rig.watch(wait, LAPTOP, CUP_FAR, step=1.0)
        wait = rig.tick(rig.app.idle_chat)
    return rig


def test_with_the_cloud_as_the_ai_idle_chat_calls_nothing(tmp_path):
    assert sent(small_talk_round(tmp_path, f'{CLOUD}/v1')) == []


def test_with_ollama_on_your_own_computer_idle_chat_still_talks(tmp_path):
    assert sent(small_talk_round(tmp_path, 'http://laptop.test:11434/v1', on_my_computer=True))


def test_an_ai_url_not_marked_as_on_your_computer_gets_no_small_talk(tmp_path):
    assert sent(small_talk_round(tmp_path, 'http://laptop.test:11434/v1')) == []


def test_with_the_cloud_as_the_ai_the_recap_and_a_lost_item_call_nothing(tmp_path):
    rig = cloud_rig(tmp_path)
    jobs: Any = rig.app
    assert not jobs.daily_summary._chat.ready and not jobs.lost_items._chat.ready        # noqa: SLF001
    rig.tick(rig.app.lost_items)
    rig.tick(rig.app.lost_items, after=rig.config.lost_items.items['keys'].alert_after_min * 60)
    rig.tick(rig.app.daily_summary, after=DAY_SEC)
    rig.tick(rig.app.daily_summary, after=60)
    assert sent(rig) == []


def test_with_the_cloud_as_the_ai_chat_you_asked_for_does_go(tmp_path):
    rig = cloud_rig(tmp_path)
    reply = rig.app.generator.answer('cup', 'How is your day going?')
    assert reply['messages'] and len(sent(rig)) == 1
    assert [c.api_key for c in rig.ai_clients if c.requests] == ['device-token']


def test_before_linking_the_cloud_servers_address_gets_no_reminders_either(tmp_path):
    rig = cloud_rig(tmp_path, linked=False)
    remind(rig)
    assert rig.desktop.shown[-1][1] == READY_WORDS and sent(rig) == []


def test_linking_later_stops_the_unprompted_calls_with_no_restart(tmp_path):
    """Marked as on your computer, then linked: the link wins, with no restart."""
    config = changed(desk_config(tmp_path), 'llm', ai_url=f'{CLOUD}/v1', ai_on_my_computer=True,
                     reminder_wait_sec=PLENTY_SEC)
    rig = on_the_desk(DeskRig(tmp_path, config=config, ai_reply='Time to stretch those legs, you have been here ages.'))
    remind(rig)
    before = len(sent(rig))
    assert before == 1, 'not linked and marked as on your computer, so it counts as your own AI'
    link(rig)
    remind(rig)
    assert len(sent(rig)) == before


def test_ollama_on_your_own_computer_still_gets_reminders(tmp_path):
    config = changed(desk_config(tmp_path), 'llm', ai_url='http://laptop.test:11434/v1', ai_on_my_computer=True,
                     reminder_wait_sec=PLENTY_SEC)
    rig = on_the_desk(DeskRig(tmp_path, config=config, ai_reply='Time to stretch those legs, you have been here ages.'))
    link(rig)
    remind(rig)
    assert rig.desktop.shown[-1][1] == 'Time to stretch those legs, you have been here ages.'
