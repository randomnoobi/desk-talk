"""raspberry_pi/camera/settings.py: the camera program's settings, from the repo, the computer and flags."""

import logging
from pathlib import Path

import pytest
from test_camera_program_fakes import KEY, MODEL, FakeClock, FakeComputer, FakeContainer, pi_settings

from desktalk_shared.detector_api import ModelSettings
from raspberry_pi.camera.detector_client import DetectorClient
from raspberry_pi.camera.settings import (
    ComputerProblem,
    SettingsError,
    SettingsWatcher,
    computer_config,
    from_layers,
    read_repo_settings,
)


def test_later_layers_win_and_detector_settings_merge_one_by_one():
    repo = pi_settings()
    computer = {'fps': 2, 'delete_every_min': 5, 'detector': {'image_size': 320}}
    flags = {'fps': 4}
    settings = from_layers(repo, computer, flags)
    assert settings.fps == 4                              # flag wins
    assert settings.delete_every_min == 5                 # from the computer
    assert settings.jpeg_quality == 80                    # from the repo's config/settings.yaml
    assert settings.detector.model == ModelSettings(model='yolo11s.pt', format='ncnn', image_size=320,
                                                    confidence=0.35, half=False)
    assert settings.detector.url == 'http://127.0.0.1:8100'
    assert settings.detector.failures_before_pause == 3


def test_a_missing_setting_is_named():
    repo = pi_settings()
    del repo['camera_warmup_sec']
    with pytest.raises(SettingsError, match=r'pi\.camera_warmup_sec is missing'):
        from_layers(repo)


def test_a_missing_detector_setting_is_named():
    repo = pi_settings()
    del repo['detector']['pause_max_sec']
    with pytest.raises(SettingsError, match=r'pi\.detector\.pause_max_sec is missing'):
        from_layers(repo)


@pytest.mark.parametrize(('changes', 'message'), [
    ({'fps': 'fast'}, r"pi\.fps must be a number above 0 \(got 'fast'\)"),
    ({'fps': 0}, r'pi\.fps must be a number above 0'),
    ({'keep_last_min': -1}, r'pi\.keep_last_min must be a number, 0 or more'),
    ({'width': 12.5}, r'pi\.width must be a whole number above 0'),
    ({'jpeg_quality': 101}, r'pi\.jpeg_quality must be a whole number from 1 to 100'),
    ({'photo_dir': 7}, r'pi\.photo_dir must be text'),
    ({'detector': {'failures_before_pause': 0}}, r'pi\.detector\.failures_before_pause must be a whole number above 0'),
    ({'detector': {'format': 'onnx'}}, r'pi\.detector: format must be one of ncnn, pt'),
    ({'detector': {'model': 'yolo11s_ncnn_model'}}, r'pi\.detector\.model must be a \.pt file name, like yolo11s\.pt'),
    ({'upload_retry_start_sec': 20}, r'pi\.upload_retry_start_sec \(20\) must not be more than '
                                     r'pi\.upload_retry_max_sec \(10\)'),
    ({'detector': {'pause_start_sec': 90}}, r'pi\.detector\.pause_start_sec \(90\) must not be more than '
                                            r'pi\.detector\.pause_max_sec \(60\)'),
])
def test_a_bad_value_is_named_in_plain_words(changes, message):
    with pytest.raises(SettingsError, match=message):
        from_layers(pi_settings(**changes))


def test_delete_every_min_0_means_never():
    """The old camera program treated 0 as "never clean up"; a Pi set up that way keeps working."""
    assert from_layers(pi_settings(delete_every_min=0)).delete_every_min == 0
    with pytest.raises(SettingsError, match=r'pi\.delete_every_min must be a number, 0 or more'):
        from_layers(pi_settings(delete_every_min=-1))


def test_restart_after_missed_pictures_is_a_whole_number():
    assert from_layers(pi_settings(restart_after_missed_pictures=12)).restart_after_missed_pictures == 12
    with pytest.raises(SettingsError, match=r'pi\.restart_after_missed_pictures must be a whole number above 0'):
        from_layers(pi_settings(restart_after_missed_pictures=0))


def test_the_repos_own_pi_section_has_every_setting_the_camera_program_needs():
    repo = read_repo_settings(Path(__file__).resolve().parents[2] / 'config' / 'settings.yaml')
    settings = from_layers(repo)
    assert settings.restart_after_missed_pictures > 0


def test_true_is_not_a_number():
    with pytest.raises(SettingsError, match=r'pi\.fps must be a number'):
        from_layers(pi_settings(fps=True))


def test_reads_the_pi_section_of_the_repos_settings_file(tmp_path):
    path = tmp_path / 'settings.yaml'
    path.write_text('camera:\n  fps: 9\npi:\n  fps: 3\n  detector:\n    image_size: 640\n')
    assert read_repo_settings(path) == {'fps': 3, 'detector': {'image_size': 640}}


def test_no_settings_file_gives_nothing(tmp_path):
    assert read_repo_settings(tmp_path / 'missing.yaml') == {}


def test_a_broken_settings_file_says_so(tmp_path):
    path = tmp_path / 'settings.yaml'
    path.write_text('pi: [unclosed\n')
    with pytest.raises(SettingsError, match='settings.yaml'):
        read_repo_settings(path)


# ── asking the computer ───────────────────────────────────────────────
def test_asks_the_computer_for_its_settings_with_the_key():
    with FakeComputer(pi=pi_settings(fps=2)) as computer:
        reply = computer_config(computer.url, KEY, timeout=2)
        assert reply['pi']['fps'] == 2 and reply['camera_source'] == 'pi'
        assert computer.requests[0].headers['x-desk-key'] == KEY


def test_a_wrong_key_is_a_plain_problem():
    with FakeComputer() as computer, pytest.raises(ComputerProblem, match='Pairing key does not match'):
        computer_config(computer.url, 'WRONG', timeout=2)


def test_a_computer_that_is_away_is_a_plain_problem():
    with FakeComputer() as computer:
        computer.away = True
        with pytest.raises(ComputerProblem, match='Cannot reach the computer'):
            computer_config(computer.url, KEY, timeout=2)


# ── settings that change on the computer ──────────────────────────────
EVERY = 60


@pytest.fixture
def parts():
    with FakeContainer() as container, FakeComputer() as computer:
        computer.pi = pi_settings(detector={'url': container.url})
        clock = FakeClock()
        current = from_layers(computer.pi)
        client = DetectorClient(current.detector, clock)
        client.upkeep()                                     # the model is sent on start

        def load():
            return from_layers(computer_config(computer.url, KEY, timeout=2)['pi'])

        watcher = SettingsWatcher(load, current, client, every_sec=EVERY, clock=clock)
        yield container, computer, clock, client, watcher


def test_new_detector_settings_on_the_computer_go_to_the_container(parts):
    container, computer, clock, client, watcher = parts
    computer.pi = pi_settings(detector={'model': 'yolo11m.pt', 'image_size': 640, 'url': container.url})
    clock.advance(EVERY)
    assert watcher.tick() == EVERY
    client.upkeep()
    assert [m.model for m in container.models_put] == ['yolo11s.pt', 'yolo11m.pt']
    assert container.models_put[-1].image_size == 640


def test_the_first_check_is_a_while_after_start(parts):
    container, computer, clock, client, watcher = parts
    assert watcher.tick() == EVERY
    assert computer.requests == []


def test_unchanged_settings_send_nothing(parts):
    container, computer, clock, client, watcher = parts
    clock.advance(EVERY)
    watcher.tick()
    client.upkeep()
    assert container.models_put == [MODEL]


def test_computer_away_changes_nothing(parts):
    container, computer, clock, client, watcher = parts
    computer.away = True
    clock.advance(EVERY)
    assert watcher.tick() == EVERY
    client.upkeep()
    assert container.models_put == [MODEL]


def test_other_changed_settings_need_a_restart_and_say_so_once(parts, caplog):
    container, computer, clock, client, watcher = parts
    computer.pi = pi_settings(fps=5, detector={'url': container.url})
    with caplog.at_level(logging.INFO):
        for _ in range(3):
            clock.advance(EVERY)
            watcher.tick()
    assert caplog.text.count('restart the camera program') == 1
    client.upkeep()
    assert container.models_put == [MODEL]
