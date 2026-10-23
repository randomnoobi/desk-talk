"""
raspberry_pi/camera/detector_client.py: asking the detector container (in Docker on the Pi) what's in
each picture, and giving up quickly when it can't answer, so pictures keep flowing to the computer.
"""

import json
import logging
import socket

import pytest
from test_camera_program_fakes import JPEG, MODEL, FakeClock, FakeContainer, cup

from desktalk_shared.detection import DetectorUnavailable
from desktalk_shared.detector_api import API_VERSION, ModelSettings, ServiceState
from desktalk_shared.wire import DetectorState
from raspberry_pi.camera.cameras import Picture
from raspberry_pi.camera.detector_client import DetectorClient, DetectorConfig, DetectorWorker, NoDetector

PAUSE = 5             # pause_start_sec
HEALTH_EVERY = 10     # health_check_every_sec


def make_client(url, clock, **changes):
    settings = dict(url=url, model=MODEL, wait_per_picture_sec=2.0, failures_before_pause=3,
                    pause_start_sec=PAUSE, pause_max_sec=60, health_check_every_sec=HEALTH_EVERY)
    settings.update(changes)
    return DetectorClient(DetectorConfig(**settings), clock)


@pytest.fixture
def container():
    with FakeContainer() as c:
        yield c


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def client(container, clock):
    return make_client(container.url, clock)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def fail(client, times):
    return [client.detect(JPEG) for _ in range(times)]


# ── one picture ───────────────────────────────────────────────────────
def test_detect_sends_the_jpeg_and_gives_back_what_was_found(container, client):
    container.found = (cup(1, 2, 30, 40, conf=0.9),)
    verdict = client.detect(JPEG)
    assert verdict.state is DetectorState.OK and verdict.reason == ''
    assert verdict.reply.detections == (cup(1, 2, 30, 40, conf=0.9),)
    assert verdict.reply.detect_ms == 123.0 and verdict.reply.model == 'yolo11s.pt (ncnn, 480)'
    sent = container.requests[-1]
    assert (sent.method, sent.path, sent.body) == ('POST', '/detect', JPEG)
    assert sent.headers['content-type'] == 'image/jpeg'


def test_the_containers_own_words_are_the_reason(container, client):
    container.routes[('POST', '/detect')] = lambda req: (500, {'detail': 'the model crashed'})
    verdict = client.detect(JPEG)
    assert verdict.state is DetectorState.OFF and verdict.reason == 'the model crashed'


def test_no_answer_in_time_means_off(container, clock):
    container.hold = True
    client = make_client(container.url, clock, wait_per_picture_sec=0.05)
    verdict = client.detect(JPEG)
    assert verdict.state is DetectorState.OFF
    assert 'no answer within 0.05 s' in verdict.reason


def test_container_not_running_means_off(clock):
    client = make_client(f'http://127.0.0.1:{free_port()}', clock)
    verdict = client.detect(JPEG)
    assert verdict.state is DetectorState.OFF and 'not running' in verdict.reason


def test_a_reply_it_cannot_read_means_off(container, client):
    container.routes[('POST', '/detect')] = lambda req: (200, b'<html>surprise</html>')
    verdict = client.detect(JPEG)
    assert verdict.state is DetectorState.OFF and "can't read" in verdict.reason


# ── stop asking for a while after failures in a row ───────────────────
def test_a_few_failures_in_a_row_stop_the_asking_for_a_while(container, client, clock):
    container.routes[('POST', '/detect')] = lambda req: (500, {'detail': 'the model crashed'})
    fail(client, 2)
    assert client.off_now() == ''                       # two failures: still worth asking
    fail(client, 1)
    assert client.off_now() == 'the model crashed'      # the third: stop asking
    clock.advance(PAUSE - 0.1)
    assert client.off_now() == 'the model crashed'
    clock.advance(0.1)
    assert client.off_now() == ''                       # the pause is over: try one picture


def test_the_pause_doubles_while_it_keeps_failing_and_resets_after_a_success(container, client, clock):
    crashed = lambda req: (500, {'detail': 'the model crashed'})  # noqa: E731
    working = container.routes[('POST', '/detect')]
    container.routes[('POST', '/detect')] = crashed
    fail(client, 3)
    clock.advance(PAUSE)
    fail(client, 1)                                     # the try after the pause fails too: pause twice as long
    clock.advance(2 * PAUSE - 0.1)
    assert client.off_now()
    clock.advance(0.1)
    assert client.off_now() == ''
    container.routes[('POST', '/detect')] = working
    assert client.detect(JPEG).state is DetectorState.OK
    container.routes[('POST', '/detect')] = crashed
    fail(client, 2)
    assert client.off_now() == ''                       # a success starts the count again
    fail(client, 1)
    clock.advance(PAUSE)
    assert client.off_now() == ''                       # and the pause is back to its first length


def test_the_pause_grows_no_longer_than_the_most(container, clock):
    client = make_client(container.url, clock, pause_max_sec=12)
    container.routes[('POST', '/detect')] = lambda req: (500, {'detail': 'crashed'})
    fail(client, 3)
    for pause in (5, 10, 12, 12):
        clock.advance(pause - 0.1)
        assert client.off_now()
        clock.advance(0.1)
        assert client.off_now() == ''
        fail(client, 1)


def test_while_paused_it_checks_health_now_and_then_and_comes_back_when_ready(container, clock):
    client = make_client(container.url, clock, pause_start_sec=60)
    container.routes[('POST', '/detect')] = lambda req: (500, {'detail': 'crashed'})
    fail(client, 3)
    container.state = ServiceState.LOADING
    container.message = 'loading yolo11s.pt'
    assert client.upkeep() == HEALTH_EVERY
    assert container.paths('GET') == []                 # not yet: the first check is a while after stopping
    clock.advance(HEALTH_EVERY)
    assert client.upkeep() == HEALTH_EVERY
    assert container.paths('GET') == ['/health']
    assert client.off_now() == 'loading yolo11s.pt'
    container.state = ServiceState.READY
    clock.advance(HEALTH_EVERY)
    client.upkeep()
    assert client.off_now() == ''                       # ready long before the 60 s pause is over
    fail(client, 2)
    assert client.off_now() == ''                       # ready starts the count of failures again
    fail(client, 1)
    assert client.off_now() == 'crashed'                # failures_before_pause new ones stop it again


def test_one_slow_reply_after_health_says_ready_does_not_pause_it_again(container, clock):
    client = make_client(container.url, clock, pause_max_sec=20)
    container.routes[('POST', '/detect')] = lambda req: (500, {'detail': 'crashed'})
    fail(client, 3)
    for pause in (5, 10, 20):                           # keeps failing: the pause grows to the most
        clock.advance(pause)
        fail(client, 1)
    container.state = ServiceState.READY
    clock.advance(HEALTH_EVERY)
    client.upkeep()                                     # /health: ready again
    assert client.off_now() == ''
    fail(client, 1)                                     # one slow reply right after it came back
    assert client.off_now() == ''                       # still sending pictures to the detector
    fail(client, 2)
    clock.advance(20 - 0.1)
    assert client.off_now() == 'crashed'                # it keeps flapping: the pause stays at the longest


# ── the model is still loading (503) ─────────────────────────────────
def test_no_model_yet_is_off_with_the_containers_reason_until_health_says_ready(container, client, clock):
    container.detect_status = 503
    verdict = client.detect(JPEG)
    assert verdict.state is DetectorState.OFF and verdict.reason == 'no model loaded yet'
    assert client.off_now() == 'no model loaded yet'    # no more pictures until it's ready
    container.state, container.message = ServiceState.LOADING, 'converting yolo11s.pt to ncnn'
    for _ in range(5):                                  # minutes of converting: off, never "paused"
        clock.advance(HEALTH_EVERY)
        assert client.upkeep() == HEALTH_EVERY
        assert client.off_now() == 'converting yolo11s.pt to ncnn'
    container.state = ServiceState.READY
    clock.advance(HEALTH_EVERY)
    assert client.upkeep() is None
    assert client.off_now() == ''


def test_no_model_yet_does_not_count_as_a_failure(container, client, clock):
    container.detect_status = 503
    for _ in range(3):
        client.detect(JPEG)
        clock.advance(HEALTH_EVERY)
        client.upkeep()                                 # health says ready each time
    container.routes[('POST', '/detect')] = lambda req: (500, {'detail': 'crashed'})
    fail(client, 1)
    assert client.off_now() == ''                       # the 503s weren't counted as crashes


def test_the_containers_busy_answer_means_busy_and_is_no_failure(container, client):
    """The container turns a picture away (429) while it's still detecting one the camera already gave up on."""
    container.routes[('POST', '/detect')] = lambda req: (429, {'detail': 'The detector is still working on '
                                                                         'another picture.'})
    verdicts = fail(client, 5)
    assert {v.state for v in verdicts} == {DetectorState.BUSY}
    assert client.off_now() == ''                       # not counted as failures: no pause


# ── which model (PUT /model) ──────────────────────────────────────────
def test_tells_the_container_which_model_on_start_once(container, client):
    assert client.upkeep() is None
    client.upkeep()
    assert container.models_put == [MODEL]


def test_tells_it_again_when_the_settings_change(container, client):
    client.upkeep()
    bigger = ModelSettings(model='yolo11m.pt', format='ncnn', image_size=640, confidence=0.4, half=True)
    client.want_model(bigger)
    client.upkeep()
    assert container.models_put == [MODEL, bigger]
    assert json.loads(container.requests[-1].body) == bigger.to_dict()


def test_tells_it_again_when_health_says_it_is_starting_from_scratch(container, client, clock):
    client.upkeep()
    container.detect_status = 503
    client.detect(JPEG)
    container.state = ServiceState.STARTING             # e.g. a brand-new models volume
    clock.advance(HEALTH_EVERY)
    client.upkeep()
    assert container.models_put == [MODEL, MODEL]


def test_container_down_at_start_gets_the_model_once_it_is_back(container, client, clock):
    container.away = True
    for _ in range(3):
        client.upkeep()                                 # can't reach it: counts as failures, then stops asking
    assert 'hung up' in client.off_now()
    container.away = False
    clock.advance(HEALTH_EVERY)
    client.upkeep()
    assert container.models_put == [MODEL]
    assert client.off_now() == ''


def test_settings_the_container_refuses_are_not_sent_again(container, client, caplog):
    container.routes[('PUT', '/model')] = lambda req: (400, {'detail': 'model must be a .pt file'})
    with caplog.at_level(logging.WARNING):
        client.upkeep()
        client.upkeep()
    assert container.paths('PUT') == ['/model']
    assert 'model must be a .pt file' in caplog.text


def test_a_container_from_another_version_is_off_and_says_how_to_update_it(container, client, clock):
    container.api_version = API_VERSION + 1
    client.upkeep()                                     # the reply to PUT /model says which version it speaks
    assert 'docker compose up -d --build' in client.off_now()
    clock.advance(HEALTH_EVERY)
    client.upkeep()
    assert 'docker compose up -d --build' in client.off_now()    # still off while /health says the same
    container.api_version = API_VERSION                 # rebuilt
    clock.advance(HEALTH_EVERY)
    client.upkeep()
    assert client.off_now() == ''


def test_health_from_another_version_is_a_problem_that_says_how_to_update_it(container, client):
    container.api_version = API_VERSION + 1
    with pytest.raises(DetectorUnavailable, match=r'docker compose up -d --build'):
        client.health()


# ── one picture at a time, always the newest ──────────────────────────
def picture(n: int) -> Picture:
    return Picture(jpeg=JPEG, frame_id=f'pi-{n:06d}', taken_at=1000.0 + n, lux=None)


def test_worker_takes_one_picture_and_says_busy_to_the_rest_until_it_is_done(container, client):
    delivered = []
    worker = DetectorWorker(client, lambda pic, verdict: delivered.append((pic.frame_id, verdict.state)))
    assert worker.offer(picture(1)) is None             # taken: it gets delivered when it's done
    assert worker.offer(picture(2)).state is DetectorState.BUSY
    assert worker.offer(picture(3)).state is DetectorState.BUSY
    assert worker.tick() == 0
    assert delivered == [('pi-000001', DetectorState.OK)]
    assert worker.offer(picture(4)) is None             # free again: the newest picture goes next
    worker.tick()
    assert delivered[-1] == ('pi-000004', DetectorState.OK)
    assert container.paths('POST') == ['/detect', '/detect']


def test_worker_says_busy_while_the_detector_is_working_on_a_picture(container, client):
    worker = DetectorWorker(client, lambda pic, verdict: None)
    meanwhile = []
    answer = container.routes[('POST', '/detect')]

    def slow_detect(req):
        meanwhile.append(worker.offer(picture(2)))     # the camera takes another picture while this one is detected
        return answer(req)

    container.routes[('POST', '/detect')] = slow_detect
    worker.offer(picture(1))
    worker.tick()
    assert [v.state for v in meanwhile] == [DetectorState.BUSY]
    assert container.paths('POST') == ['/detect']


def test_worker_delivers_a_failed_picture_as_off_so_the_computer_detects_it(container, client):
    container.routes[('POST', '/detect')] = lambda req: (500, {'detail': 'the model crashed'})
    delivered = []
    worker = DetectorWorker(client, lambda pic, verdict: delivered.append(verdict))
    worker.offer(picture(1))
    worker.tick()
    assert [(v.state, v.reason) for v in delivered] == [(DetectorState.OFF, 'the model crashed')]


def test_worker_says_off_with_the_reason_while_the_container_is_down(container, client):
    container.detect_status = 503
    worker = DetectorWorker(client, lambda pic, verdict: None)
    worker.offer(picture(1))
    worker.tick()
    verdict = worker.offer(picture(2))
    assert (verdict.state, verdict.reason) == (DetectorState.OFF, 'no model loaded yet')
    assert container.paths('POST') == ['/detect']


def test_worker_with_nothing_to_do_looks_after_the_container(container, client):
    worker = DetectorWorker(client, lambda pic, verdict: None)
    assert worker.tick() is None                        # nothing to do until a picture comes
    assert container.models_put == [MODEL]


def test_offering_a_picture_wakes_the_worker(client):
    worker = DetectorWorker(client, lambda pic, verdict: None)
    assert not worker.wake.is_set()
    worker.offer(picture(1))
    assert worker.wake.is_set()


def test_no_detector_sends_every_picture_as_off():
    verdict = NoDetector('detection on the Pi is turned off (--no-detector)').offer(picture(1))
    assert (verdict.state, verdict.reason) == (DetectorState.OFF, 'detection on the Pi is turned off (--no-detector)')
