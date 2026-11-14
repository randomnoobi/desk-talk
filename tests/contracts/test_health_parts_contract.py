"""What the health checks' interfaces must do (desktalk/ports.py): DiskSpace (free space on the data folder's disk),
HealthCheck (each measure once, with a level) and PiHeartbeats (the Pi's heartbeat). The fakes and the real parts
run the same suites here."""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from desktalk.adapters.camera import PiFrameSource
from desktalk.adapters.storage.disk_space import FolderDiskSpace
from desktalk.config import HealthChecksSettings, PiSettings
from desktalk.core.health import HealthChecks
from desktalk.domain import CheckLevel, DetectorState, HealthMeasure, PiHealth, UploadInfo
from desktalk.ports import DiskSpace, HealthCheck, PiHeartbeats
from tests.fakes import (
    FakeClock,
    FakeDetectionTimes,
    FakeDiskSpace,
    FakeFrameSource,
    FakeHealthCheck,
    FakePi,
)
from tests.unit.test_camera_sources_push import camera_settings

PICTURE = cv2.imencode('.jpg', np.full((120, 160, 3), 90, np.uint8))[1].tobytes()
KEYS = {'camera', 'detections', 'pi_temperature', 'card', 'pi_heartbeat', 'card_rest', 'saving'}


# ── DiskSpace ──
class DiskSpaceContract:
    """Subclass and give a `disk` fixture (on a folder that is there) and a `missing` one (on one that isn't)."""

    def test_it_is_disk_space(self, disk):
        assert isinstance(disk, DiskSpace)

    def test_free_space_is_some_megabytes(self, disk):
        free = disk.free_mb()
        assert isinstance(free, float) and free >= 0

    def test_a_folder_that_isnt_there_cant_be_measured(self, missing):
        assert missing.free_mb() is None


class TestFolderDiskSpace(DiskSpaceContract):
    @pytest.fixture
    def disk(self, tmp_path):
        return FolderDiskSpace(tmp_path)

    @pytest.fixture
    def missing(self, tmp_path):
        return FolderDiskSpace(tmp_path / 'not-here')


class TestFakeDiskSpace(DiskSpaceContract):
    @pytest.fixture
    def disk(self):
        return FakeDiskSpace(1234.0)

    @pytest.fixture
    def missing(self):
        return FakeDiskSpace(None)


# ── HealthCheck ──
class HealthCheckContract:
    """Subclass and give a `checks` fixture."""

    def test_it_is_a_health_check(self, checks):
        assert isinstance(checks, HealthCheck)

    def test_each_measure_once_with_a_level(self, checks):
        measures = checks.measures()
        assert all(isinstance(m, HealthMeasure) and isinstance(m.level, CheckLevel) for m in measures)
        keys = [m.key for m in measures]
        assert len(keys) == len(set(keys)) and set(keys) <= KEYS


class TestHealthChecks(HealthCheckContract):
    @pytest.fixture
    def checks(self):
        clock = FakeClock()
        settings = HealthChecksSettings.model_construct(card_warning_below_mb=2000.0, card_serious_below_mb=500.0,
                                                        room_warning_below_mb=300.0, room_serious_below_mb=100.0,
                                                        card_rest_warning_below_mb=2000.0,
                                                        card_rest_serious_below_mb=500.0,
                                                        no_detections_warning_sec=300.0,
                                                        no_detections_serious_sec=900.0, pi_heartbeat_late_sec=90.0)
        return HealthChecks(FakeFrameSource('pi', clock), FakeDetectionTimes(), FakeDiskSpace(), settings,
                            PiSettings.model_construct(hot_warning_c=75.0, hot_serious_c=80.0), clock=clock,
                            pi=FakePi(clock))


class TestFakeHealthCheck(HealthCheckContract):
    @pytest.fixture
    def checks(self):
        return FakeHealthCheck([HealthMeasure(key='card', level=CheckLevel.WARNING, value=1500.0, unit='MB')])


# ── PiHeartbeats ──
class PiHeartbeatsContract:
    """Subclass and give `pi` (nothing heard yet), `clock` (the FakeClock it goes by) and `upload`, which hands `pi`
    one picture with the UploadInfo it is given: upload(pi, info)."""

    def test_it_is_pi_heartbeats(self, pi):
        assert isinstance(pi, PiHeartbeats)

    def test_nothing_before_the_first_heartbeat_or_picture(self, pi):
        assert pi.last_heartbeat_at() is None and pi.latest_health() is None

    def test_a_heartbeat_is_the_newest_health(self, pi, clock):
        clock.advance(5)
        pi.heartbeat(PiHealth(cpu_temp_c=66.0))
        assert pi.last_heartbeat_at() == clock.monotonic()
        assert pi.latest_health() == (PiHealth(cpu_temp_c=66.0), clock.monotonic())
        assert pi.pi_info()['cpu_temp_c'] == 66.0

    def test_a_picture_after_it_is_newer_but_no_heartbeat(self, pi, clock, upload):
        pi.heartbeat(PiHealth(cpu_temp_c=66.0))
        beat = clock.monotonic()
        clock.advance(3)
        upload(pi, UploadInfo(frame_id='pi-000001', taken_at=None, detector=DetectorState.OK,
                              health=PiHealth(cpu_temp_c=70.0)))
        assert pi.last_heartbeat_at() == beat
        assert pi.latest_health() == (PiHealth(cpu_temp_c=70.0), clock.monotonic())


class TestPiFrameSourceHeartbeats(PiHeartbeatsContract):
    @pytest.fixture
    def clock(self):
        return FakeClock()

    @pytest.fixture
    def pi(self, clock):
        return PiFrameSource(clock, camera_settings())

    @pytest.fixture
    def upload(self):
        return lambda pi, info: pi.push(PICTURE, info)


class TestFakePiHeartbeats(PiHeartbeatsContract):
    @pytest.fixture
    def clock(self):
        return FakeClock()

    @pytest.fixture
    def pi(self, clock):
        return FakePi(clock)

    @pytest.fixture
    def upload(self, clock):
        return lambda pi, info: pi.receive(info, clock.now())
