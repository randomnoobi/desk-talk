"""Real parts that already do what an interface added in Task 4 of the cleanup plan asks run its contract here.
(The real PictureTools comes in Task 5 and runs PictureToolsContract next to its own tests.)"""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from desktalk.core.vision.proof_photos import ProofPhotos
from desktalk.core.vision.rules import SafetyRules
from desktalk.desk_state import DeskState
from desktalk.ports import DeskRulesView, PhotoTaker
from tests.contracts.desk_rules_contract import DeskRulesContract
from tests.contracts.photo_taker_contract import PhotoTakerContract
from tests.fakes import FakeClock, InMemoryKeyValueStore
from tests.unit.test_core_vision_kit import app_config

CONFIG = app_config()


class TestProofPhotosPassesPhotoTakerContract(PhotoTakerContract):
    @pytest.fixture
    def taker(self):
        desk = DeskState()
        desk.frames[0] = cv2.imencode('.jpg', np.full((120, 160, 3), 90, np.uint8))[1].tobytes()
        return ProofPhotos(desk, FakeClock(), CONFIG.snapshots)

    @pytest.fixture
    def taker_without_picture(self):
        return ProofPhotos(DeskState(), FakeClock(), CONFIG.snapshots)


class TestSafetyRulesPassesDeskRulesContract(DeskRulesContract):
    @pytest.fixture
    def rules(self):
        return SafetyRules(InMemoryKeyValueStore(), CONFIG.safety)


def test_the_real_parts_have_all_of_their_interface():
    assert isinstance(ProofPhotos(DeskState(), FakeClock(), CONFIG.snapshots), PhotoTaker)
    assert isinstance(SafetyRules(InMemoryKeyValueStore(), CONFIG.safety), DeskRulesView)
