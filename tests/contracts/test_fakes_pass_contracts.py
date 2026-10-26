"""The fakes in tests/fakes/ pass the same contracts as the real parts."""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from desktalk.domain import BBox, Detection, QueryResult, TableInfo, TestItem
from tests.contracts.character_memory_contract import CharacterMemoryContract
from tests.contracts.chat_model_contract import ChatModelContract
from tests.contracts.desk_rules_contract import DeskRulesContract
from tests.contracts.desk_scene_contract import DeskSceneContract
from tests.contracts.detector_contract import DetectorContract
from tests.contracts.dev_contracts import (
    DatabaseBrowserContract,
    DataFilesContract,
    RunnerContract,
    SmoothingTraceContract,
)
from tests.contracts.event_bus_contract import EventBusContract
from tests.contracts.event_store_contract import EventStoreContract
from tests.contracts.frame_source_contract import FrameSourceContract
from tests.contracts.key_value_contract import KeyValueContract
from tests.contracts.live_pictures_contract import LivePicturesContract
from tests.contracts.notifier_contract import NotifierContract
from tests.contracts.object_states_contract import ObjectStatesContract
from tests.contracts.phone_camera_contract import PhoneCameraContract
from tests.contracts.photo_album_contract import PhotoAlbumContract
from tests.contracts.photo_taker_contract import PhotoTakerContract
from tests.contracts.pi_status_contract import PiStatusContract
from tests.contracts.picture_tools_contract import PictureToolsContract
from tests.contracts.push_source_contract import PushSourceContract
from tests.contracts.rule_nudges_contract import RuleNudgesContract, a_nudge
from tests.contracts.safety_board_contract import SafetyBoardContract
from tests.contracts.state_store_contract import StateStoreContract
from tests.fakes import (
    FakeCharacterMemory,
    FakeChatModel,
    FakeDatabaseBrowser,
    FakeDataFiles,
    FakeDeskRules,
    FakeDeskScene,
    FakeDetector,
    FakeFrameSource,
    FakeLivePictures,
    FakeObjectStates,
    FakePhone,
    FakePhotoAlbum,
    FakePhotoTaker,
    FakePi,
    FakePictureTools,
    FakeRuleNudges,
    FakeSafetyBoard,
    FakeSmoothingTrace,
    FakeTestRunner,
    InMemoryEventStore,
    InMemoryKeyValueStore,
    InMemoryStateStore,
    RecordingNotifier,
    SyncEventBus,
    smoothing_step,
)


class TestFakeDetector(DetectorContract):
    @pytest.fixture
    def detector(self):
        return FakeDetector([Detection('cup', 0.9, BBox(1, 1, 20, 20))])

    @pytest.fixture
    def broken_detector(self):
        return FakeDetector(fail='the detector container is not answering')

    @pytest.fixture
    def picture(self):
        return np.zeros((48, 64, 3), np.uint8)


class TestFakeFrameSource(FrameSourceContract, PushSourceContract):
    @pytest.fixture
    def source(self):
        return FakeFrameSource()

    @pytest.fixture
    def send(self):
        return lambda source, picture: source.add(picture)

    @pytest.fixture
    def jpeg_of(self):
        return lambda picture: cv2.imencode('.jpg', picture)[1].tobytes()


class TestSyncEventBus(EventBusContract):
    @pytest.fixture
    def bus(self):
        return SyncEventBus()


class TestInMemoryEventStore(EventStoreContract):
    @pytest.fixture
    def store(self):
        return InMemoryEventStore()


class TestInMemoryStateStore(StateStoreContract):
    @pytest.fixture
    def store(self):
        return InMemoryStateStore()


class TestInMemoryKeyValueStore(KeyValueContract):
    @pytest.fixture
    def kv(self):
        return InMemoryKeyValueStore()


class TestFakeChatModel(ChatModelContract):
    @pytest.fixture
    def model(self):
        return FakeChatModel(reply='Hello there!')

    @pytest.fixture
    def broken_model(self):
        return FakeChatModel(fail='Gemini hit its free usage limit.')


@pytest.mark.parametrize('fake, port', [
    ('FakeClock', 'Clock'), ('FakeDetector', 'Detector'), ('FakeFrameSource', 'FrameSource'),
    ('FakeObjectStates', 'ObjectStatesView'), ('FakeRuleNudges', 'RuleNudges'),
    ('FakeFrameSource', 'PictureInbox'), ('SyncEventBus', 'EventBus'), ('InMemoryEventStore', 'EventStore'),
    ('InMemoryKeyValueStore', 'KeyValueStore'), ('InMemoryStateStore', 'ObjectStateStore'),
    ('FakeChatModel', 'ChatModel'), ('RecordingNotifier', 'Notifier'),
    ('FakeChatVoice', 'ChatVoice'), ('StaticRoster', 'RosterView'), ('FakeProfiles', 'Profiles'),
    ('FakeHabits', 'HabitsView'), ('FakeHabits', 'HabitsLifecycle'), ('FakePreferences', 'Preferences'),
    ('FakeCameraHealth', 'CameraHealthView'),
    ('FakeDeskStatus', 'DeskStatus'), ('FakePictureCheck', 'PictureCheck'), ('FakePainter', 'LiveViewPainter'),
    ('FakePictureTools', 'PictureTools'), ('FakePhotoTaker', 'PhotoTaker'), ('FakeDeskRules', 'DeskRulesView'),
    ('FakeDeskRules', 'DeskLayout'), ('FakePi', 'PiStatus'), ('FakePi', 'PiUploads'), ('FakePhone', 'PhoneCamera'),
    ('FakePhone', 'PhoneControls'), ('FakeLivePictures', 'LivePictures'), ('FakeDeskScene', 'DeskScene'),
    ('FakeSafetyBoard', 'SafetyBoard'), ('FakeCharacterMemory', 'CharacterMemory'), ('FakePhotoAlbum', 'PhotoAlbum'),
    ('FakeTestRunner', 'TestRunner'), ('FakeDatabaseBrowser', 'DatabaseBrowser'), ('FakeDataFiles', 'DataFiles'),
    ('FakeSmoothingTrace', 'SmoothingTraceView'), ('FakeDesktopNotifications', 'DesktopNotifications'),
    ('FakeChatJoins', 'ChatJoins'), ('FakeTracePhotos', 'TracePhotos'), ('FakeTodoList', 'TodoList'),
])
def test_every_fake_has_all_of_its_interface(fake, port):
    import desktalk.ports as ports
    import tests.fakes as fakes
    assert isinstance(getattr(fakes, fake)(), getattr(ports, port))


def test_fake_profiles_find_agents_by_word():
    from desktalk.domain import AgentProfile
    from tests.fakes import FakeProfiles
    cup = AgentProfile(id='cup', name='Cup', labels=['cup', 'mug'])
    profiles = FakeProfiles([cup], ignored=['person'])
    assert profiles.for_label('mug') is cup
    assert profiles.for_label('person') is None
    assert profiles.mention_words() == {'cup': 'cup', 'mug': 'cup'}


class TestRecordingNotifier(NotifierContract):
    @pytest.fixture
    def notifier(self, clock):
        return RecordingNotifier(clock)


class TestFakePassesPictureToolsContract(PictureToolsContract):
    @pytest.fixture
    def tools(self):
        return FakePictureTools()


class TestFakePassesPhotoTakerContract(PhotoTakerContract):
    @pytest.fixture
    def taker(self):
        return FakePhotoTaker()

    @pytest.fixture
    def taker_without_picture(self):
        return FakePhotoTaker(has_picture=False)


class TestFakePassesDeskRulesContract(DeskRulesContract):
    @pytest.fixture
    def rules(self):
        return FakeDeskRules()


def test_fake_photo_taker_remembers_what_it_was_asked():
    taker = FakePhotoTaker()
    taker.save('Spill risk', [(1.0, 2.0, 3.0, 4.0)])
    assert taker.saved == [('Spill risk', ((1.0, 2.0, 3.0, 4.0),))]


def test_fake_picture_tools_need_no_opencv():
    import tests.fakes.vision as module
    assert 'cv2' not in module.__dict__


class TestFakePassesPiStatusContract(PiStatusContract):
    @pytest.fixture
    def pi(self):
        return FakePi()

    @pytest.fixture
    def upload(self):
        return lambda pi, info: pi.receive(info, arrived_at=1_760_000_000.0)


class TestFakePassesPhoneCameraContract(PhoneCameraContract):
    @pytest.fixture
    def phone(self):
        return FakePhone()


class TestFakePassesLivePicturesContract(LivePicturesContract):
    @pytest.fixture
    def pictures(self):
        return FakeLivePictures()


class TestFakePassesDeskSceneContract(DeskSceneContract):
    @pytest.fixture
    def scene(self):
        return FakeDeskScene()


class TestFakePassesSafetyBoardContract(SafetyBoardContract):
    @pytest.fixture
    def board(self):
        return FakeSafetyBoard()


class TestFakePassesCharacterMemoryContract(CharacterMemoryContract):
    @pytest.fixture
    def memory(self):
        return FakeCharacterMemory()


class TestFakePassesPhotoAlbumContract(PhotoAlbumContract):
    @pytest.fixture
    def album(self):
        return FakePhotoAlbum(keep=3)


class TestFakeObjectStates(ObjectStatesContract):
    @pytest.fixture
    def states(self):
        return FakeObjectStates()

    @pytest.fixture
    def add_change(self):
        return lambda states, ts, label, kind: states.change(ts, label, kind)

    @pytest.fixture
    def put_object(self):
        return lambda states, label, state, since: states.set(label, state, since)


class TestFakeRuleNudges(RuleNudgesContract):
    @pytest.fixture
    def rule_nudges(self):
        return FakeRuleNudges([a_nudge('rule.hydration')])


# ── the developer page's tools ──
class TestFakeTestRunner(RunnerContract):
    @pytest.fixture
    def runner(self):
        return FakeTestRunner([TestItem('tests/unit/test_a.py::test_x', 'tests/unit/test_a.py', ()),
                               TestItem('tests/unit/test_a.py::test_y', 'tests/unit/test_a.py', ('slow',))],
                              checks={'Fast tests': 0, 'Lint': 1, 'Types': 0})


EVENTS = TableInfo('events', ('ts', 'object'), 2, 'CREATE TABLE events (ts REAL, object TEXT)')


class TestFakeDatabaseBrowser(DatabaseBrowserContract):
    @pytest.fixture
    def browser(self):
        rows = QueryResult(('ts', 'object'), ((1.0, 'cup'), (2.0, 'pen/pencil')), False, 0.1)
        return FakeDatabaseBrowser({'object_states': [EVENTS]}, {'SELECT * FROM events': rows})


class TestFakeDataFiles(DataFilesContract):
    @pytest.fixture
    def data_files(self):
        return FakeDataFiles({'logs/notes.txt': 'hello desk', 'events/2026-10-24/events.jsonl': '{}'})


class TestFakeSmoothingTrace(SmoothingTraceContract):
    @pytest.fixture
    def view(self):
        return FakeSmoothingTrace({'cup': [smoothing_step(1.0), smoothing_step(2.0, conf=None)],
                                   'pen/pencil': [smoothing_step(1.0)]})


def test_the_fake_runner_fails_a_run_with_a_failed_test():
    from desktalk.domain import TestOutcome
    item = TestItem('tests/unit/test_a.py::test_x', 'tests/unit/test_a.py', ())
    runner = FakeTestRunner([item], {item.id: TestOutcome(item.id, 'failed', 0.1, 'assert 1 == 2')})
    runner.collect()
    status = runner.status(runner.start_tests([item.file]), 0)
    assert (status.state, status.outcomes[0].message) == ('failed', 'assert 1 == 2')
    assert '1 failed' in status.output
    assert runner.started == [('tests', [item.file])]

