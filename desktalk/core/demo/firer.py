"""
Sends each of the Demo tab's pretend warnings (settings → dev.scenarios.items) the way the real one goes out, so
the chat, the Mac pop-up and the event log show exactly what a user would see:

    edge_warning    the proximity Alert the desk monitor publishes for a thing near the desk edge, said by that
                    thing's character (the chat's alert voice puts it into words, with the AI when there is one)
    danger_zone     the proximity Alert for a thing in another's danger zone, said by the fragile one (like the
                    desk monitor does), else by the one that's too close
    drink_reminder  a hydration nudge, said by the habit reminders (the agent for water, or Lens)
    camera_covered  Lens's "covered" camera warning, in the words the camera check uses
    sip             a sip the camera saw, logged in the habit tracker (it counts in today's water)
    lost_object     the lost_object Alert the lost-items job publishes, in its plain words

The alerts carry `demo: true`, so the event log tells them from real ones. A scenario that names an agent or a
lost item the settings don't have (someone took the keys out of lost_items.items) never stops the app: problem()
says why, and the Demo tab refuses to start it.

    firer = DemoFirer(config.dev.scenarios, bus, habits, lens, reminders, profiles, prompts=config.prompts,
                      lost_item_names={id: item.name for id, item in config.lost_items.items.items()})
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from desktalk.config import DemoScenario, DemoSettings
from desktalk.config.prompts import Prompts
from desktalk.domain import LENS, Alert, Nudge
from desktalk.ports import CameraWarnings, EventBus, HabitLog, Profiles, ReminderSender

PRETEND = {'demo': True}                 # in each alert's extra, for the event log


def _number(value: float | None) -> str:
    """50.0 → '50' (a minute count in a sentence)."""
    return f'{value or 0:g}'


AGENTS_FILE, LOST_ITEMS = 'config/agents.yaml', 'lost_items.items'     # where the names it uses must be


class DemoFirer:
    """The ScenarioFirer (desktalk/ports.py). `prompts.dev` has its words for a scenario that can't go off."""

    def __init__(self, settings: DemoSettings, bus: EventBus, habit_log: HabitLog, camera: CameraWarnings,
                 reminders: ReminderSender, profiles: Profiles, *, prompts: Prompts,
                 lost_item_names: Mapping[str, str]):
        self._bus = bus
        self._habit_log = habit_log
        self._camera = camera
        self._reminders = reminders
        self._profiles = profiles
        self._words = prompts
        self._lost_names = dict(lost_item_names)
        self._items = {item.name: item for item in settings.items}
        self._send: dict[str, Callable[[DemoScenario, float], None]] = {
            'edge_warning': self._edge_warning, 'danger_zone': self._danger_zone,
            'drink_reminder': self._drink_reminder, 'camera_covered': self._camera_covered, 'sip': self._sip,
            'lost_object': self._lost_object}

    def problem(self, name: str) -> str | None:
        """Raises KeyError for a name that isn't in the settings."""
        item = self._items[name]
        if item.fires in ('edge_warning', 'danger_zone'):
            for agent in filter(None, (item.object, item.near)):
                if self._profiles.get(agent) is None:
                    return self._words.dev.cannot_fire.format(name=name, thing=agent, where=AGENTS_FILE)
        if item.fires == 'lost_object' and item.object not in self._lost_names:
            return self._words.dev.cannot_fire.format(name=name, thing=item.object, where=LOST_ITEMS)
        return None

    def fire(self, name: str, now: float) -> None:
        """Raises KeyError for a name that isn't in the settings, and ValueError for one with a problem()."""
        problem = self.problem(name)
        if problem is not None:
            raise ValueError(problem)
        item = self._items[name]
        self._send[item.fires](item, now)

    def _edge_warning(self, item: DemoScenario, now: float) -> None:
        reason = self._words.desk_alerts.edge_reason.format(edge=item.edge, distance=_number(item.distance_px))
        self._bus.publish(Alert(ts=now, kind='proximity', agent=item.object,
                                extra={'severity': 'medium', 'rule_label': reason, **PRETEND}))

    def _danger_zone(self, item: DemoScenario, now: float) -> None:
        target = self._profiles.get(item.near)
        speaker, other = (item.near, item.object) if target and target.fragile else (item.object, item.near)
        self._bus.publish(Alert(ts=now, kind='proximity', agent=speaker,
                                extra={'near': other, 'severity': 'high', 'rule_label': self._words.dev.scenarios.zone,
                                       **PRETEND}))

    def _drink_reminder(self, item: DemoScenario, now: float) -> None:
        words, minutes = self._words.dev.scenarios, _number(item.minutes)
        nudge = Nudge('hydration', f'demo.{item.name}', evidence=words.drink_evidence.format(minutes=minutes),
                      text=words.drink_text.format(minutes=minutes), repeat_after_sec=0)
        self._reminders.say(nudge, now)

    def _camera_covered(self, item: DemoScenario, now: float) -> None:
        self._camera.say_problem('covered', self._words.camera.problems.covered, now)

    def _sip(self, item: DemoScenario, now: float) -> None:
        self._habit_log.log('hydration', 'sip', how='camera')

    def _lost_object(self, item: DemoScenario, now: float) -> None:
        text = self._words.lost_items.alert_plain.format(name=self._lost_names[item.object],
                                                         minutes=_number(item.minutes), hint='')
        self._bus.publish(Alert(ts=now, kind='scenario', scenario='lost_object', feature=item.object, agent=LENS,
                                text=text, extra=PRETEND))
