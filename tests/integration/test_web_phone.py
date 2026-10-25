"""The phone camera page's own server (create_phone_app): its pages, its pictures, and the phone's tilt."""

from __future__ import annotations

from typing import cast

import pytest

from desktalk.adapters.camera import PhoneFrameSource
from desktalk.web.app import create_phone_app
from desktalk.web.routers.phone import parse_gravity, parse_rotation
from tests.integration.app_kit import PAIR_KEY, DeskRig, changed, desk_config
from tests.integration.web_kit import web_client


@pytest.fixture
def rig(tmp_path):
    return DeskRig(tmp_path, config=changed(desk_config(tmp_path), 'camera', source='phone'))


def phone_camera(rig: DeskRig) -> PhoneFrameSource:
    """The real phone camera behind the app (its picture count and turn aren't in any interface)."""
    return cast(PhoneFrameSource, rig.app.source)


@pytest.fixture
def phone(rig):
    return web_client(create_phone_app(rig.app), from_ip='192.168.1.50')


@pytest.mark.parametrize('header, gravity', [
    ('0.12,-9.71,0.85', (0.12, -9.71, 0.85)),
    ('1,2,3,4', (1.0, 2.0, 3.0)),          # anything after the third number is ignored
    ('', None),
    (None, None),
    ('1,2', None),                         # not three numbers
    ('1,a,3', None),
    ('0,-60,0', None),                     # far more than gravity: not a real reading
])
def test_the_tilt_header_is_read_as_three_numbers(header, gravity):
    assert parse_gravity(header, most=50) == gravity


def test_the_highest_real_tilt_reading_comes_from_the_settings(rig):
    assert parse_gravity('0,-60,0', most=70) == (0.0, -60.0, 0.0)
    assert rig.app.config.camera.phone_tilt_max == 50       # camera.phone_tilt_max


@pytest.mark.parametrize('header, rotation', [
    ('90', 90),
    ('-90', 270),                          # a quarter turn back is three quarters forward
    ('', None),                            # the phone didn't say
    ('--90', None),                        # not a number
    ('\u00b2', None),                      # a digit to Python's isdigit(), but not a number int() reads
])
def test_the_turn_header_is_read_as_a_whole_number_of_degrees(header, rotation):
    assert parse_rotation(header) == rotation


def test_a_picture_with_a_turn_that_is_not_a_number_still_arrives(rig, phone):
    for bad in ('--90', '\u00b2'):
        headers = {'X-Desk-Key': PAIR_KEY, 'Content-Type': 'image/jpeg', 'X-Desk-Rotation': bad.encode('latin-1')}
        reply = phone.post('/api/phone/frame', content=rig.jpeg, headers=headers)
        assert reply.status_code == 200, bad
    assert phone_camera(rig).pictures_received == 2


def test_a_picture_from_the_phone_reaches_the_camera_with_its_tilt_and_turn(rig, phone):
    headers = {'X-Desk-Key': PAIR_KEY, 'Content-Type': 'image/jpeg', 'X-Desk-Rotation': '-90',
               'X-Desk-Gravity': '0.12,-9.71,0.85'}
    reply = phone.post('/api/phone/frame', content=rig.jpeg, headers=headers)
    assert reply.status_code == 200 and reply.json() == {'ok': True, 'fps': 0.0}      # no tip: nothing wrong
    source = phone_camera(rig)
    assert source.pictures_received == 1
    assert source.recent_gravity() == (0.12, -9.71, 0.85) and source.rotation == 270
    assert 'access-control-allow-origin' not in reply.headers


def test_bad_phone_uploads_are_refused(rig, phone):
    wrong_key = phone.post('/api/phone/frame', content=rig.jpeg, headers={'X-Desk-Key': 'nope'})
    assert wrong_key.status_code == 403 and 'Scan the QR code' in wrong_key.json()['error']
    for body in (b'', b'not a picture'):
        bad = phone.post('/api/phone/frame', content=body, headers={'X-Desk-Key': PAIR_KEY})
        assert (bad.status_code, bad.json()) == (400, {'error': 'not a JPEG image'})
    assert phone_camera(rig).pictures_received == 0


def test_stop_on_the_phone_disconnects_the_camera(rig, phone):
    phone.post('/api/phone/frame', content=rig.jpeg, headers={'X-Desk-Key': PAIR_KEY})
    assert rig.app.desk_status.camera_connected()
    assert phone.post('/api/phone/stop', headers={'X-Desk-Key': 'nope'}).status_code == 403
    assert phone.post('/api/phone/stop', headers={'X-Desk-Key': PAIR_KEY}).json() == {'ok': True}
    assert not rig.app.desk_status.camera_connected()


def test_only_the_pairing_key_lets_the_phone_in(phone):
    assert phone.post('/api/phone/stop', headers={'X-Desk-Key': PAIR_KEY}).status_code == 200
    wrong = phone.post('/api/phone/stop', headers={'X-Desk-Key': PAIR_KEY + 'x'})
    missing = phone.post('/api/phone/stop')
    not_plain_letters = phone.post('/api/phone/stop', headers={'X-Desk-Key': 'cl\u00e9 \u2603'.encode()})
    for reply in (wrong, missing, not_plain_letters):
        assert (reply.status_code, reply.json()) == (403, {'error': 'Pairing key does not match.'})


def test_the_phone_page_and_its_files_are_served_and_nothing_else(rig, phone):
    for path in ('/', '/phone', '/phone/'):
        assert phone.get(path).text.startswith('<!DOCTYPE html>'), path
    assert phone.get('/phone/camera.js').headers['cache-control'] == 'no-cache'
    assert phone.get('/art/desk.svg').status_code == 200
    assert phone.get('/api/phone/config').json()['fps'] == rig.config.camera.fps
    for path in ('/static/style.css', '/phone/nope.js', '/api/live', '/phone/../config/settings.yaml'):
        assert phone.get(path).status_code == 404, path


def test_the_phone_gets_its_picture_settings(phone, rig):
    camera = rig.config.camera
    assert phone.get('/api/phone/config').json() == {
        'fps': camera.fps, 'max_side': camera.phone_max_side, 'jpeg_quality': camera.phone_jpeg_quality,
        'tilt_max': camera.phone_tilt_max, 'capture_width': camera.phone_capture_width,
        'capture_height': camera.phone_capture_height}


def test_phone_uploads_when_the_camera_is_the_pi_say_which_camera_is_set(tmp_path):
    rig = DeskRig(tmp_path)              # the Pi is the camera
    phone = web_client(create_phone_app(rig.app), from_ip='192.168.1.50')
    headers = {'X-Desk-Key': PAIR_KEY, 'Content-Type': 'image/jpeg'}
    for path in ('/api/phone/frame', '/api/phone/stop'):
        reply = phone.post(path, content=rig.jpeg, headers=headers)
        assert reply.status_code == 409 and 'camera.source: pi' in reply.json()['error'], path
