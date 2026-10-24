"""raspberry_pi/camera/self_test.py: --self-test checks the camera, the container, the temperature and the computer."""

import logging

import pytest
import yaml
from test_camera_program_fakes import KEY, FakeCamera, FakeClock, FakeComputer, FakeContainer, pi_settings

from desktalk_shared.detector_api import ServiceState
from raspberry_pi.camera import self_test
from raspberry_pi.camera.cameras import CameraError
from raspberry_pi.camera.cli import main
from raspberry_pi.camera.detector_client import DetectorClient
from raspberry_pi.camera.health import HealthReader
from raspberry_pi.camera.settings import from_layers


def fake_pi(tmp_path, temp_c=50.0, throttled='0'):
    root = tmp_path / 'pi'
    zone = root / 'sys/class/thermal/thermal_zone0'
    zone.mkdir(parents=True)
    (zone / 'type').write_text('cpu-thermal\n')
    if temp_c is not None:
        (zone / 'temp').write_text(f'{int(temp_c * 1000)}\n')
    firmware = root / 'sys/devices/platform/soc/soc:firmware'
    firmware.mkdir(parents=True)
    (firmware / 'get_throttled').write_text(throttled + '\n')
    return root


@pytest.fixture
def container():
    with FakeContainer() as c:
        yield c


@pytest.fixture
def computer():
    with FakeComputer() as c:
        yield c


def client_for(container):
    return DetectorClient(from_layers(pi_settings(detector={'url': container.url})).detector, FakeClock())


# ── camera ────────────────────────────────────────────────────────────
def test_camera_that_takes_a_picture_is_fine():
    camera = FakeCamera(pictures=[b'x' * 4096], lux=120.0)
    check = self_test.check_camera(lambda: camera)
    assert check.ok and 'took a picture (4 KB, light 120 lux)' in check.message
    assert camera.closed


def test_camera_that_cannot_open_is_a_problem():
    def broken():
        raise CameraError('the Pi camera module needs picamera2')

    check = self_test.check_camera(broken)
    assert not check.ok and 'needs picamera2' in check.message


def test_camera_that_gives_no_picture_is_a_problem():
    camera = FakeCamera()
    camera.broken = CameraError('no picture from the camera')
    check = self_test.check_camera(lambda: camera)
    assert not check.ok and 'no picture' in check.message and camera.closed


# ── detector container ────────────────────────────────────────────────
def test_ready_container_is_fine(container):
    check = self_test.check_detector(client_for(container))
    assert check.ok and 'yolo11s.pt' in check.message


@pytest.mark.parametrize('state', [ServiceState.STARTING, ServiceState.LOADING])
def test_starting_or_loading_container_is_fine_with_a_note(container, state):
    container.state, container.message = state, 'converting yolo11s.pt to ncnn'
    check = self_test.check_detector(client_for(container))
    assert check.ok and state.value in check.message


def test_container_with_an_error_is_a_problem(container):
    container.state, container.message, container.model = ServiceState.ERROR, 'yolo11s.pt failed to load', None
    check = self_test.check_detector(client_for(container))
    assert not check.ok and 'failed to load' in check.message


def test_container_not_running_says_how_to_start_it(container):
    container.away = True
    check = self_test.check_detector(client_for(container))
    assert not check.ok and 'docker compose up -d' in check.message


def test_no_detector_is_fine():
    check = self_test.check_detector(None)
    assert check.ok and '--no-detector' in check.message


# ── temperature ───────────────────────────────────────────────────────
def test_cool_pi_is_fine(tmp_path):
    check = self_test.check_temperature(HealthReader(fake_pi(tmp_path, 52.5)), hot_warning_c=75)
    assert check.ok and '52.5 °C' in check.message


def test_hot_pi_is_a_problem(tmp_path):
    check = self_test.check_temperature(HealthReader(fake_pi(tmp_path, 78)), hot_warning_c=75)
    assert not check.ok and 'too hot' in check.message


def test_slowed_down_right_now_is_a_problem(tmp_path):
    check = self_test.check_temperature(HealthReader(fake_pi(tmp_path, 60, throttled='4')), hot_warning_c=75)
    assert not check.ok and 'the processor is slowed down' in check.message


def test_slowed_down_earlier_is_only_mentioned(tmp_path):
    check = self_test.check_temperature(HealthReader(fake_pi(tmp_path, 60, throttled='10000')), hot_warning_c=75)
    assert check.ok and 'the power supply was too weak' in check.message


def test_no_temperature_is_a_problem(tmp_path):
    check = self_test.check_temperature(HealthReader(tmp_path), hot_warning_c=75)
    assert not check.ok and "couldn't read" in check.message


# ── computer ──────────────────────────────────────────────────────────
def test_computer_that_answers_is_fine(computer):
    check = self_test.check_computer(computer.url, KEY, timeout=2)
    assert check.ok and computer.url in check.message


def test_wrong_key_is_a_problem(computer):
    check = self_test.check_computer(computer.url, 'WRONG', timeout=2)
    assert not check.ok and 'Pairing key does not match' in check.message


def test_computer_away_is_a_problem(computer):
    computer.away = True
    check = self_test.check_computer(computer.url, KEY, timeout=2)
    assert not check.ok and 'Cannot reach the computer' in check.message


def test_computer_set_to_another_camera_is_a_problem(computer):
    computer.camera_source = 'phone'
    check = self_test.check_computer(computer.url, KEY, timeout=2)
    assert not check.ok and 'camera.source: phone' in check.message


def test_no_server_given_is_a_problem():
    check = self_test.check_computer(None, None, timeout=2)
    assert not check.ok and '--server' in check.message


# ── all together ──────────────────────────────────────────────────────
def repo_file(tmp_path, **changes):
    path = tmp_path / 'settings.yaml'
    path.write_text(yaml.safe_dump({'pi': pi_settings(**changes)}))
    return path


def self_test_args(tmp_path, computer, *more):
    pictures = tmp_path / 'pictures'
    pictures.mkdir()
    (pictures / 'a.jpg').write_bytes(b'\xff\xd8 picture')
    return ['--self-test', '--server', computer.url, '--key', KEY, '--camera', f'folder:{pictures}', *more]


def test_self_test_all_fine_ends_with_0(tmp_path, container, computer, caplog):
    computer.pi = pi_settings(detector={'url': container.url})
    with caplog.at_level(logging.INFO):
        code = main(self_test_args(tmp_path, computer), repo_settings=repo_file(tmp_path), sys_root=fake_pi(tmp_path))
    assert code == 0
    assert [line.split()[0] for line in caplog.messages[-5:-1]] == ['OK'] * 4
    assert container.paths('POST') == [] and container.models_put == []      # it only looks
    assert computer.paths('POST') == []


def test_self_test_with_a_problem_ends_with_1(tmp_path, container, computer, caplog):
    computer.pi = pi_settings(detector={'url': container.url})
    container.away = True
    code = main(self_test_args(tmp_path, computer), repo_settings=repo_file(tmp_path), sys_root=fake_pi(tmp_path))
    assert code == 1
    assert 'PROBLEM  detector' in caplog.text
