"""Real parts that already do what an interface added in Task 4 of the cleanup plan asks run its contract here.
(The real PictureTools, OpenCvPictureTools, runs PictureToolsContract in test_opencv_picture_tools.py.)"""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from desktalk.adapters.camera import PhoneFrameSource, PiFrameSource
from desktalk.core.vision.proof_photos import ProofPhotos
from desktalk.core.vision.rules import SafetyRules
from desktalk.desk_state import DeskState
from desktalk.ports import DeskRulesView, PhoneCamera, PhoneControls, PhotoTaker, PiStatus, PiUploads
from tests.contracts.desk_rules_contract import DeskRulesContract
from tests.contracts.phone_camera_contract import PhoneCameraContract
from tests.contracts.photo_taker_contract import PhotoTakerContract
from tests.contracts.pi_status_contract import PiStatusContract
from tests.fakes import FakeClock, InMemoryKeyValueStore
from tests.unit.test_camera_sources_push import camera_settings
from tests.unit.test_core_vision_kit import app_config

CONFIG = app_config()
PICTURE = cv2.imencode('.jpg', np.full((120, 160, 3), 90, np.uint8))[1].tobytes()


class TestProofPhotosPassesPhotoTakerContract(PhotoTakerContract):
    @pytest.fixture
    def taker(self):
        desk = DeskState()
        desk.frames[0] = PICTURE
        return ProofPhotos(desk, FakeClock(), CONFIG.snapshots)

    @pytest.fixture
    def taker_without_picture(self):
        return ProofPhotos(DeskState(), FakeClock(), CONFIG.snapshots)


class TestSafetyRulesPassesDeskRulesContract(DeskRulesContract):
    @pytest.fixture
    def rules(self):
        return SafetyRules(InMemoryKeyValueStore(), CONFIG.safety)


class TestPiFrameSourcePassesPiStatusContract(PiStatusContract):
    @pytest.fixture
    def pi(self):
        return PiFrameSource(FakeClock(), camera_settings())

    @pytest.fixture
    def upload(self):
        return lambda pi, info: pi.push(PICTURE, info)


class TestPhoneFrameSourcePassesPhoneCameraContract(PhoneCameraContract):
    @pytest.fixture
    def phone(self):
        return PhoneFrameSource(FakeClock(), camera_settings())


def test_the_real_parts_have_all_of_their_interface():
    assert isinstance(ProofPhotos(DeskState(), FakeClock(), CONFIG.snapshots), PhotoTaker)
    assert isinstance(SafetyRules(InMemoryKeyValueStore(), CONFIG.safety), DeskRulesView)
    pi = PiFrameSource(FakeClock(), camera_settings())
    assert isinstance(pi, PiStatus) and isinstance(pi, PiUploads)
    phone = PhoneFrameSource(FakeClock(), camera_settings())
    assert isinstance(phone, PhoneCamera) and isinstance(phone, PhoneControls)
