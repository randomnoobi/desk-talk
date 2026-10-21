"""GET /api/health: how every part is doing, in one reply (the background jobs, the event stream, the camera,
the Pi, detection and the AI)."""

from __future__ import annotations

from desktalk.domain import DetectorState, UploadInfo
from tests.integration.app_kit import LAPTOP, DeskRig, changed, desk_config
from tests.integration.web_kit import web_client


def test_health_shows_the_pi_its_detector_and_detection_here(tmp_path):
    rig = DeskRig(tmp_path)
    rig.picture(UploadInfo(frame_id='pi-000009', taken_at=rig.clock.now(), detector=DetectorState.OFF,
                           detector_reason='the detector container is not answering'))
    rig.detector.fail = 'the model file is missing'
    rig.picture()
    health = web_client(rig.app).get('/api/health').json()
    assert health['pi']['last_frame_id'] == 'pi-000009'
    assert health['pi']['detector'] == 'off'
    assert health['pi']['detector_reason'] == 'the detector container is not answering'
    assert health['camera']['source'] == 'pi' and health['camera']['connected'] is True
    assert (health['detection']['pictures_analysed'], health['detection']['problem']) == (
        1, 'the model file is missing')


def test_health_shows_the_event_stream_and_the_scheduled_jobs(tmp_path):
    rig = DeskRig(tmp_path)
    rig.watch(1, LAPTOP)
    health = web_client(rig.app).get('/api/health').json()
    log = next(c for c in health['events'] if c['name'] == 'event-log')
    assert log['handled'] == 3 and log['errors'] == 0
    assert {job['name'] for job in health['scheduler']} >= {'compact event log', 'save habit day'}


def test_health_shows_the_ai_problem(tmp_path):
    rig = DeskRig(tmp_path, ai_reply=ConnectionError('Connection refused'))      # every AI call fails
    client = web_client(rig.app)
    assert client.get('/api/health').json()['ai']['problem'] is None
    client.post('/api/chat', json={'chat': 'cup', 'message': 'hi'})
    ai = client.get('/api/health').json()['ai']
    assert ai['ready'] is True and isinstance(ai['problem'], str) and ai['problem']     # in plain words


def test_health_shows_each_background_job_once_they_run(tmp_path):
    rig = DeskRig(tmp_path)
    client = web_client(rig.app)
    assert client.get('/api/health').json()['jobs'] == []
    rig.app.start()
    try:
        jobs = client.get('/api/health').json()['jobs']
    finally:
        rig.app.stop(timeout=5)
    assert {job['name'] for job in jobs} >= {'detection', 'scheduler', 'lens', 'habit reminders'}
    assert {job['state'] for job in jobs} == {'running'}


def test_health_has_no_pi_part_when_the_camera_is_the_phone(tmp_path):
    rig = DeskRig(tmp_path, config=changed(desk_config(tmp_path), 'camera', source='phone'))
    assert web_client(rig.app).get('/api/health').json()['pi'] is None
