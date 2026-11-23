"""The upload format between the Pi and this computer (desktalk_shared.wire)."""

from __future__ import annotations

import json

import pytest

from desktalk_shared.detection import BBox, Detection
from desktalk_shared.wire import (
    DETECTOR_TURNED_OFF,
    VERSION,
    DetectorState,
    PiHealth,
    UploadInfo,
    WireError,
    decode_upload,
    encode_upload,
)

JPEG = b'\xff\xd8\xff\xe0' + bytes(range(256)) * 4 + b'\r\n--not-a-boundary\r\n' + b'\xff\xd9'


def make_info(**kw):
    base = dict(frame_id='pi-000123', taken_at=1_760_000_000.25, lux=240.0,
                detector=DetectorState.OK, detections=(Detection('cup', 0.8, BBox(10, 20, 50, 100)),),
                detect_ms=412.5, model='yolo11s.ncnn',
                health=PiHealth(camera='picamera2', photos_stored=12, disk_free_mb=2048, cpu_temp_c=61.5,
                                throttled='0x0', last_cleanup_ts=1_759_999_000.0))
    base.update(kw)
    return UploadInfo(**base)


def test_upload_round_trips_picture_and_info():
    info = make_info()
    body, content_type = encode_upload(JPEG, info)
    assert content_type.startswith('multipart/form-data; boundary=')
    jpeg, back = decode_upload(body, content_type)
    assert jpeg == JPEG
    assert back == info
    assert back.version == VERSION


def test_busy_and_off_pictures_carry_no_detections():
    for state in (DetectorState.BUSY, DetectorState.OFF):
        info = make_info(detector=state, detections=(), detect_ms=None, detector_reason='container not answering')
        assert decode_upload(*encode_upload(JPEG, info))[1] == info
    with pytest.raises(WireError, match='only'):
        make_info(detector=DetectorState.BUSY)


def test_newer_format_says_update_this_computer():
    body, ct = encode_upload(JPEG, make_info())
    newer = body.replace(b'"version": 2', b'"version": 3')
    with pytest.raises(WireError, match='this computer'):
        decode_upload(newer, ct)


def test_older_format_says_update_the_pi():
    data = make_info().to_dict()
    data['version'] = 1
    with pytest.raises(WireError, match='Pi'):
        UploadInfo.from_dict(data)


def test_missing_parts_and_garbage_give_clear_errors():
    info = make_info()
    body, ct = encode_upload(JPEG, info)
    with pytest.raises(WireError, match='boundary'):
        decode_upload(body, 'multipart/form-data')
    with pytest.raises(WireError, match='picture'):
        decode_upload(body.replace(b'name="picture"', b'name="other"'), ct)
    with pytest.raises(WireError, match='info'):
        decode_upload(body.replace(b'name="info"', b'name="other"'), ct)
    with pytest.raises(WireError):
        decode_upload(b'garbage', ct)
    with pytest.raises(WireError, match='JSON'):
        UploadInfo.from_json(b'{not json')


def test_a_boundary_that_is_not_plain_ascii_is_refused_with_a_clear_error():
    body, _ = encode_upload(JPEG, make_info())
    with pytest.raises(WireError, match='boundary'):
        decode_upload(body, 'multipart/form-data; boundary=caf\u00e9')


def test_bad_detection_in_info_is_rejected_with_its_position():
    data = make_info().to_dict()
    data['detections'][0]['conf'] = 7
    with pytest.raises(WireError, match=r'detections\[0\]'):
        UploadInfo.from_dict(data)


def test_info_json_is_plain_and_readable():
    data = json.loads(make_info().to_json())
    assert data['version'] == VERSION
    assert data['detector'] == 'ok'
    assert data['detections'][0] == {'label': 'cup', 'conf': 0.8, 'bbox': [10.0, 20.0, 50.0, 100.0],
                                      'center': [30.0, 60.0]}
    assert data['health']['cpu_temp_c'] == 61.5


def test_the_reasons_for_detection_off_on_purpose_keep_their_words():
    """Pis already running send these exact words, so changing them would make Lens warn about them again."""
    assert DETECTOR_TURNED_OFF == 'detection on the Pi is turned off (--no-detector)'


