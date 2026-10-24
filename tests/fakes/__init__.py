"""Fakes for tests: each behaves like the real part (the main ones pass the same contract tests in
tests/contracts/) but needs no network, camera, Pi, AI key or real time.

    from tests.fakes import FakeClock, FakeDetector, SyncEventBus, FakeChatModel
"""

from tests.fakes.cameras import FakePhone, FakePi
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
    FakePainter,
    FakePictureCheck,
    FakePreferences,
    FakeProfiles,
    StaticRoster,
)
from tests.fakes.vision import FakeDeskRules, FakePhotoTaker, FakePictureTools, plain_desk_rules

__all__ = [
    'FakeCameraHealth', 'FakeChatModel', 'FakeChatVoice', 'FakeClock', 'FakeDeskRules', 'FakeDeskStatus',
    'FakeDetector', 'FakeFrameSource', 'FakeHabits', 'FakePainter', 'FakePhone', 'FakePhotoTaker', 'FakePi',
    'FakePictureCheck', 'FakePictureTools', 'FakePreferences', 'FakeProfiles', 'InMemoryEventStore',
    'InMemoryKeyValueStore', 'RecordingNotifier', 'StaticRoster', 'SyncEventBus', 'plain_desk_rules',
]
