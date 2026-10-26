"""The computer's own pop-up notifications behind the DesktopNotifications interface (the Mac's, through osascript).

    desktop = make_notifications(config.notifications, sys.platform, queue_size=...)
    feed = DesktopAlerts(hub, desktop, preferences, profiles, config.notifications, clock, ...)
"""

from desktalk.adapters.notify.mac import MacNotifications, NoNotifications, make_notifications

__all__ = ['MacNotifications', 'NoNotifications', 'make_notifications']
