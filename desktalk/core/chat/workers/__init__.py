"""
The background chat jobs. Each is a PeriodicWorker (desktalk/ports.py) that the Supervisor runs
(desktalk/runtime/): it never starts a thread or sleeps itself, and a round that fails is tried
again later instead of switching the feature off.

    IdleChat       the agents' small talk
    LostItems      "have you seen my keys?": sightings, alerts and answers (also listens to DetectionEvents)
    Lens           the camera's own character: camera problems, a hot Pi, a Pi clock that's off
    DailySummary   the end-of-day recap
"""

from desktalk.core.chat.workers.daily_summary import DailySummary, recap_facts
from desktalk.core.chat.workers.idle_chat import IdleChat
from desktalk.core.chat.workers.lens import Lens
from desktalk.core.chat.workers.lost_items import LostItems

__all__ = ['DailySummary', 'IdleChat', 'Lens', 'LostItems', 'recap_facts']
