"""
POST /api/pi/frame: the camera program's uploads in format 2 (the picture plus what the Pi's detector found)
and the old camera_node.py's format 1 (the picture as the body, X-... headers), and the replies for
uploads that can't be used.
"""

from __future__ import annotations

from dataclasses import replace

from desktalk.domain import DetectorState, PiHealth, UploadInfo
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
    assert set(rig.app.desk.seen_labels) == {'laptop', 'person'}
    status = rig.app.pi_source.status()
    assert (status['upload_format'], status['detector'], status['model']) == (2, 'ok', 'yolo26n_ncnn')
    assert (status['camera'], status['cpu_temp_c'], status['last_frame_id']) == ('picamera2', 61.5, 'pi-000001')


def test_a_busy_upload_only_reaches_the_live_view(tmp_path):
    rig = DeskRig(tmp_path)
    assert pi_upload(web_client(rig.app), rig.jpeg, info(rig, DetectorState.BUSY)).status_code == 200
    rig.app.producer.tick(rig.clock.now())
    assert rig.app.producer.pictures_analysed == 0 and rig.detector.calls == 0
    with rig.app.desk.lock:
        assert rig.app.desk.raw_frames[0] == rig.jpeg


def test_an_off_upload_is_detected_on_this_computer(tmp_path):
    rig = DeskRig(tmp_path)
    rig.detector.detections = [LAPTOP]
    upload = info(rig, DetectorState.OFF, reason='the detector container is not answering')
    assert pi_upload(web_client(rig.app), rig.jpeg, upload).status_code == 200
    rig.app.producer.tick(rig.clock.now())
    assert rig.detector.calls == 1 and set(rig.app.desk.seen_labels) == {'laptop'}
    assert rig.app.pi_source.status()['detector_reason'] == 'the detector container is not answering'


def test_an_old_pi_sends_format_1_headers(tmp_path):
    rig = DeskRig(tmp_path)
    headers = {'X-Desk-Key': PAIR_KEY, 'Content-Type': 'image/jpeg', 'X-Frame-Id': 'pi-000042', 'X-Lux': '312.5',
               'X-Pi-Camera': 'usb', 'X-Pi-Photos': '', 'X-Pi-Free-MB': '1234', 'X-Taken-At': str(rig.clock.now())}
    reply = web_client(rig.app).post('/api/pi/frame', content=rig.jpeg, headers=headers)
    assert reply.status_code == 200
    upload, _ = rig.app.pi_source.latest_upload()
    assert (upload.version, upload.frame_id, upload.lux, upload.detector) == (1, 'pi-000042', 312.5, DetectorState.OFF)
    assert rig.app.pi_source.pi_info() == {'last_frame_id': 'pi-000042', 'camera': 'usb', 'disk_free_mb': 1234}


def test_an_upload_above_the_size_limit_is_refused_before_it_reaches_the_camera(tmp_path):
    config = changed(desk_config(tmp_path), 'web', pi_upload_max_mb=0.5)
    rig = DeskRig(tmp_path, config=config)
    too_big = rig.jpeg + bytes(MB // 2)
    reply = pi_upload(web_client(rig.app), too_big, info(rig, DetectorState.OFF))
    assert reply.status_code == 413
    assert '0.5 MB' in reply.json()['error']
    assert rig.app.pi_source.pictures_received == 0


def test_an_upload_with_a_newer_format_is_refused_with_what_to_do(tmp_path):
    rig = DeskRig(tmp_path)
    reply = pi_upload(web_client(rig.app), rig.jpeg, replace(info(rig, DetectorState.OFF), version=3))
    assert reply.status_code == 400
    assert 'Update Desk Talk on this computer' in reply.json()['error']
    assert rig.app.pi_source.pictures_received == 0


def test_an_upload_whose_picture_is_not_a_jpeg_is_refused(tmp_path):
    rig = DeskRig(tmp_path)
    reply = pi_upload(web_client(rig.app), b'not a picture', info(rig, DetectorState.OFF))
    assert reply.status_code == 400 and reply.json() == {'error': 'not a JPEG image'}


def test_the_pairing_key_is_checked_before_the_upload_is_read(tmp_path):
    rig = DeskRig(tmp_path)
    reply = pi_upload(web_client(rig.app), rig.jpeg, info(rig, DetectorState.OFF), key='not-the-key')
    assert reply.status_code == 403 and 'Pairing key does not match' in reply.json()['error']
    assert rig.app.pi_source.pictures_received == 0


def test_uploads_are_refused_when_the_camera_is_not_the_pi(tmp_path):
    rig = DeskRig(tmp_path, config=changed(desk_config(tmp_path), 'camera', source='phone'))
    reply = pi_upload(web_client(rig.app), desk_jpeg(), info(rig, DetectorState.OFF))
    assert reply.status_code == 409
    assert reply.json() == {'error': 'This computer is set to camera.source: phone. '
                                     'Set it to pi in config/settings.yaml.'}
