"""
DesktopAlerts: a Notifier that also pops up the computer's own notification.

It wraps the chat's Notifier (the message hub). send() hands the message to the hub first (without the `notify`
mark, which the pages don't need), so the chat never waits. Then, for a message marked `notify` = 'warning',
'reminder' or 'sip' (danger alerts and Lens's camera warnings, habit reminders, and sips the camera saw), it
shows a pop-up: the character's name as the title (a sip uses the "Sip logged" title) and the message's words,
without any action tags, as the text. Nothing pops up when notifications are off, that kind is off, or it is quiet
hours. Joins, greetings, small talk and replies are never marked.

    feed = DesktopAlerts(hub, desktop, preferences, profiles, config.notifications, clock,
                         lens_name=config.prompts.lens.card.name, sip_title=config.prompts.notifications.sip_title)
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


class DesktopAlerts:
    """`lens_name`: Lens's name for a title (prompts → lens.card.name). `sip_title`: the title of a sip pop-up
    (prompts → notifications.sip_title)."""

    def __init__(self, inner: Notifier, desktop: DesktopNotifications, prefs: Preferences, profiles: Profiles,
                 settings: NotificationsSettings, clock: Clock, *, lens_name: str, sip_title: str):
        self._inner = inner
        self._desktop = desktop
        self._prefs = prefs
        self._profiles = profiles
        self._clock = clock
        self._lens_name = lens_name
        self._sip_title = sip_title
        self._on = {WARNING: settings.enabled and settings.warnings,
                    REMINDER: settings.enabled and settings.reminders,
                    SIP: settings.enabled and settings.sips}

    def send(self, message: Mapping[str, Any]) -> dict[str, Any]:
        for_pages = {key: value for key, value in message.items() if key != NOTIFY}      # the pages never see the mark
        sent = self._inner.send(for_pages)
        try:
            self._pop_up(message)
        except Exception:       # noqa: BLE001  (a pop-up problem must never reach the chat)
            log.exception('Could not show a desktop notification')
        return sent

    def _pop_up(self, message: Mapping[str, Any]) -> None:
        kind = str(message.get(NOTIFY) or '')
        if not self._on.get(kind) or self._prefs.quiet_now(self._clock.now()):
            return
        text = strip_action_tags(str(message.get('text') or ''))
        if not text:
            return
        self._desktop.show(self._sip_title if kind == SIP else self._name(str(message.get('character') or LENS)), text)

    def _name(self, character: str) -> str:
        if character == LENS:
            return self._lens_name
        profile = self._profiles.get(character)
        return profile.name if profile else character
