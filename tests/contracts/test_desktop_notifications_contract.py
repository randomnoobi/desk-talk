"""What every DesktopNotifications must do (desktalk/ports.py): show the computer's own pop-up, never raising and
never holding up the caller. The fake runs it here; the real Mac one joins as one more test class in this file
(it needs a `notifications` fixture that doesn't really show anything, e.g. with a fake runner)."""

from __future__ import annotations

import pytest

from desktalk.ports import DesktopNotifications
from tests.fakes import FakeDesktopNotifications


class DesktopNotificationsContract:
    """Subclass and give a `notifications` fixture."""

    @pytest.fixture
    def notifications(self):
        raise NotImplementedError

    def test_it_is_desktop_notifications(self, notifications):
        assert isinstance(notifications, DesktopNotifications)

    def test_show_returns_nothing(self, notifications):
        assert notifications.show('Cup', 'Time for some water?') is None

    @pytest.mark.parametrize('title, text', [('', ''), ('Lens', 'say "hi" \\ there'), ('Book', 'Ünïcode ✨ to-do'),
                                             ('Cup', 'a\nnew line'), ('Phone', 'x' * 5000)])
    def test_any_text_is_shown_without_raising(self, notifications, title, text):
        notifications.show(title, text)


class TestFakeDesktopNotifications(DesktopNotificationsContract):
    @pytest.fixture
    def notifications(self):
        return FakeDesktopNotifications()


def test_the_fake_remembers_what_it_showed():
    fake = FakeDesktopNotifications()
    fake.show('Cup', 'Sip logged')
    fake.show('Lens', 'The camera is covered')
    assert fake.shown == [('Cup', 'Sip logged'), ('Lens', 'The camera is covered')]
