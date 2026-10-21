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
    RecordingNotifier,
    SyncEventBus,
)
from tests.fakes.views import (
    FakeCameraHealth,
    FakeChatVoice,
    FakeDeskStatus,
    FakeHabits,
    FakePreferences,
    FakeProfiles,
    StaticRoster,
)

__all__ = [
    'FakeCameraHealth', 'FakeChatModel', 'FakeChatVoice', 'FakeClock', 'FakeDeskStatus', 'FakeDetector',
    'FakeFrameSource', 'FakeHabits', 'FakePreferences', 'FakeProfiles', 'InMemoryEventStore',
    'InMemoryKeyValueStore', 'RecordingNotifier', 'StaticRoster', 'SyncEventBus',
]
