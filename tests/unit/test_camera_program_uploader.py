"""raspberry_pi/camera/uploader.py: sending pictures (and what the Pi's detector found) to the computer."""

import logging

import pytest
from test_camera_program_fakes import JPEG, KEY, FakeClock, FakeComputer, cup

from desktalk_shared.wire import DetectorState, PiHealth, UploadInfo
from raspberry_pi.camera.uploader import Uploader

RETRY_START = 1
RETRY_MAX = 10


@pytest.fixture
def computer():
    with FakeComputer() as c:
        yield c


@pytest.fixture
def clock():
    return FakeClock()


def make_uploader(computer, clock, key=KEY):
    return Uploader(computer.url, key, timeout_sec=2, retry_start_sec=RETRY_START, retry_max_sec=RETRY_MAX,
                    clock=clock)


@pytest.fixture
def uploader(computer, clock):
    return make_uploader(computer, clock)


def info(n, state=DetectorState.BUSY, **more):
    return UploadInfo(frame_id=f'pi-{n:06d}', taken_at=1000.0 + n, detector=state, **more)


def sent_ids(computer):
    return [u.info.frame_id for u in computer.uploads]


def test_sends_the_picture_and_its_info_in_upload_format_2(computer, uploader):
    full = info(1, DetectorState.OK, lux=120.5, detections=(cup(),), detect_ms=640.0, model='yolo11s.pt (ncnn, 480)',
                health=PiHealth(camera='picamera2', photos_stored=12, disk_free_mb=5000, cpu_temp_c=55.2,
                                throttled='0x0'))
    uploader.offer(JPEG, full)
    assert uploader.tick() == 0
    [upload] = computer.uploads
    assert (upload.jpeg, upload.info, upload.key) == (JPEG, full, KEY)
    assert uploader.sent == 1


def test_nothing_to_send_waits_for_a_picture(uploader):
    assert uploader.tick() is None


def test_offering_a_picture_wakes_the_uploader(uploader):
    uploader.offer(JPEG, info(1))
    assert uploader.wake.is_set()


def test_only_the_newest_picture_waits(computer, uploader):
    uploader.offer(JPEG, info(1))
    uploader.offer(JPEG, info(2))
    uploader.tick()
    uploader.tick()
    assert sent_ids(computer) == ['pi-000002']
    assert uploader.skipped == 1


def test_a_live_view_picture_never_pushes_out_one_with_detections(computer, uploader):
    uploader.offer(JPEG, info(1, DetectorState.OK, detections=(cup(),)))
    uploader.offer(JPEG, info(2, DetectorState.BUSY))
    uploader.tick()
    uploader.offer(JPEG, info(3, DetectorState.BUSY))
    uploader.offer(JPEG, info(4, DetectorState.OFF, detector_reason='the detector crashed'))
    uploader.tick()
    assert sent_ids(computer) == ['pi-000001', 'pi-000004']
    assert uploader.skipped == 2


def test_computer_away_waits_then_sends_only_the_newest(computer, uploader, clock):
    computer.away = True
    uploader.offer(JPEG, info(1))
    assert uploader.tick() == RETRY_START
    assert uploader.failed == 1
    uploader.offer(JPEG, info(2))
    uploader.offer(JPEG, info(3))
    assert uploader.tick() == RETRY_START                # still waiting: nothing sent
    computer.away = False
    clock.advance(RETRY_START)
    uploader.tick()
    assert sent_ids(computer) == ['pi-000003']           # no catching up on the old ones


def test_the_wait_grows_while_the_computer_stays_away_and_resets_when_it_is_back(computer, uploader, clock):
    computer.away = True
    waits = []
    for n in range(6):
        uploader.offer(JPEG, info(n))
        waits.append(uploader.tick())
        clock.advance(waits[-1])
    assert waits == [1, 2, 4, 8, 10, 10]
    computer.away = False
    uploader.offer(JPEG, info(7))
    assert uploader.tick() == 0
    computer.away = True
    uploader.offer(JPEG, info(8))
    assert uploader.tick() == RETRY_START


def test_wrong_key_is_reported_once_in_plain_words(computer, clock, caplog):
    uploader = make_uploader(computer, clock, key='WRONG')
    with caplog.at_level(logging.INFO):
        for n in range(3):
            uploader.offer(JPEG, info(n))
            clock.advance(uploader.tick())
    assert computer.uploads == [] and uploader.failed == 3
    assert '403' in uploader.last_error
    assert caplog.text.count('Pairing key does not match') == 1


def test_says_when_pictures_reach_the_computer_again(computer, uploader, clock, caplog):
    with caplog.at_level(logging.INFO):
        uploader.offer(JPEG, info(1))
        uploader.tick()
        computer.away = True
        uploader.offer(JPEG, info(2))
        clock.advance(uploader.tick())
        computer.away = False
        uploader.offer(JPEG, info(3))
        uploader.tick()
    assert caplog.text.count('pictures are reaching') == 2
    assert caplog.text.count('Cannot reach the computer') == 1
