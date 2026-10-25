"""Fakes for tests: each behaves like the real part (the main ones pass the same contract tests in
tests/contracts/) but needs no network, camera, Pi, AI key or real time.

    from tests.fakes import FakeClock, FakeDetector, SyncEventBus, FakeChatModel
"""

from tests.fakes.cameras import FakePhone, FakePi
from tests.fakes.clock import FakeClock
from tests.fakes.desk import FakeCharacterMemory, FakeDeskScene, FakeLivePictures, FakePhotoAlbum, FakeSafetyBoard
from tests.fakes.dev import (
    FakeDatabaseBrowser,
    FakeDataFiles,
    FakeSmoothingTrace,
    FakeTestRunner,
    smoothing_step,
)
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
from tests.fakes.vision import FakeDeskRules, FakePhotoTaker, FakePictureTools, plain_desk_rules

__all__ = [
    'FakeCameraHealth', 'FakeCharacterMemory', 'FakeChatModel', 'FakeChatVoice', 'FakeClock', 'FakeDatabaseBrowser',
    'FakeDataFiles', 'FakeDeskRules', 'FakeSmoothingTrace', 'FakeTestRunner', 'smoothing_step',
    'FakeDeskScene', 'FakeDeskStatus', 'FakeDetector', 'FakeFrameSource', 'FakeHabits', 'FakeLivePictures',
    'FakeObjectStates', 'FakePainter', 'FakePhone', 'FakePhotoAlbum', 'FakePhotoTaker', 'FakePi', 'FakePictureCheck',
    'FakePictureTools', 'FakePreferences', 'FakeProfiles', 'FakeRuleNudges', 'FakeSafetyBoard', 'InMemoryEventStore',
    'InMemoryKeyValueStore', 'InMemoryStateStore', 'RecordingNotifier', 'StaticRoster', 'SyncEventBus',
    'plain_desk_rules',
]
