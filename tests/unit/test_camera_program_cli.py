"""
raspberry_pi/camera/cli.py: the camera program's flags and settings, and starting it with
python3 -m raspberry_pi.camera.
"""

import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from test_camera_program_fakes import JPEG, KEY, FakeComputer, FakeContainer, pi_settings

from desktalk_shared.wire import DetectorState
from raspberry_pi.camera.cli import load_settings, main, parse_args
from raspberry_pi.camera.settings import ComputerProblem

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def container():
    with FakeContainer() as c:
        yield c


@pytest.fixture
def computer():
    with FakeComputer() as c:
        yield c


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


def older_repo_file(tmp_path, *missing) -> Path:
    """The Pi's config/settings.yaml from before some settings were added (the Pi wasn't git-pulled)."""
    pi = {name: value for name, value in pi_settings().items() if name not in missing}
    path = tmp_path / 'settings.yaml'
    path.write_text(yaml.safe_dump({'pi': pi}))
    return path


def test_an_older_pi_settings_file_without_error_text_chars_still_asks_the_computer(tmp_path, computer):
    computer.pi = pi_settings(error_text_chars=42)
    args = parse_args(['--server', computer.url, '--key', KEY])
    assert load_settings(args, older_repo_file(tmp_path, 'error_text_chars')).error_text_chars == 42


def test_without_error_text_chars_the_computers_whole_reason_is_shown(tmp_path, computer, caplog):
    computer.key = 'another key'
    args = parse_args(['--server', computer.url, '--key', KEY])
    with pytest.raises(ComputerProblem, match='restart the camera to read it'):
        load_settings(args, older_repo_file(tmp_path, 'error_text_chars'), need_computer=True)


def test_an_older_pi_settings_file_without_camera_or_error_text_chars_still_starts_offline(tmp_path, caplog):
    """The Pi wasn't git-pulled and can't ask the computer: it uses the values the program had before."""
    args = ['--offline', '--camera', f'folder:{tmp_path}', '--photo-dir', str(tmp_path / 'p')]
    assert main(args, repo_settings=older_repo_file(tmp_path, 'camera', 'error_text_chars')) == 1   # 1: the camera
    assert 'no .jpg files' in caplog.text
    assert "The Pi's config/settings.yaml is older than its camera program" in caplog.text


def test_an_older_pi_settings_file_is_used_when_the_computer_is_away(tmp_path, computer, caplog):
    computer.away = True
    args = parse_args(['--server', computer.url, '--key', KEY])
    settings = load_settings(args, older_repo_file(tmp_path, 'camera', 'error_text_chars'))
    assert (settings.camera, settings.error_text_chars) == ('auto', 200)


def test_computer_away_at_start_uses_the_repos_settings(tmp_path, computer, caplog):
    computer.away = True
    args = parse_args(['--server', computer.url, '--key', KEY])
    assert load_settings(args, repo_file(tmp_path, fps=6)).fps == 6
    assert 'Cannot reach the app' in caplog.text


def test_offline_does_not_ask_the_computer(tmp_path, computer):
    args = parse_args(['--server', computer.url, '--key', KEY, '--offline'])
    load_settings(args, repo_file(tmp_path))
    assert computer.requests == []


def test_missing_server_says_where_to_get_the_command(caplog):
    assert main([]) == 2
    assert "The app's page shows the address and the key" in caplog.text


@pytest.fixture(autouse=True)
def no_container_settings(monkeypatch):
    """The container's own environment must not leak into these tests."""
    for name in ('DESKTALK_SERVER', 'DESKTALK_PAIR_KEY_FILE'):
        monkeypatch.delenv(name, raising=False)


def test_key_comes_from_the_key_file(tmp_path):
    key_file = tmp_path / 'pair-key.txt'
    key_file.write_text(KEY + '\n')
    assert parse_args(['--key-file', str(key_file)]).key == KEY


def test_key_file_with_a_windows_line_ending_and_spaces(tmp_path):
    key_file = tmp_path / 'pair-key.txt'
    key_file.write_text(f'  {KEY}  \r\n')
    assert parse_args(['--key-file', str(key_file)]).key == KEY


def test_the_key_flag_beats_the_key_file(tmp_path):
    key_file = tmp_path / 'pair-key.txt'
    key_file.write_text('from the file\n')
    assert parse_args(['--key', KEY, '--key-file', str(key_file)]).key == KEY


def test_the_server_and_key_file_can_come_from_the_environment(tmp_path, monkeypatch):
    key_file = tmp_path / 'pair-key.txt'
    key_file.write_text(KEY + '\n')
    monkeypatch.setenv('DESKTALK_SERVER', 'http://192.168.1.20:5000')
    monkeypatch.setenv('DESKTALK_PAIR_KEY_FILE', str(key_file))
    args = parse_args([])
    assert (args.server, args.key) == ('http://192.168.1.20:5000', KEY)


def test_flag_beats_the_environment(tmp_path, monkeypatch):
    env_file = tmp_path / 'env-key.txt'
    env_file.write_text('from the environment\n')
    flag_file = tmp_path / 'flag-key.txt'
    flag_file.write_text('from the flag\n')
    monkeypatch.setenv('DESKTALK_SERVER', 'http://environment:5000')
    monkeypatch.setenv('DESKTALK_PAIR_KEY_FILE', str(env_file))
    args = parse_args(['--server', 'http://flag:5000', '--key-file', str(flag_file)])
    assert (args.server, args.key) == ('http://flag:5000', 'from the flag')
    assert parse_args(['--key', 'typed']).key == 'typed'                 # --key beats the environment's file too


def test_missing_key_file_says_so(tmp_path, capsys):
    missing = tmp_path / 'nowhere' / 'pair-key.txt'
    with pytest.raises(SystemExit) as stop:
        parse_args(['--key-file', str(missing)])
    assert stop.value.code == 2
    assert str(missing) in capsys.readouterr().err


def test_missing_key_file_from_the_environment_says_so_too(tmp_path, monkeypatch, capsys):
    missing = tmp_path / 'pair-key.txt'
    monkeypatch.setenv('DESKTALK_PAIR_KEY_FILE', str(missing))
    with pytest.raises(SystemExit):
        parse_args([])
    assert str(missing) in capsys.readouterr().err


def test_an_empty_key_file_says_so(tmp_path, capsys):
    key_file = tmp_path / 'pair-key.txt'
    key_file.write_text('\n')
    with pytest.raises(SystemExit):
        parse_args(['--key-file', str(key_file)])
    err = capsys.readouterr().err
    assert str(key_file) in err and 'empty' in err


def test_missing_server_says_how_to_give_it_in_docker(caplog):
    assert main([]) == 2
    assert 'DESKTALK_SERVER' in caplog.text and 'DESKTALK_PAIR_KEY_FILE' in caplog.text


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


def test_pictures_keep_coming_while_the_computer_cannot_be_reached(tmp_path, container):
    pictures = tmp_path / 'pictures'
    pictures.mkdir()
    (pictures / 'a.jpg').write_bytes(JPEG)
    with socket.socket() as s:                                            # a port nothing listens on
        s.bind(('127.0.0.1', 0))
        away = f'http://127.0.0.1:{s.getsockname()[1]}'
    repo = repo_file(tmp_path, min_free_mb=0, detector={'url': container.url}, restart_after_missed_pictures=10 ** 6,
                     upload_timeout_sec=0.5)
    code = main(['--server', away, '--key', KEY, '--camera', f'folder:{pictures}', '--fps', '50',
                 '--photo-dir', str(tmp_path / 'photos'), '--max-pictures', '10'], repo_settings=repo)
    assert code == 0
    assert len(list((tmp_path / 'photos').glob('*/*.jpg'))) == 10       # every picture taken and saved


def test_help_shows_the_run_command_with_only_the_standard_library():
    """-S: no installed packages at all, so desktalk_shared has to come from the repo's shared/ folder."""
    env = {k: v for k, v in os.environ.items() if k != 'PYTHONPATH'}
    done = subprocess.run([sys.executable, '-S', '-m', 'raspberry_pi.camera', '--help'], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    assert 'python3 -m raspberry_pi.camera --server http://' in done.stdout
    assert '--self-test' in done.stdout


def test_the_camera_comes_from_the_pi_settings_unless_a_flag_says(tmp_path, caplog):
    empty = tmp_path / 'empty'
    empty.mkdir()
    repo = repo_file(tmp_path, camera=f'folder:{empty}')
    assert main(['--offline', '--photo-dir', str(tmp_path / 'p')], repo_settings=repo) == 1
    assert f'no .jpg files in {empty}' in caplog.text
    caplog.clear()
    other = tmp_path / 'other'
    other.mkdir()
    assert main(['--offline', '--camera', f'folder:{other}', '--photo-dir', str(tmp_path / 'p')],
                repo_settings=repo) == 1
    assert f'no .jpg files in {other}' in caplog.text


def test_the_camera_flag_says_where_its_default_comes_from():
    assert parse_args([]).camera is None
