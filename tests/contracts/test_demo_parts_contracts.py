"""The real parts behind the Demo tab (DemoFirer, Lens, HabitReminders) and their recording stand-ins in
tests/fakes/demo.py pass the same contracts (demo_parts_contract.py)."""

from __future__ import annotations

from pathlib import Path

import pytest

from desktalk.config import load_config
from desktalk.core.demo.firer import DemoFirer
from desktalk.core.habits.reminders import HabitReminders
from desktalk.domain import AgentProfile
from tests.contracts.demo_parts_contract import CameraWarningsContract, ReminderSenderContract, ScenarioFirerContract
from tests.fakes import (
    FakeChatVoice,
    FakeDeskStatus,
    FakeHabits,
    FakeProfiles,
    FakeRuleNudges,
    RecordingCameraWarnings,
    RecordingFirer,
    RecordingHabitLog,
    RecordingNotifier,
    RecordingReminders,
    StaticRoster,
    SyncEventBus,
)
from tests.unit.test_core_workers_lens import Rig as LensRig

CONFIG = load_config(environ={}, dotenv=Path('/nonexistent'))
LOST_NAMES = {item_id: item.name for item_id, item in CONFIG.lost_items.items.items()}
NO_CUP = [AgentProfile(id='computer', name='Computer', fragile=True)]     # so cup_at_edge can't go off


class TestDemoFirer(ScenarioFirerContract):
    @pytest.fixture
    def parts(self):
        return SyncEventBus(), RecordingHabitLog(), RecordingCameraWarnings(), RecordingReminders()

    @pytest.fixture
    def firer(self, parts):
        bus, habits, camera, reminders = parts
        return DemoFirer(CONFIG.dev.scenarios, bus, habits, camera, reminders, FakeProfiles(NO_CUP),
                         prompts=CONFIG.prompts, lost_item_names=LOST_NAMES)

    @pytest.fixture
    def ready_name(self):
        return 'sip'

    @pytest.fixture
    def blocked_name(self):
        return 'cup_at_edge'

    @pytest.fixture
    def sent(self, parts):
        bus, habits, camera, reminders = parts
        return lambda: len(bus.published) + len(habits.logged) + len(camera.said) + len(reminders.said)


class TestRecordingFirer(ScenarioFirerContract):
    @pytest.fixture
    def firer(self):
        return RecordingFirer(problems={'cup_at_edge': "cup_at_edge names unicorn, which isn't an agent"})

    @pytest.fixture
    def ready_name(self):
        return 'sip'

    @pytest.fixture
    def blocked_name(self):
        return 'cup_at_edge'

    @pytest.fixture
    def sent(self, firer):
        return lambda: len(firer.fired)


class TestLens(CameraWarningsContract):
    @pytest.fixture
    def rig(self):
        return LensRig()

    @pytest.fixture
    def warnings(self, rig):
        return rig.lens

    @pytest.fixture
    def heard(self, rig):
        return rig.notifier.texts


class TestRecordingCameraWarnings(CameraWarningsContract):
    @pytest.fixture
    def warnings(self):
        return RecordingCameraWarnings()

    @pytest.fixture
    def heard(self, warnings):
        return lambda: [text for _, text, _ in warnings.said]


class TestHabitReminders(ReminderSenderContract):
    @pytest.fixture
    def notifier(self):
        return RecordingNotifier()

    @pytest.fixture
    def sender(self, notifier):
        """The desk isn't ready for reminders and the user is away: a pretend one is said anyway."""
        return HabitReminders(FakeHabits(), FakeRuleNudges(), FakeDeskStatus(ready=False, active=False),
                              StaticRoster(()), FakeProfiles([]), FakeChatVoice(None), notifier, SyncEventBus(),
                              CONFIG.reminders)

    @pytest.fixture
    def heard(self, notifier):
        return lambda: [(m['habit'], m['text']) for m in notifier.sent]


class TestRecordingReminders(ReminderSenderContract):
    @pytest.fixture
    def sender(self):
        return RecordingReminders()

    @pytest.fixture
    def heard(self, sender):
        return lambda: [(nudge.habit, nudge.text) for nudge, _ in sender.said]
