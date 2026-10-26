"""
The web servers for real: uvicorn on 127.0.0.1 and a free port, in a thread (desktalk/web/serve.py).

- The camera program (raspberry_pi/camera/) gets its settings from the server and sends it pictures in upload
  format 2, in each detector state (ported from test_pi_to_server.py).
- The server's lifespan starts the background jobs and stops them when the server stops, and open pages
  don't hold it up.
- The phone page's server speaks HTTPS with its own certificate.
"""

from __future__ import annotations

import threading
import time

import anyio
import httpx
import pytest

from desktalk.adapters.storage.files import write_atomically
from desktalk.bootstrap import build
from desktalk.domain import DetectorState, UploadInfo
from desktalk.runtime import SystemClock
from desktalk.web.app import create_app, create_phone_app
from desktalk.web.pairing import ensure_cert
from raspberry_pi.camera import cli as camera_program
from raspberry_pi.camera.clock import SystemClock as PiClock
from raspberry_pi.camera.uploader import Uploader
from tests.fakes import FakeDetector
from tests.integration.app_kit import (
    LAPTOP,
    PAIR_KEY,
    PERSON,
    DeskRig,
    changed,
    desk_config,
    desk_jpeg,
    event_log_file,
    inbox,
    pi_camera,
)
from tests.integration.web_kit import links_for, running

WAIT_SEC = 10          # longest a test waits for something to happen


def wait_until(check, seconds: float = WAIT_SEC) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.02)
    return check()


def real_desk(tmp_path, **changes):
    """The whole app with the real clock (its background jobs run in threads), a fake detector and no AI."""
    config = desk_config(tmp_path)
    for where, values in changes.items():
        config = changed(config, where, **values)
    return build(config, clock=SystemClock(), detector=FakeDetector([LAPTOP, PERSON]), data_folder=tmp_path / 'run',
                 pair_key=PAIR_KEY, set_up_logging=False)


def uploader(url: str, key: str = PAIR_KEY) -> Uploader:
    return Uploader(url, key, timeout_sec=5, retry_start_sec=1, retry_max_sec=10, error_text_chars=200,
                    clock=PiClock())


def from_pi(state: DetectorState, number: int, *found) -> UploadInfo:
    return UploadInfo(frame_id=f'pi-{number:06d}', taken_at=time.time(), detector=state, detections=found,
                      detect_ms=150.0 if found else None, model='yolo26n_ncnn' if found else '')


# ── the camera program ───────────────────────────────────────────────
def test_the_camera_programs_uploads_reach_the_server_in_each_detector_state(tmp_path):
    rig = DeskRig(tmp_path)
    app = rig.app
    with running(create_app(app, links_for(app))) as url:
        pi = uploader(url)
        pi.offer(rig.jpeg, from_pi(DetectorState.OK, 1, LAPTOP, PERSON))
        assert pi.tick() == 0.0
        app.producer.tick(rig.clock.now())
        pi.offer(desk_jpeg(200), from_pi(DetectorState.BUSY, 2))
        pi.tick()
        app.producer.tick(rig.clock.now())
        rig.detector.detections = [PERSON]
        pi.offer(rig.jpeg, from_pi(DetectorState.OFF, 3))
        pi.tick()
        app.producer.tick(rig.clock.now())
    assert (pi.sent, pi.failed) == (3, 0)
    day = app.event_log.days()[0]
    events = list(reversed(event_log_file(app).recent_detections(day, 10)))
    assert [(e['frame_id'], e['detected_on']) for e in events] == [('pi-000001', 'pi'), ('pi-000003', 'computer')]
    assert rig.detector.calls == 1                       # only the off picture was detected here
    assert pi_camera(app).status()['detector'] == 'off'


def test_the_camera_program_reports_a_wrong_key(tmp_path):
    rig = DeskRig(tmp_path)
    with running(create_app(rig.app, links_for(rig.app))) as url:
        pi = uploader(url, key='WRONG')
        pi.offer(rig.jpeg, from_pi(DetectorState.OFF, 1))
        pi.tick()
    assert (pi.sent, pi.failed) == (0, 1)
    assert pi.last_error.startswith('The computer said no (403): Pairing key does not match')


def test_the_camera_program_gets_its_settings_from_the_server_and_its_pictures_get_analysed(tmp_path):
    app = real_desk(tmp_path)
    pictures = tmp_path / 'pictures'
    pictures.mkdir()
    for i in range(3):
        (pictures / f'{i}.jpg').write_bytes(desk_jpeg(90 + 40 * i))
    with running(create_app(app, links_for(app)), lifespan='on') as url:
        status = camera_program.main(['--server', url, '--key', PAIR_KEY, '--camera', f'folder:{pictures}',
                                      '--no-detector', '--fps', '10', '--max-pictures', '10', '--min-free-mb', '0',
                                      '--photo-dir', str(tmp_path / 'pi-photos')])
        assert status == 0
        assert wait_until(lambda: app.producer.pictures_analysed >= 1)
    assert pi_camera(app).pictures_received >= 1
    latest = pi_camera(app).latest_upload()
    assert latest is not None
    upload, _ = latest
    assert (upload.version, upload.detector) == (2, DetectorState.OFF)
    assert upload.detector_reason == 'detection on the Pi is turned off (--no-detector)'
    day = app.event_log.days()[0]
    assert {i['label'] for i in event_log_file(app).presence(day)} == {'laptop', 'person'}


# ── starting and stopping ────────────────────────────────────────────
def test_the_server_runs_the_background_jobs_while_it_runs(tmp_path):
    app = real_desk(tmp_path)
    with running(create_app(app, links_for(app)), lifespan='on'):
        assert {job['state'] for job in app.supervisor.status()} == {'running'}
    assert {job['state'] for job in app.supervisor.status()} == {'stopped'}
    assert app.store.load(f'habits/{time.strftime("%Y-%m-%d")}') is not None     # saved when it stopped


def test_open_pages_do_not_hold_up_the_server_when_it_stops(tmp_path):
    app = real_desk(tmp_path, web={'stop_wait_sec': 3})
    ended: dict[str, bool] = {}
    opened = threading.Barrier(3)

    def watch(url: str, path: str) -> None:
        with httpx.Client(timeout=WAIT_SEC) as client, client.stream('GET', url + path) as reply:
            opened.wait(WAIT_SEC)
            try:
                for _ in reply.iter_raw():
                    pass
            except httpx.HTTPError:
                pass
            ended[path] = True

    with running(create_app(app, links_for(app))) as url:
        readers = [threading.Thread(target=watch, args=(url, path), daemon=True)
                   for path in ('/api/events', '/api/video_feed/0')]
        inbox(app).push(desk_jpeg())
        app.producer.tick(app.clock.now())           # a picture, so the video starts
        for reader in readers:
            reader.start()
        opened.wait(WAIT_SEC)
        started = time.monotonic()
    took = time.monotonic() - started
    for reader in readers:
        reader.join(WAIT_SEC)
    assert ended == {'/api/events': True, '/api/video_feed/0': True}
    assert took < app.config.web.stop_wait_sec


class SmallThreadPool:
    """Wraps a web app so its server runs plain `def` routes (and other blocking work) in only `size` threads
    at once, instead of the usual 40."""

    def __init__(self, app, size: int):
        self.app, self.size = app, size

    async def __call__(self, scope, receive, send):
        anyio.to_thread.current_default_thread_limiter().total_tokens = self.size
        await self.app(scope, receive, send)


def test_open_chat_pages_do_not_use_up_the_threads_the_other_routes_need(tmp_path):
    pool = 2
    pages = pool + 2
    app = real_desk(tmp_path)
    web = create_app(app, links_for(app))
    web.add_middleware(SmallThreadPool, size=pool)
    opened = threading.Semaphore(0)

    def chat_page(url: str) -> None:
        with httpx.Client(timeout=WAIT_SEC) as client, client.stream('GET', url + '/api/events') as reply:
            parts = reply.iter_raw()
            next(parts)                              # "retry: ...": the page is listening now
            opened.release()
            try:
                for _ in parts:
                    pass
            except httpx.HTTPError:
                pass

    with running(web) as url:
        readers = [threading.Thread(target=chat_page, args=(url,), daemon=True) for _ in range(pages)]
        for reader in readers:
            reader.start()
        assert all(opened.acquire(timeout=WAIT_SEC) for _ in range(pages)), 'not every chat page could open'
        started = time.monotonic()
        live = httpx.get(url + '/api/live', timeout=WAIT_SEC)
        took = time.monotonic() - started
    assert live.status_code == 200
    assert took < 1, f'/api/live waited {took:.1f} s for a free thread'


# ── the phone page's server ──────────────────────────────────────────
@pytest.fixture
def phone_desk(tmp_path):
    return DeskRig(tmp_path, config=changed(desk_config(tmp_path), 'camera', source='phone'))


def test_the_phone_page_is_served_over_https_with_its_own_certificate(tmp_path, phone_desk):
    cert = ensure_cert(tmp_path / 'certs', '127.0.0.1', phone_desk.config.camera, save=write_atomically)
    with running(create_phone_app(phone_desk.app), cert=cert) as url:
        assert url.startswith('https://')
        page = httpx.get(url + '/phone', verify=str(cert.cert_file))
    assert page.status_code == 200 and page.text.startswith('<!DOCTYPE html>')


def test_the_certificate_is_made_once_and_kept_for_the_next_start(tmp_path):
    camera = desk_config(tmp_path).camera
    cert = ensure_cert(tmp_path / 'certs', '192.168.1.20', camera, save=write_atomically)
    made = cert.cert_file.read_bytes()
    assert ensure_cert(tmp_path / 'certs', '192.168.1.20', camera, save=write_atomically) == cert
    assert cert.cert_file.read_bytes() == made
    ensure_cert(tmp_path / 'certs', '192.168.1.77', camera, save=write_atomically)      # a new Wi-Fi address:
    assert cert.cert_file.read_bytes() != made                                          # a new certificate
    assert (cert.cert_file.name, cert.key_file.name) == (camera.phone_cert_file, camera.phone_key_file)
