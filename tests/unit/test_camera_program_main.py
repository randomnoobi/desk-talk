"""
raspberry_pi/camera/main.py: the camera program's loop (take, save, detect, send), its threads,
its flags and settings, and starting it with python3 -m raspberry_pi.camera.
"""

import logging
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest
import yaml
from test_camera_program_fakes import JPEG, KEY, FakeCamera, FakeClock, FakeComputer, FakeContainer, cup, pi_settings

from desktalk_shared.wire import DetectorState
from raspberry_pi.camera.main import WorkerThread, build_program, load_settings, main, parse_args
from raspberry_pi.camera.settings import from_layers
from raspberry_pi.camera.watchdog import Watchdog

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def container():
    with FakeContainer() as c:
        yield c


@pytest.fixture
def computer():
    with FakeComputer() as c:
        yield c


def fake_pi(tmp_path, temp_c=50.0):
    """A folder that looks like the Pi's /sys."""
    root = tmp_path / 'pi'
    zone = root / 'sys/class/thermal/thermal_zone0'
    zone.mkdir(parents=True)
    (zone / 'type').write_text('cpu-thermal\n')
    (zone / 'temp').write_text(f'{int(temp_c * 1000)}\n')
    firmware = root / 'sys/devices/platform/soc/soc:firmware'
    firmware.mkdir(parents=True)
    (firmware / 'get_throttled').write_text('0\n')
    return root


def make_program(tmp_path, container, computer, clock, camera=None, offline=False, no_detector=False, **changes):
    changes = {'photo_dir': str(tmp_path / 'photos'), 'min_free_mb': 0, 'detector': {'url': container.url}, **changes}
    settings = from_layers(pi_settings(**changes))
    return build_program(settings, camera or FakeCamera(), server=computer.url, key=KEY, offline=offline,
                         no_detector=no_detector, clock=clock, sys_root=fake_pi(tmp_path))


def states(computer):
    return [(u.info.frame_id, u.info.detector) for u in computer.uploads]


# ── one picture at a time ─────────────────────────────────────────────
def test_each_picture_is_saved_and_the_first_one_goes_to_the_detector(tmp_path, container, computer):
    clock = FakeClock()
    program, workers = make_program(tmp_path, container, computer, clock)
    first = program.step()
    second = program.step()
    assert (first.frame_id, second.frame_id) == ('pi-000001', 'pi-000002')
    assert first.taken_at == clock.now()
    assert program.store.count() == 2
    workers.uploader.tick()
    assert states(computer) == [('pi-000002', DetectorState.BUSY)]       # the detector is still on the first


def test_a_detected_picture_goes_out_with_what_was_found(tmp_path, container, computer):
    container.found = (cup(),)
    program, workers = make_program(tmp_path, container, computer, FakeClock(), camera=FakeCamera(lux=88.0))
    program.step()
    workers.detector.tick()
    workers.uploader.tick()
    [upload] = computer.uploads
    assert upload.jpeg == JPEG
    assert (upload.info.frame_id, upload.info.detector) == ('pi-000001', DetectorState.OK)
    assert upload.info.detections == (cup(),) and upload.info.detect_ms == 123.0
    assert upload.info.model == 'yolo11s.pt (ncnn, 480)' and upload.info.lux == 88.0


def test_every_picture_carries_how_the_pi_is_doing(tmp_path, container, computer):
    program, workers = make_program(tmp_path, container, computer, FakeClock())
    program.step()
    program.step()
    workers.uploader.tick()
    health = computer.uploads[0].info.health
    assert health.camera == 'fake' and health.cpu_temp_c == 50.0 and health.throttled == '0x0'
    assert health.photos_stored == 1 and health.disk_free_mb > 0


def test_how_the_pi_is_doing_is_checked_every_few_seconds(tmp_path, container, computer):
    clock = FakeClock()
    program, workers = make_program(tmp_path, container, computer, clock, health_every_sec=3)
    program.step()
    clock.advance(2)
    program.step()
    assert program.health_now.photos_stored == 1                         # not checked again yet
    clock.advance(1)
    program.step()
    assert program.health_now.photos_stored == 3


def test_container_down_sends_pictures_as_off_with_the_reason(tmp_path, container, computer):
    container.detect_status = 503
    program, workers = make_program(tmp_path, container, computer, FakeClock())
    program.step()
    workers.detector.tick()
    workers.uploader.tick()
    program.step()
    workers.uploader.tick()
    assert states(computer) == [('pi-000001', DetectorState.OFF), ('pi-000002', DetectorState.OFF)]
    assert computer.uploads[1].info.detector_reason == 'no model loaded yet'
    assert container.paths('POST') == ['/detect']


def test_no_detector_sends_every_picture_as_off(tmp_path, container, computer):
    program, workers = make_program(tmp_path, container, computer, FakeClock(), no_detector=True)
    program.step()
    workers.uploader.tick()
    assert states(computer) == [('pi-000001', DetectorState.OFF)]
    assert 'turned off' in computer.uploads[0].info.detector_reason
    assert workers.detector is None and container.requests == []


def test_offline_only_takes_and_saves_pictures(tmp_path, container, computer):
    program, workers = make_program(tmp_path, container, computer, FakeClock(), offline=True)
    program.step()
    assert program.store.count() == 1
    assert (workers.uploader, workers.detector, workers.settings) == (None, None, None)
    assert computer.requests == [] and container.requests == []


def test_disk_nearly_full_deletes_the_oldest_pictures(tmp_path, container, computer):
    program, workers = make_program(tmp_path, container, computer, FakeClock(), min_free_mb=10 ** 12)
    program.step()
    assert program.store.count() == 0


# ── the loop ──────────────────────────────────────────────────────────
def test_takes_pictures_at_the_set_rate(tmp_path, container, computer):
    clock = FakeClock()
    program, workers = make_program(tmp_path, container, computer, clock, fps=4)
    start = clock.monotonic()
    program.run(max_pictures=5)
    assert program.taken == 5
    assert clock.monotonic() - start == pytest.approx(1.0)                # 4 gaps of 0.25 s


def test_a_slow_picture_does_not_make_the_next_ones_hurry(tmp_path, container, computer):
    clock = FakeClock()
    camera = FakeCamera()
    taken_at = []

    def slow_capture(real=camera.capture):
        taken_at.append(clock.monotonic())
        if len(taken_at) == 2:
            clock.advance(2)                                              # the camera hangs for 2 s
        return real()

    camera.capture = slow_capture
    program, workers = make_program(tmp_path, container, computer, clock, camera=camera, fps=4)
    program.run(max_pictures=4)
    gaps = [b - a for a, b in zip(taken_at, taken_at[1:], strict=False)]
    assert gaps == pytest.approx([0.25, 2.0, 0.25])                       # no burst to catch up


def test_a_camera_problem_waits_and_tries_again(tmp_path, container, computer, caplog):
    clock = FakeClock()
    camera = FakeCamera()
    camera.broken = OSError('camera cable loose')
    program, workers = make_program(tmp_path, container, computer, clock, camera=camera, fps=4,
                                    pause_after_error_sec=0.5)
    program.step_safely()
    program.step_safely()
    camera.broken = None
    program.step_safely()
    assert program.errors == 2 and program.taken == 1
    assert clock.slept == [0.5, 0.5]
    assert caplog.text.count('camera cable loose') == 1


def test_pictures_are_deleted_on_schedule(tmp_path, container, computer):
    clock = FakeClock()
    program, workers = make_program(tmp_path, container, computer, clock, delete_every_min=10)
    for _ in range(6):
        program.step()
    started = clock.now()
    assert program.maybe_cleanup(started + 60) is None                    # not yet
    assert program.maybe_cleanup(started + 601) == 6                      # 10 minutes later: all gone
    assert program.store.count() == 0
    assert program.maybe_cleanup(started + 700) is None                   # the next one is 10 minutes after that
    assert program.maybe_cleanup(started + 1202) == 0


def test_delete_every_min_0_never_deletes(tmp_path, container, computer):
    clock = FakeClock()
    program, workers = make_program(tmp_path, container, computer, clock, delete_every_min=0)
    program.step()
    assert program.maybe_cleanup(clock.now() + 10 ** 6) is None
    assert program.store.count() == 1


def test_a_cleanup_that_fails_does_not_stop_the_camera(tmp_path, container, computer, caplog):
    clock = FakeClock()
    program, workers = make_program(tmp_path, container, computer, clock, fps=4, delete_every_min=0.001)

    def broken_cleanup(now):
        raise PermissionError(13, 'Permission denied', str(program.store.folder))

    program.store.cleanup = broken_cleanup
    program.run(max_pictures=5)
    assert program.taken == 5
    assert caplog.text.count('Permission denied') == 1


def test_a_picture_that_cannot_be_saved_is_still_detected_and_sent(tmp_path, container, computer, caplog):
    program, workers = make_program(tmp_path, container, computer, FakeClock())

    def disk_full(jpeg, taken_at, frame_id):
        raise OSError(28, 'No space left on device')

    program.store.save = disk_full
    program.step_safely()
    workers.detector.tick()
    workers.uploader.tick()
    assert states(computer) == [('pi-000001', DetectorState.OK)]
    program.step_safely()
    assert program.taken == 2 and program.errors == 0
    assert caplog.text.count('No space left on device') == 1


# ── the watchdog ──────────────────────────────────────────────────────
def test_the_watchdog_stops_the_program_when_no_picture_comes(caplog):
    clock = FakeClock()
    stops = []
    dog = Watchdog(10, clock, stop_now=lambda: stops.append(clock.monotonic()))
    clock.advance(9)
    assert dog.tick() == pytest.approx(1)
    dog.picture_taken()
    clock.advance(9.9)
    dog.tick()
    assert stops == []
    clock.advance(0.1)
    dog.tick()
    assert stops == [19.0]
    assert 'systemd' in caplog.text


def test_each_picture_taken_keeps_the_watchdog_quiet(tmp_path, container, computer):
    clock = FakeClock()
    program, workers = make_program(tmp_path, container, computer, clock, fps=2, restart_after_missed_pictures=6)
    stops = []
    workers.watchdog.stop_now = lambda: stops.append(clock.monotonic())
    clock.advance(2.5)
    program.step()                                                        # a picture: the 3 s start again
    clock.advance(2.5)
    workers.watchdog.tick()
    assert stops == []
    clock.advance(0.5)                                                    # 6 pictures at 2 a second = 3 s
    workers.watchdog.tick()
    assert stops == [5.5]


def test_a_camera_that_keeps_failing_also_trips_the_watchdog(tmp_path, container, computer):
    clock = FakeClock()
    camera = FakeCamera()
    camera.broken = OSError('camera cable loose')
    program, workers = make_program(tmp_path, container, computer, clock, camera=camera, fps=2,
                                    restart_after_missed_pictures=6)
    stops = []
    workers.watchdog.stop_now = lambda: stops.append(clock.monotonic())
    for _ in range(7):
        program.step_safely()                                             # 0.5 s pause after each failure
    workers.watchdog.tick()
    assert stops == [3.5]


def test_writes_a_status_line_now_and_then(tmp_path, container, computer, caplog):
    clock = FakeClock()
    program, workers = make_program(tmp_path, container, computer, clock, status_every_sec=30)
    with caplog.at_level(logging.INFO):
        program.step()
        program.maybe_log_status()
        clock.advance(30)
        program.maybe_log_status()
    assert caplog.text.count('Pictures taken 1') == 1


# ── background threads ────────────────────────────────────────────────
def test_worker_thread_ticks_again_when_woken_and_stops():
    ticks = threading.Semaphore(0)
    wake = threading.Event()

    def tick():
        ticks.release()
        return None                                                       # wait until woken

    thread = WorkerThread('test', tick, wake, pause_after_error_sec=0.01)
    thread.start()
    assert ticks.acquire(timeout=2)
    wake.set()
    assert ticks.acquire(timeout=2)
    thread.stop()
    thread.join(2)
    assert not thread.is_alive()


def test_worker_thread_keeps_going_after_a_problem(caplog):
    calls = []
    done = threading.Event()

    def tick():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError('one bad round')
        done.set()
        return None

    thread = WorkerThread('test', tick, threading.Event(), pause_after_error_sec=0.01)
    thread.start()
    assert done.wait(2)
    thread.stop()
    thread.join(2)
    assert 'one bad round' in caplog.text


# ── settings and flags ────────────────────────────────────────────────
def repo_file(tmp_path, **changes) -> Path:
    path = tmp_path / 'settings.yaml'
    path.write_text(yaml.safe_dump({'pi': pi_settings(**changes)}))
    return path


def test_flags_beat_the_computer_which_beats_the_repo(tmp_path, computer):
    computer.pi = {'fps': 2, 'delete_every_min': 5}
    args = parse_args(['--server', computer.url, '--key', KEY, '--fps', '4'])
    settings = load_settings(args, repo_file(tmp_path, jpeg_quality=70))
    assert settings.fps == 4                   # flag wins
    assert settings.delete_every_min == 5      # from the computer's settings
    assert settings.jpeg_quality == 70         # from config/settings.yaml (pi:) in the repo


def test_computer_away_at_start_uses_the_repos_settings(tmp_path, computer, caplog):
    computer.away = True
    args = parse_args(['--server', computer.url, '--key', KEY])
    assert load_settings(args, repo_file(tmp_path, fps=6)).fps == 6
    assert 'Cannot reach the computer' in caplog.text


def test_offline_does_not_ask_the_computer(tmp_path, computer):
    args = parse_args(['--server', computer.url, '--key', KEY, '--offline'])
    load_settings(args, repo_file(tmp_path))
    assert computer.requests == []


def test_missing_server_says_where_to_get_the_command(caplog):
    assert main([]) == 2
    assert 'server.py' in caplog.text


def test_bad_settings_stop_it_with_the_reason(tmp_path, caplog):
    assert main(['--offline', '--fps', '0'], repo_settings=repo_file(tmp_path)) == 2
    assert 'pi.fps must be a number above 0' in caplog.text


def test_a_camera_that_cannot_open_stops_it_with_the_reason(tmp_path, caplog):
    args = ['--offline', '--camera', f'folder:{tmp_path}', '--photo-dir', str(tmp_path / 'p')]
    assert main(args, repo_settings=repo_file(tmp_path)) == 1
    assert 'no .jpg files' in caplog.text


def test_runs_end_to_end_with_the_container_and_the_computer(tmp_path, container, computer):
    pictures = tmp_path / 'pictures'
    pictures.mkdir()
    (pictures / 'a.jpg').write_bytes(JPEG)
    # a picture now and then takes longer on a busy test machine: the watchdog must not stop pytest itself
    repo = repo_file(tmp_path, min_free_mb=0, detector={'url': container.url}, restart_after_missed_pictures=10 ** 6)
    computer.pi = {}                                                      # the repo's settings are enough
    code = main(['--server', computer.url, '--key', KEY, '--camera', f'folder:{pictures}', '--fps', '50',
                 '--photo-dir', str(tmp_path / 'photos'), '--max-pictures', '15'], repo_settings=repo)
    assert code == 0
    assert container.models_put and container.paths('POST')
    infos = [u.info for u in computer.uploads]
    assert infos and all(u.jpeg == JPEG for u in computer.uploads)
    assert DetectorState.OK in {i.detector for i in infos}
    assert len({i.frame_id for i in infos}) == len(infos)                 # each picture at most once
    assert len(list((tmp_path / 'photos').glob('*/*.jpg'))) == 15


def test_help_shows_the_run_command_with_only_the_standard_library():
    """-S: no installed packages at all, so desktalk_shared has to come from the repo's shared/ folder."""
    env = {k: v for k, v in os.environ.items() if k != 'PYTHONPATH'}
    done = subprocess.run([sys.executable, '-S', '-m', 'raspberry_pi.camera', '--help'], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    assert 'python3 -m raspberry_pi.camera --server http://' in done.stdout
    assert '--self-test' in done.stdout
