"""Fakes for tests: each behaves like the real part (the main ones pass the same contract tests in
tests/contracts/) but needs no network, camera, Pi, AI key or real time.

    from tests.fakes import FakeClock, FakeDetector, SyncEventBus, FakeChatModel
"""

from tests.fakes.clock import FakeClock
from tests.fakes.parts import (
    FakeChatModel,
    FakeDetector,
    FakeFrameSource,
    InMemoryEventStore,
    InMemoryKeyValueStore,
    InMemoryStateStore,
    RecordingNotifier,
    SyncEventBus,
)
from tests.fakes.views import (
    FakeCameraHealth,
    FakeChatVoice,
    FakeDeskStatus,
    FakeHabits,
    FakeObjectStates,
    FakePainter,
    FakePictureCheck,
    FakePreferences,
    FakeProfiles,
    FakeRuleNudges,
    StaticRoster,
)

__all__ = [
    'FakeCameraHealth', 'FakeChatModel', 'FakeChatVoice', 'FakeClock', 'FakeDeskStatus', 'FakeDetector',
    'FakeFrameSource', 'FakeHabits', 'FakeObjectStates', 'FakePainter', 'FakePictureCheck', 'FakePreferences',
    'FakeProfiles', 'FakeRuleNudges', 'InMemoryEventStore', 'InMemoryKeyValueStore', 'InMemoryStateStore',
    'RecordingNotifier',
    'StaticRoster', 'SyncEventBus',
]
