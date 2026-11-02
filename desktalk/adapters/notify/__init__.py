"""Saying warnings and reminders through the DesktopNotifications interface: the Mac's own pop-ups (through osascript)
or the Pi's speaker (through Piper), as settings → notifications.output says.

    desktop = make_notifications(config.notifications, sys.platform, queue_size=...)
    feed = DesktopAlerts(hub, desktop, preferences, profiles, config.notifications, clock, ...)
"""

from desktalk.adapters.notify.mac import MacNotifications, NoNotifications, make_notifications
from desktalk.adapters.notify.queued import QueuedNotifications
from desktalk.adapters.notify.speaker import SpeakerNotifications

__all__ = ['MacNotifications', 'NoNotifications', 'QueuedNotifications', 'SpeakerNotifications', 'make_notifications']
