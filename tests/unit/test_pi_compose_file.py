"""raspberry_pi/compose.yaml, .env and settings.pi.yaml: what must hold before Docker is even started."""

from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml

from desktalk.config import load_config

ROOT = Path(__file__).resolve().parents[2]
PI = ROOT / 'raspberry_pi'
SERVICES = {'camera', 'detector', 'app'}


def env_file(path: Path) -> dict[str, str]:
    lines = path.read_text(encoding='utf-8').splitlines()
    return dict(line.split('=', 1) for line in lines if line.strip() and not line.lstrip().startswith('#'))


@pytest.fixture(scope='module')
def compose() -> dict[str, Any]:
    return yaml.safe_load((PI / 'compose.yaml').read_text(encoding='utf-8'))


@pytest.fixture(scope='module')
def services(compose) -> dict[str, Any]:
    return compose['services']


@pytest.fixture(scope='module')
def pi_settings() -> dict[str, Any]:
    return yaml.safe_load((PI / 'settings.pi.yaml').read_text(encoding='utf-8'))


def no_fallbacks(text: str) -> str:
    """${NAME:-value} → ${NAME}. (test_detector_service_docker_files.py checks the fallbacks match .env.)"""
    return re.sub(r'\$\{(\w+):-[^}]*\}', r'${\1}', str(text))


def mounts(service: dict[str, Any]) -> dict[str, str]:
    """Where in the container → the whole volume line."""
    lines = [no_fallbacks(line) for line in service['volumes']]
    return {line.split(':')[1]: line for line in lines}


def test_it_has_the_three_containers(services):
    assert set(services) == SERVICES


@pytest.mark.parametrize('name', sorted(SERVICES))
def test_each_container_restarts_has_a_memory_limit_logs_in_the_journal_and_a_health_check(services, name):
    service = services[name]
    assert service['restart'] == 'unless-stopped'
    assert service['mem_limit'].startswith('${') and service['memswap_limit'] == service['mem_limit']
    assert service['healthcheck']['test']
    assert no_fallbacks(service['environment']['TZ']) == '${TZ}'


@pytest.mark.parametrize('name', sorted(SERVICES))
def test_each_container_logs_to_the_journal_under_its_own_name(services, name):
    """The journal keeps them with the Pi's own messages, through a power cut, within the size install.sh gives it
    (JOURNAL_MAX_USE). raspberry_pi/get_logs.sh finds each one by this name: journalctl -t desktalk-<service>."""
    assert services[name]['logging'] == {'driver': 'journald', 'options': {'tag': f'desktalk-{name}'}}


def test_the_old_log_file_sizes_are_gone():
    """The journald driver takes no file sizes: the journal's own limit is the one."""
    assert not {'LOG_FILE_SIZE', 'LOG_FILES'} & set(env_file(PI / '.env.example'))


def test_every_name_the_compose_file_uses_is_in_the_env_file():
    text = (PI / 'compose.yaml').read_text(encoding='utf-8')
    used = set(re.findall(r'\$\{(\w+)', '\n'.join(line.split(' #')[0] for line in text.splitlines())))
    assert used <= set(env_file(PI / '.env.example'))


def test_only_the_camera_gets_all_the_devices_and_nothing_is_privileged(services):
    assert '/dev' in mounts(services['camera'])
    assert mounts(services['camera'])['/run/udev'].endswith(':ro')
    assert len(services['camera']['device_cgroup_rules']) == 3
    assert services['camera']['group_add']
    for name, service in services.items():
        assert 'privileged' not in service
        assert 'devices' not in service
        if name != 'camera':
            assert '/dev' not in mounts(service)
    assert 'device_cgroup_rules' not in services['detector']     # the app's one rule: test_the_app_gets_the_sound_card


def test_the_detector_has_no_open_port_and_only_the_app_does(services):
    assert 'ports' not in services['detector'] and 'ports' not in services['camera']
    assert [no_fallbacks(port) for port in services['app']['ports']] == ['${APP_HOST_PORT}:${APP_PORT}']


def test_the_app_port_is_the_one_in_the_settings():
    assert int(env_file(PI / '.env.example')['APP_PORT']) == load_config(root=ROOT, environ={}).web.port


def test_the_pairing_key_is_shared_read_only_for_the_camera(services, pi_settings):
    key = Path(pi_settings['camera']['pair_key_file'])
    assert services['camera']['environment']['DESKTALK_PAIR_KEY_FILE'] == str(key)
    assert mounts(services['app'])[str(key.parent)].split(':') == ['pairing', str(key.parent)]
    assert mounts(services['camera'])[str(key.parent)].split(':') == ['pairing', str(key.parent), 'ro']
    assert 'pairing' not in str(services['detector']['volumes'])


def test_the_camera_waits_for_the_app_to_be_healthy_and_uses_its_address(services):
    camera = services['camera']
    assert camera['depends_on']['app']['condition'] == 'service_healthy'
    assert no_fallbacks(camera['environment']['DESKTALK_SERVER']) == 'http://app:${APP_PORT}'


def test_the_app_answers_the_camera_at_the_name_it_uses(services, pi_settings):
    """The app answers only names it knows (web.allowed_hosts); the camera reaches it as `app` on Docker's network."""
    server = no_fallbacks(services['camera']['environment']['DESKTALK_SERVER'])
    assert server.split('//')[1].split(':')[0] in pi_settings['web']['allowed_hosts']


def test_the_cameras_pictures_are_kept_in_memory_where_the_settings_say(compose, services, pi_settings):
    """In memory, not on the SD card: about 3 pictures a second, each written once and deleted minutes later, would
    write hundreds of MB an hour to the card for nothing."""
    folder = pi_settings['pi']['photo_dir']
    camera = services['camera']
    assert folder not in mounts(camera) and 'photos' not in compose.get('volumes', {})
    tmpfs = {line.split(':')[0]: no_fallbacks(line) for line in camera['tmpfs']}
    assert tmpfs[folder] == f'{folder}:size=${{CAMERA_PHOTOS_SIZE}},uid=${{PI_USER_ID}},gid=${{PI_GROUP_ID}},mode=0700'


def test_both_programs_that_read_the_settings_get_the_pi_settings_read_only(services):
    for name in ('app', 'camera'):
        line = mounts(services[name])['/desk-talk/config/settings.local.yaml']
        assert line == './settings.pi.yaml:/desk-talk/config/settings.local.yaml:ro'


def test_both_get_the_users_own_settings_folder_read_only(services):
    """A folder, not a file: Docker makes a missing folder, but a missing file becomes a folder and breaks the mount."""
    for name in ('app', 'camera'):
        assert mounts(services[name])['/desk-talk/config/mine'] == './mine:/desk-talk/config/mine:ro'


def test_the_users_own_settings_are_not_in_git():
    lines = (ROOT / '.gitignore').read_text(encoding='utf-8').splitlines()
    assert 'raspberry_pi/mine/' in lines
    assert (PI / 'settings.mine.example.yaml').is_file()


def test_the_data_folder_location_comes_from_the_env_file(services):
    assert mounts(services['app'])['/desk-talk/data'] == '${DESKTALK_DATA}:/desk-talk/data'
    assert env_file(PI / '.env.example')['DESKTALK_DATA'] == '../data'      # next to the repo's raspberry_pi/ folder


def test_the_app_with_the_pi_settings_loads_without_a_word_about_old_settings(tmp_path, caplog):
    (tmp_path / 'config').mkdir()
    for name in ('settings.yaml', 'protocols.yaml', 'agents.yaml', 'prompts.yaml'):
        shutil.copy(ROOT / 'config' / name, tmp_path / 'config' / name)
    shutil.copy(PI / 'settings.pi.yaml', tmp_path / 'config' / 'settings.local.yaml')
    with caplog.at_level(logging.WARNING):
        config = load_config(root=tmp_path, environ={})
    assert 'no longer used' not in caplog.text
    assert config.detector.enabled is False and config.dev.enabled is False
    assert config.camera.source == 'pi'
    assert config.pi.detector.url.startswith('http://detector:')
    assert not config.secrets.gemini_api_key.get_secret_value()


def test_the_pi_settings_set_no_ai_and_keep_pictures_home(pi_settings):
    assert not pi_settings['llm'].get('ai_url')
    assert pi_settings['llm']['send_pictures'] is False
    assert pi_settings['chat']['ai_writes_warnings'] is False


def test_the_pi_speaks_through_a_usb_card_and_a_reminder_waits_briefly(pi_settings, tmp_path):
    (tmp_path / 'config').mkdir()
    for name in ('settings.yaml', 'protocols.yaml', 'agents.yaml', 'prompts.yaml'):
        shutil.copy(ROOT / 'config' / name, tmp_path / 'config' / name)
    shutil.copy(PI / 'settings.pi.yaml', tmp_path / 'config' / 'settings.local.yaml')
    config = load_config(root=tmp_path, environ={})
    assert config.notifications.output == 'speaker' and config.notifications.sound_card == 'usb'
    assert config.llm.reminder_wait_sec < config.llm.timeout_sec


def test_a_computer_keeps_todays_behaviour():
    config = load_config(environ={}, dotenv=ROOT / 'no-such-file')
    assert config.notifications.output == 'mac' and config.chat.ai_writes_warnings is True
    assert config.llm.send_pictures is True and config.llm.ai_url == ''
    assert config.llm.reminder_wait_sec > config.llm.longest_reply_sec()


def test_the_app_gets_the_sound_card_and_no_other_device(compose):
    app = compose['services']['app']
    binds = [str(v) for v in app['volumes']]
    assert not any(v.startswith('/dev:') for v in binds) and 'privileged' not in app and 'devices' not in app
    assert '${SOUND_DEVICES:-/dev/snd}:/dev/snd' in binds
    assert '${SOUND_CARDS_FILE:-/proc/asound/cards}:/sound-cards:ro' in binds
    assert app['device_cgroup_rules'] == ['c ${SOUND_DEVICE_MAJOR:-116}:* rw']
    assert app['environment']['PI_AUDIO_GROUP'] == '${PI_AUDIO_GROUP:-29}'


def test_the_card_list_is_where_the_settings_look(pi_settings, compose):
    inside = next(v for v in compose['services']['app']['volumes'] if 'SOUND_CARDS_FILE' in str(v)).split(':')[-2]
    assert pi_settings['notifications']['sound_cards_file'] == inside


def test_the_app_can_see_whether_the_pis_clock_has_synced(compose):
    """systemd-timesyncd makes `synchronized` in this folder once the clock is set from the internet."""
    binds = [str(v) for v in compose['services']['app']['volumes']]
    assert '${CLOCK_SYNC_FOLDER:-/run/systemd/timesync}:/run/timesync:ro' in binds


def test_the_clock_flag_is_where_the_settings_look(pi_settings, compose):
    inside = next(v for v in compose['services']['app']['volumes'] if 'CLOCK_SYNC_FOLDER' in str(v)).split(':')[-2]
    assert pi_settings['object_states']['clock_synced_file'] == f'{inside}/synchronized'


def test_a_computer_trusts_its_own_clock():
    """Only the Pi names a flag file: a Mac keeps its time with no internet."""
    settings = yaml.safe_load((ROOT / 'config' / 'settings.yaml').read_text(encoding='utf-8'))
    assert settings['object_states']['clock_synced_file'] is None


def test_the_voice_is_in_the_image_where_the_settings_say():
    config = load_config(environ={}, dotenv=ROOT / 'no-such-file')
    dockerfile = (PI / 'app' / 'Dockerfile').read_text(encoding='utf-8')
    assert 'COPY --from=voice /opt/piper /opt/piper' in dockerfile
    assert config.notifications.voice_model.startswith('/opt/piper/')
    assert f'-o {Path(config.notifications.voice_model).name} ' in dockerfile


def test_the_app_image_can_see_no_keys_or_data():
    lines = [line for line in (PI / 'app' / 'Dockerfile.dockerignore').read_text(encoding='utf-8').splitlines()
             if not line.startswith('#')]
    assert lines[0] == '*'
    let_in = {line[1:] for line in lines if line.startswith('!')}
    assert not {'.env', 'data', 'certs', 'models'} & let_in
    assert 'config/settings.local.yaml' in lines


def test_without_buildkit_the_top_list_keeps_the_same_things_out():
    lines = (ROOT / '.dockerignore').read_text(encoding='utf-8').splitlines()
    assert {'**/.env', 'config/settings.local.yaml', 'raspberry_pi/mine', 'data', 'certs', 'models'} <= set(lines)


def test_the_camera_gets_as_few_rights_as_it_can_work_with(services):
    camera = services['camera']
    assert all(rule.endswith(' rw') for rule in camera['device_cgroup_rules'])
    assert camera['cap_drop'] == ['ALL'] and camera['security_opt'] == ['no-new-privileges:true']
    assert {line.split(':')[0] for line in camera['tmpfs']} == {'/dev/shm', '/dev/pts', '/dev/mqueue', '/photos'}


@pytest.mark.parametrize('name', ['app', 'detector'])
def test_a_hung_program_ends_itself_so_docker_starts_it_again(services, name):
    service = services[name]
    assert service['init'] is True          # without it the program is PID 1, which can't be stopped from inside
    assert service['healthcheck']['test'] == ['CMD', 'python', '/usr/local/bin/desktalk-health']
    environment = {key: no_fallbacks(value) for key, value in service['environment'].items()}
    prefix = name.upper()
    assert environment['HEALTH_ANSWER_WAIT_SEC'] == f'${{{prefix}_HEALTH_ANSWER_WAIT_SEC}}'
    assert environment['HEALTH_GRACE_SEC'] == f'${{{prefix}_HEALTH_GRACE_SEC}}'
    assert environment['HEALTH_FAILS_BEFORE_RESTART'] == f'${{{prefix}_HEALTH_FAILS_BEFORE_RESTART}}'


@pytest.mark.parametrize('name', ['APP', 'DETECTOR'])
def test_the_health_check_gives_up_on_an_answer_before_docker_gives_up_on_it(name):
    values = env_file(PI / '.env.example')
    assert float(values[f'{name}_HEALTH_ANSWER_WAIT_SEC']) < int(values[f'{name}_HEALTH_TIMEOUT'].removesuffix('s'))


def test_a_detector_changing_a_model_has_minutes_before_it_could_be_ended():
    values = env_file(PI / '.env.example')
    every = int(values['DETECTOR_HEALTH_INTERVAL'].removesuffix('s'))
    assert every * int(values['DETECTOR_HEALTH_FAILS_BEFORE_RESTART']) >= 5 * 60
    assert int(values['DETECTOR_HEALTH_GRACE_SEC']) >= 5 * 60


def test_ultralytics_sends_nothing_home():
    text = (PI / 'detector_service' / 'Dockerfile').read_text(encoding='utf-8')
    assert 'YOLO_OFFLINE=true' in text and 'sync=False' in text


def test_only_the_pi_reads_a_boot_id():
    """The Pi tells a restart of only the app from a power cut by its boot id. A computer with no flag file trusts its
    clock anyway, so it names no boot id file, and the tests run the same on a Mac and on Linux."""
    settings = yaml.safe_load((ROOT / 'config' / 'settings.yaml').read_text(encoding='utf-8'))
    assert settings['object_states']['boot_id_file'] is None


def test_the_pi_reads_the_kernels_boot_id(pi_settings):
    """/proc needs nothing in compose.yaml: every container shares the Pi's kernel, so it shows the Pi's own id."""
    assert pi_settings['object_states']['boot_id_file'] == '/proc/sys/kernel/random/boot_id'


def test_the_app_sees_the_pis_own_programs_and_network_read_only(pi_settings, compose):
    """For the developer page's Pi tab: the app container's own /proc shows only its own programs and network."""
    line = next(str(v) for v in compose['services']['app']['volumes'] if str(v).startswith('/proc:'))
    outside, inside, mode = line.split(':')
    assert (outside, mode) == ('/proc', 'ro')
    assert pi_settings['system_stats'] == {'enabled': True, 'host_proc_folder': inside}


def test_a_computer_reads_no_system_numbers():
    """Only the Pi reads them: on a Mac there's no /proc, and the developer page fetches the Pi's."""
    settings = yaml.safe_load((ROOT / 'config' / 'settings.yaml').read_text(encoding='utf-8'))
    assert settings['system_stats']['enabled'] is False
