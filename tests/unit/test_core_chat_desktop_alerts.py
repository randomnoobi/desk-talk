"""DesktopAlerts: the chat message always goes out first; warnings, reminders and camera-seen sips also pop up as the
computer's own notification (and go to your phone, when there is one), unless that kind (or all of them) is off or
quiet hours are on. The phone never gets a to-do's words."""

from __future__ import annotations

import calendar
from typing import Any

import pytest

from desktalk.config import NotificationsSettings
from desktalk.core.chat.desktop_alerts import DesktopAlerts
from desktalk.domain import LENS, AgentProfile
from desktalk.ports import Notifier
from tests.fakes import FakeClock, FakeDesktopNotifications, FakePreferences, FakeProfiles, RecordingNotifier

ALL_ON = dict(enabled=True, warnings=True, reminders=True, sips=True, time_limit_sec=5.0, command='osascript')
NOON = calendar.timegm((2026, 10, 21, 12, 0, 0, 0, 0, 0))
QUIET = {'quiet_enabled': True, 'quiet_start_hour': 22, 'quiet_end_hour': 7}
TODO_LINE = 'A to-do is due. Open Desk Talk to see it.'


class Rig:
    def __init__(self, prefs=None, desktop=None, phone=None, phone_todo: Any = TODO_LINE, **switches):
        self.clock = FakeClock(NOON)
        self.inner = RecordingNotifier(self.clock)
        self.desktop = desktop or FakeDesktopNotifications()
        self.phone: Any = phone                     # a FakeDesktopNotifications, Broken or None
        self.profiles = FakeProfiles([AgentProfile(id='cup', name='Cup'), AgentProfile(id='computer', name='Computer')])
        self.alerts = DesktopAlerts(self.inner, self.desktop, FakePreferences(prefs, self.clock), self.profiles,
                                    NotificationsSettings.model_construct(**{**ALL_ON, **switches}), self.clock,
                                    lens_name='Lens', sip_title='Sip logged', phone=phone, phone_todo=phone_todo)

    def send(self, **message):
        return self.alerts.send({'type': 'group_message', 'character': 'cup', 'text': 'Time for water', **message})


def test_it_is_a_notifier():
    assert isinstance(Rig().alerts, Notifier)


def test_reminder_message_pops_up():
    r = Rig()
    sent = r.send(notify='reminder')
    assert r.desktop.shown == [('Cup', 'Time for water')]
    assert sent == r.inner.sent[0] and 'id' in sent               # what the chat got is handed back
    assert 'notify' not in sent                                   # the mark is only for this part


def test_a_warning_pops_up_with_the_characters_name():
    r = Rig()
    r.send(character='computer', text='My keyboard is wet', notify='warning')
    assert r.desktop.shown == [('Computer', 'My keyboard is wet')]


def test_lens_pops_up_under_its_own_name():
    r = Rig()
    r.send(character=LENS, text='The camera is covered', notify='warning')
    assert r.desktop.shown == [('Lens', 'The camera is covered')]


def test_a_camera_sip_pops_up_with_the_sip_title():
    r = Rig()
    r.send(text="Sip detected! That's 3 today.", notify='sip')
    assert r.desktop.shown == [('Sip logged', "Sip detected! That's 3 today.")]


def test_join_note_does_not():
    r = Rig()
    r.send(type='roster', text='Cup joined the chat')
    r.send(hello=True)
    r.send(notify='')
    assert r.desktop.shown == [] and len(r.inner.sent) == 3


def test_an_unknown_kind_does_not():
    r = Rig()
    r.send(notify='gossip')
    assert r.desktop.shown == []


def test_action_tags_are_stripped_from_the_text():
    r = Rig()
    r.send(text='Nice, [LOG_SIP] one more [ADD_TODO:Buy milk]  today', notify='reminder')
    assert r.desktop.shown == [('Cup', 'Nice, one more today')]


def test_a_message_with_no_text_does_not_pop_up():
    r = Rig()
    r.send(text='[LOG_SIP]', notify='reminder')
    assert r.desktop.shown == []


def test_a_character_nobody_knows_is_shown_by_its_id():
    r = Rig()
    r.send(character='ghost', notify='warning')
    assert r.desktop.shown == [('ghost', 'Time for water')]


@pytest.mark.parametrize('kind', ['warning', 'reminder', 'sip'])
def test_quiet_hours_mute_everything(kind):
    r = Rig(prefs=QUIET)
    r.clock.advance(11 * 3600)                    # 23:00
    r.send(notify=kind)
    assert r.desktop.shown == [] and len(r.inner.sent) == 1


def test_outside_quiet_hours_it_pops_up():
    r = Rig(prefs=QUIET)
    r.send(notify='warning')
    assert len(r.desktop.shown) == 1


@pytest.mark.parametrize('switch, kind', [('warnings', 'warning'), ('reminders', 'reminder'), ('sips', 'sip')])
def test_kind_switch_off(switch, kind):
    r = Rig(**{switch: False})
    r.send(notify=kind)
    for other in {'warning', 'reminder', 'sip'} - {kind}:
        r.send(notify=other)
    assert len(r.desktop.shown) == 2 and len(r.inner.sent) == 3


def test_main_switch_off():
    r = Rig(enabled=False)
    for kind in ('warning', 'reminder', 'sip'):
        r.send(notify=kind)
    assert r.desktop.shown == [] and len(r.inner.sent) == 3


def test_chat_message_sent_even_if_desktop_raises():
    class Broken:
        def show(self, title, text):
            raise RuntimeError('no osascript')

    r = Rig(desktop=Broken())
    sent = r.send(notify='warning')
    assert sent['text'] == 'Time for water' and len(r.inner.sent) == 1


class Broken:
    def show(self, title, text):
        raise RuntimeError('no internet')


# ── the phone ─────────────────────────────────────────────────────────
def with_phone(**kw):
    return Rig(phone=FakeDesktopNotifications(), **kw)


@pytest.mark.parametrize('kind', ['warning', 'reminder', 'sip'])
def test_the_phone_gets_what_pops_up(kind):
    r = with_phone()
    r.send(text='Nice, [LOG_SIP] one more', notify=kind)
    assert r.phone.shown == r.desktop.shown and len(r.phone.shown) == 1


def test_the_phone_gets_nothing_that_does_not_pop_up():
    r = with_phone()
    r.send(type='roster', text='Cup joined the chat')
    r.send(notify='gossip')
    r.send(text='[LOG_SIP]', notify='reminder')
    assert r.phone.shown == [] and len(r.inner.sent) == 3


@pytest.mark.parametrize('kind', ['warning', 'reminder', 'sip'])
def test_quiet_hours_mute_the_phone_too(kind):
    r = with_phone(prefs=QUIET)
    r.clock.advance(11 * 3600)                    # 23:00
    r.send(notify=kind)
    assert r.phone.shown == []


@pytest.mark.parametrize('switch, kind', [('warnings', 'warning'), ('reminders', 'reminder'), ('sips', 'sip'),
                                          ('enabled', 'warning')])
def test_the_switches_hold_for_the_phone_too(switch, kind):
    r = with_phone(**{switch: False})
    r.send(notify=kind)
    assert r.phone.shown == []


def test_a_to_do_reminder_goes_to_the_phone_without_its_words():
    r = with_phone()
    r.send(character='book', text='"Pay the rent" is due today!', habit='tasks', notify='reminder')
    assert r.phone.shown == [('book', TODO_LINE)]
    assert r.desktop.shown == [('book', '"Pay the rent" is due today!')]        # the speaker or the Mac: its words
    assert r.inner.sent[0]['text'] == '"Pay the rent" is due today!'           # and the chat


def test_other_reminders_go_to_the_phone_with_their_words():
    r = with_phone()
    r.send(habit='hydration', notify='reminder')
    assert r.phone.shown == [('Cup', 'Time for water')]


def test_the_phone_gets_a_reminders_ready_made_words_never_the_ais():
    """An AI may have seen the to-do list, so its words for any reminder stay home: the phone gets the reminder's own
    ready-made words, while the chat and the speaker or the Mac get the AI's."""
    r = with_phone()
    r.send(character='book', text='Phone away, then "Pay the rent"?', habit='phone_free', notify='reminder',
           ready_text='Your phone has been out a while.')
    assert r.phone.shown == [('book', 'Your phone has been out a while.')]
    assert r.desktop.shown == [('book', 'Phone away, then "Pay the rent"?')]
    assert r.inner.sent[0]['text'] == 'Phone away, then "Pay the rent"?' and 'ready_text' not in r.inner.sent[0]


def test_a_phone_problem_stops_neither_the_chat_nor_the_pop_up():
    r = Rig(phone=Broken())
    sent = r.send(notify='warning')
    assert sent['text'] == 'Time for water' and len(r.inner.sent) == 1
    assert r.desktop.shown == [('Cup', 'Time for water')]


def test_a_pop_up_problem_does_not_stop_the_phone():
    r = Rig(desktop=Broken(), phone=FakeDesktopNotifications())
    r.send(notify='warning')
    assert r.phone.shown == [('Cup', 'Time for water')] and len(r.inner.sent) == 1


def test_without_a_to_do_line_a_to_do_reminder_does_not_go_to_the_phone():
    r = with_phone(phone_todo='')
    r.send(text='"Pay the rent" is due today!', habit='tasks', notify='reminder')
    assert r.phone.shown == [] and len(r.desktop.shown) == 1
