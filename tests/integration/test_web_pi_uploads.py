"""
POST /api/pi/frame: the camera program's uploads (the picture plus what the Pi's detector found), and the
replies for uploads that can't be used.
"""

from __future__ import annotations

from dataclasses import replace

from desktalk.domain import DetectorState, PiHealth, UploadInfo
from desktalk_shared.wire import encode_upload
from tests.integration.app_kit import LAPTOP, PAIR_KEY, PERSON, DeskRig, changed, desk_config, desk_jpeg
from tests.integration.web_kit import pi_upload, web_client

MB = 1024 * 1024


def info(rig: DeskRig, state: DetectorState, *found, number: int = 1, reason: str = '') -> UploadInfo:
    return UploadInfo(frame_id=f'pi-{number:06d}', taken_at=rig.clock.now(), lux=88.0, detector=state,
                      detector_reason=reason, detections=found, detect_ms=240.0 if found else None,
                      model='yolo26n_ncnn' if found else '',
                      health=PiHealth(camera='picamera2', photos_stored=12, disk_free_mb=900.0, cpu_temp_c=61.5))


def test_an_ok_upload_hands_the_pis_detections_to_the_app(tmp_path):
    rig = DeskRig(tmp_path)
    reply = pi_upload(web_client(rig.app), rig.jpeg, info(rig, DetectorState.OK, LAPTOP, PERSON))
    assert reply.status_code == 200
    assert reply.json() == {'ok': True, 'fps': 0.0, 'tip': None}     # one picture: no speed yet
    rig.app.producer.tick(rig.clock.now())
    assert rig.detector.calls == 0                                  # the Pi already found what's there
    assert set(rig.app.scene.seen()) == {'laptop', 'person'}
    status = rig.app.pi.status()
    assert (status['upload_format'], status['detector'], status['model']) == (2, 'ok', 'yolo26n_ncnn')
    assert (status['camera'], status['cpu_temp_c'], status['last_frame_id']) == ('picamera2', 61.5, 'pi-000001')


def test_a_busy_upload_only_reaches_the_live_view(tmp_path):
    rig = DeskRig(tmp_path)
    assert pi_upload(web_client(rig.app), rig.jpeg, info(rig, DetectorState.BUSY)).status_code == 200
    rig.app.producer.tick(rig.clock.now())
    assert rig.app.producer.pictures_analysed == 0 and rig.detector.calls == 0
    assert rig.app.live_pictures.plain(0) == rig.jpeg


def test_an_off_upload_is_detected_on_this_computer(tmp_path):
    rig = DeskRig(tmp_path)
    rig.detector.detections = [LAPTOP]
    upload = info(rig, DetectorState.OFF, reason='the detector container is not answering')
    assert pi_upload(web_client(rig.app), rig.jpeg, upload).status_code == 200
    rig.app.producer.tick(rig.clock.now())
    assert rig.detector.calls == 1 and set(rig.app.scene.seen()) == {'laptop'}
    assert rig.app.pi.status()['detector_reason'] == 'the detector container is not answering'


def test_an_upload_that_is_not_multipart_is_refused(tmp_path):
    rig = DeskRig(tmp_path)
    headers = {'X-Desk-Key': PAIR_KEY, 'Content-Type': 'image/jpeg'}
    reply = web_client(rig.app).post('/api/pi/frame', content=rig.jpeg, headers=headers)
    assert reply.status_code == 400 and 'multipart/form-data' in reply.json()['error']
    assert rig.app.source.pictures_received == 0


def test_an_upload_whose_boundary_is_not_plain_ascii_is_refused(tmp_path):
    rig = DeskRig(tmp_path)
    body, _ = encode_upload(rig.jpeg, info(rig, DetectorState.OFF))
    headers = {'X-Desk-Key': PAIR_KEY, 'Content-Type': 'multipart/form-data; boundary=caf\u00e9'.encode('latin-1')}
    reply = web_client(rig.app).post('/api/pi/frame', content=body, headers=headers)
    assert reply.status_code == 400 and 'boundary' in reply.json()['error']
    assert rig.app.source.pictures_received == 0


def test_an_upload_above_the_size_limit_is_refused_before_it_reaches_the_camera(tmp_path):
    config = changed(desk_config(tmp_path), 'web', pi_upload_max_mb=0.5)
    rig = DeskRig(tmp_path, config=config)
    too_big = rig.jpeg + bytes(MB // 2)
    reply = pi_upload(web_client(rig.app), too_big, info(rig, DetectorState.OFF))
    assert reply.status_code == 413
    assert '0.5 MB' in reply.json()['error']
    assert rig.app.source.pictures_received == 0


def test_an_upload_with_a_newer_format_is_refused_with_what_to_do(tmp_path):
    rig = DeskRig(tmp_path)
    reply = pi_upload(web_client(rig.app), rig.jpeg, replace(info(rig, DetectorState.OFF), version=3))
    assert reply.status_code == 400
    assert 'Update Desk Talk on this computer' in reply.json()['error']
    assert rig.app.source.pictures_received == 0


def test_an_upload_whose_picture_is_not_a_jpeg_is_refused(tmp_path):
    rig = DeskRig(tmp_path)
    reply = pi_upload(web_client(rig.app), b'not a picture', info(rig, DetectorState.OFF))
    assert reply.status_code == 400 and reply.json() == {'error': 'not a JPEG image'}


def test_the_pairing_key_is_checked_before_the_upload_is_read(tmp_path):
    rig = DeskRig(tmp_path)
    reply = pi_upload(web_client(rig.app), rig.jpeg, info(rig, DetectorState.OFF), key='not-the-key')
    assert reply.status_code == 403 and 'Pairing key does not match' in reply.json()['error']
    assert rig.app.source.pictures_received == 0


def test_uploads_are_refused_when_the_camera_is_not_the_pi(tmp_path):
    rig = DeskRig(tmp_path, config=changed(desk_config(tmp_path), 'camera', source='phone'))
    reply = pi_upload(web_client(rig.app), desk_jpeg(), info(rig, DetectorState.OFF))
    assert reply.status_code == 409
    assert reply.json() == {'error': 'The app is set to camera.source: phone, so pictures from pi go nowhere. '
                                     'Set camera.source to pi in its settings (settings.local.yaml; on the Pi, '
                                     'raspberry_pi/mine/settings.mine.yaml) and restart it.'}
