"""
The background chat jobs. Each is a PeriodicWorker (desktalk/ports.py) that the Supervisor runs
(desktalk/runtime/): it never starts a thread or sleeps itself, and a round that fails is tried
again later instead of switching the feature off.

    IdleChat       the agents' small talk
    LostItems      "have you seen my keys?": sightings, alerts and answers (also listens to DetectionEvents)
    Lens           the camera's own character: camera problems, a hot Pi, a Pi clock that's off
    DailySummary   the end-of-day recap
    VisionHabits   posture, screen distance and a phone in hand, with a vision AI on this computer

needs.py has the few small interfaces Lens needs on top of desktalk/ports.py.
"""

from desktalk.core.chat.workers.daily_summary import DailySummary, recap_facts
from desktalk.core.chat.workers.idle_chat import IdleChat
from desktalk.core.chat.workers.lens import Lens
from desktalk.core.chat.workers.lost_items import LostItems
from desktalk.core.chat.workers.needs import DeskLayout, PhoneCamera, PiUploads
from desktalk.core.chat.workers.vision_habits import VisionHabits

__all__ = ['DailySummary', 'DeskLayout', 'IdleChat', 'Lens', 'LostItems', 'PhoneCamera', 'PiUploads', 'VisionHabits',
           'recap_facts']
