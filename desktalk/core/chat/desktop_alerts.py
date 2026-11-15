"""
DesktopAlerts: a Notifier that also pops up the computer's own notification.

It wraps the chat's Notifier (the message hub). send() hands the message to the hub first (without the `notify`
mark, which the pages don't need), so the chat never waits. Then, for a message marked `notify` = 'warning',
'reminder' or 'sip' (danger alerts and Lens's camera warnings, habit reminders, and sips the camera saw), it
shows a pop-up: the character's name as the title (a sip uses the "Sip logged" title) and the message's words,
without any action tags, as the text. Nothing pops up when notifications are off, that kind is off, or it is quiet
hours. Joins, greetings, small talk and replies are never marked.

With a `phone` (ntfy, once NTFY_TOPIC is set), the same ones go there too, with two changes, since a to-do's text
never leaves the Pi: a to-do reminder (a message whose habit is 'tasks') goes as the `phone_todo` line instead of its
words, and any other reminder goes with its own ready-made words (`ready_text`), never an AI's, as an AI may have seen
the to-do list. A problem with one output never stops the other, or the chat.

    feed = DesktopAlerts(hub, desktop, preferences, profiles, config.notifications, clock,
                         lens_name=config.prompts.lens.card.name, sip_title=config.prompts.notifications.sip_title,
                         phone=phone, phone_todo=config.prompts.notifications.phone_todo)
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from desktalk.config import NotificationsSettings
from desktalk.core.chat.tracker_actions import strip_action_tags
from desktalk.domain import LENS
from desktalk.ports import Clock, DesktopNotifications, Notifier, Preferences, Profiles

log = logging.getLogger(__name__)

NOTIFY = 'notify'                       # the key a message carries to ask for a pop-up
WARNING, REMINDER, SIP = 'warning', 'reminder', 'sip'
TODO_HABIT = 'tasks'                    # a message about this habit names a to-do, which never goes to the phone
READY_TEXT = 'ready_text'               # a reminder's own ready-made words (never an AI's): what the phone gets
MARKS = (NOTIFY, READY_TEXT)            # keys only this part reads: the pages never see them


class DesktopAlerts:
    """`lens_name`: Lens's name for a title (prompts → lens.card.name). `sip_title`: the title of a sip pop-up
    (prompts → notifications.sip_title). `phone`: where they also go, or None. `phone_todo`: what the phone gets
    instead of a to-do reminder's words (prompts → notifications.phone_todo)."""

    def __init__(self, inner: Notifier, desktop: DesktopNotifications, prefs: Preferences, profiles: Profiles,
                 settings: NotificationsSettings, clock: Clock, *, lens_name: str, sip_title: str,
                 phone: DesktopNotifications | None = None, phone_todo: str = ''):
        self._inner = inner
        self._desktop = desktop
        self._phone = phone
        self._phone_todo = phone_todo
        self._prefs = prefs
        self._profiles = profiles
        self._clock = clock
        self._lens_name = lens_name
        self._sip_title = sip_title
        self._on = {WARNING: settings.enabled and settings.warnings,
                    REMINDER: settings.enabled and settings.reminders,
                    SIP: settings.enabled and settings.sips}

    def send(self, message: Mapping[str, Any]) -> dict[str, Any]:
        for_pages = {key: value for key, value in message.items() if key not in MARKS}    # the pages never see these
        sent = self._inner.send(for_pages)
        try:
            shown = self._what_to_show(message)
        except Exception:       # noqa: BLE001  (a pop-up problem must never reach the chat)
            log.exception('Could not show a desktop notification')
            return sent
        if shown:
            title, text = shown
            self._show(self._desktop, title, text, 'Could not show a desktop notification')
            if self._phone is not None:
                # An AI may have seen the to-do list, so the phone gets a reminder's own words, never the AI's.
                ready = strip_action_tags(str(message.get(READY_TEXT) or ''))
                for_phone = self._phone_todo if message.get('habit') == TODO_HABIT else ready or text
                if for_phone:           # no to-do line given: a to-do reminder doesn't go to the phone at all
                    self._show(self._phone, title, for_phone, 'Could not send a notification to the phone')
        return sent

    def _what_to_show(self, message: Mapping[str, Any]) -> tuple[str, str] | None:
        """The title and words, or None when nothing should be shown now."""
        kind = str(message.get(NOTIFY) or '')
        if not self._on.get(kind) or self._prefs.quiet_now(self._clock.now()):
            return None
        text = strip_action_tags(str(message.get('text') or ''))
        if not text:
            return None
        return self._sip_title if kind == SIP else self._name(str(message.get('character') or LENS)), text

    @staticmethod
    def _show(output: DesktopNotifications, title: str, text: str, problem: str) -> None:
        try:
            output.show(title, text)
        except Exception:       # noqa: BLE001  (a problem with one output must never reach the chat or the other)
            log.exception(problem)

    def _name(self, character: str) -> str:
        if character == LENS:
            return self._lens_name
        profile = self._profiles.get(character)
        return profile.name if profile else character
