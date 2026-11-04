"""What the web servers and the app's own detector say comes from config/prompts.yaml (server: and detection:), so
it can be changed there with no code change."""

from __future__ import annotations

import numpy as np
import pytest

from desktalk.adapters.detector import NoDetector
from desktalk.domain import DetectorState, DetectorUnavailable, UploadInfo
from desktalk.web.app import create_phone_app
from tests.integration.app_kit import DeskRig, changed, desk_config
from tests.integration.web_kit import pi_upload, web_client

NEW = {'something_went_wrong': 'Oops (in prompts.yaml).', 'pi_key_problem': 'Wrong Pi key (in prompts.yaml).',
       'phone_key_problem': 'Wrong phone key (in prompts.yaml).',
       'phone_stop_key_problem': 'Wrong key to stop (in prompts.yaml).',
       'other_camera': 'Set to {source}, not {kind} (in prompts.yaml).'}


def rig_with(tmp_path, **config_changes) -> DeskRig:
    config = changed(desk_config(tmp_path), 'prompts.server', **NEW)
    for where, values in config_changes.items():
        config = changed(config, where, **values)
    return DeskRig(tmp_path, config=config)


def off(rig: DeskRig) -> UploadInfo:
    return UploadInfo(frame_id='pi-000001', taken_at=rig.clock.now(), detector=DetectorState.OFF)


def test_a_crash_says_the_words_in_prompts(tmp_path, monkeypatch):
    rig = rig_with(tmp_path)

    def broken_disk() -> list[str]:
        raise OSError('the disk is gone')

    monkeypatch.setattr(rig.app.event_log, 'days', broken_disk)
    assert web_client(rig.app).get('/api/log/days').json() == {'error': NEW['something_went_wrong']}


def test_a_wrong_pi_key_says_the_words_in_prompts(tmp_path):
    rig = rig_with(tmp_path)
    reply = pi_upload(web_client(rig.app), rig.jpeg, off(rig), key='not-the-key')
    assert (reply.status_code, reply.json()) == (403, {'error': NEW['pi_key_problem']})


def test_a_picture_from_the_other_camera_says_the_words_in_prompts(tmp_path):
    rig = rig_with(tmp_path, camera={'source': 'phone'})
    reply = pi_upload(web_client(rig.app), rig.jpeg, off(rig))
    assert (reply.status_code, reply.json()) == (409, {'error': 'Set to phone, not pi (in prompts.yaml).'})


@pytest.mark.parametrize('path, words', [('/api/phone/frame', 'phone_key_problem'),
                                         ('/api/phone/stop', 'phone_stop_key_problem')])
def test_a_wrong_phone_key_says_the_words_in_prompts(tmp_path, path, words):
    rig = rig_with(tmp_path, camera={'source': 'phone'})
    reply = web_client(create_phone_app(rig.app)).post(path, headers={'X-Desk-Key': 'not-the-key'})
    assert (reply.status_code, reply.json()) == (403, {'error': NEW[words]})


def test_no_detector_says_the_words_in_prompts(tmp_path):
    words = desk_config(tmp_path).prompts.detection.model_copy(update={'none_label': 'none (prompts.yaml)',
                                                                        'none_reason': 'Off (prompts.yaml).'})
    detector = NoDetector(words)
    assert detector.label == 'none (prompts.yaml)'
    with pytest.raises(DetectorUnavailable, match=r'Off \(prompts.yaml\)'):
        detector.detect(np.zeros((8, 8, 3), np.uint8))
