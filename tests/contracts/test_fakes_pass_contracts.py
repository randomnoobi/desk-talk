"""The fakes in tests/fakes/ pass the same contracts as the real parts."""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from desktalk.domain import BBox, Detection
from tests.contracts.chat_model_contract import ChatModelContract
from tests.contracts.desk_rules_contract import DeskRulesContract
from tests.contracts.detector_contract import DetectorContract
from tests.contracts.event_bus_contract import EventBusContract
from tests.contracts.event_store_contract import EventStoreContract
from tests.contracts.frame_source_contract import FrameSourceContract
from tests.contracts.key_value_contract import KeyValueContract
from tests.contracts.notifier_contract import NotifierContract
from tests.contracts.phone_camera_contract import PhoneCameraContract
from tests.contracts.photo_taker_contract import PhotoTakerContract
from tests.contracts.pi_status_contract import PiStatusContract
from tests.contracts.picture_tools_contract import PictureToolsContract
from tests.contracts.push_source_contract import PushSourceContract
from tests.fakes import (
    FakeChatModel,
    FakeDeskRules,
    FakeDetector,
    FakeFrameSource,
    FakePhone,
    FakePhotoTaker,
    FakePi,
    FakePictureTools,
    InMemoryEventStore,
    InMemoryKeyValueStore,
    RecordingNotifier,
    SyncEventBus,
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
    ('FakeFrameSource', 'PictureInbox'), ('SyncEventBus', 'EventBus'), ('InMemoryEventStore', 'EventStore'),
    ('InMemoryKeyValueStore', 'KeyValueStore'), ('FakeChatModel', 'ChatModel'), ('RecordingNotifier', 'Notifier'),
    ('FakeChatVoice', 'ChatVoice'), ('StaticRoster', 'RosterView'), ('FakeProfiles', 'Profiles'),
    ('FakeHabits', 'HabitsView'), ('FakePreferences', 'Preferences'), ('FakeCameraHealth', 'CameraHealthView'),
    ('FakeDeskStatus', 'DeskStatus'), ('FakePictureCheck', 'PictureCheck'), ('FakePainter', 'LiveViewPainter'),
    ('FakePictureTools', 'PictureTools'), ('FakePhotoTaker', 'PhotoTaker'), ('FakeDeskRules', 'DeskRulesView'),
    ('FakeDeskRules', 'DeskLayout'), ('FakePi', 'PiStatus'), ('FakePi', 'PiUploads'), ('FakePhone', 'PhoneCamera'),
    ('FakePhone', 'PhoneControls'),
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
