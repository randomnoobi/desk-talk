"""raspberry_pi/install.sh and update.sh, run for real in a made-up Pi: a copy of the files they read in a temporary
folder, with stand-ins on PATH for everything that would touch the machine (docker, sudo, curl, hostnamectl ...). Each
stand-in writes down how it was called in `calls`, so nothing here can change this computer."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest
import yaml

from desktalk.config import load_config
from tests.unit.test_pi_compose_file import env_file

ROOT = Path(__file__).resolve().parents[2]
PI = ROOT / 'raspberry_pi'
CODE = 'SECRET-CODE'
SERVER = 'http://192.168.1.9:8080'
BOOT_LINE = 'console=tty1 root=PARTUUID=abc rootwait'
FLAGS = 'cgroup_enable=memory cgroup_memory=1'

STAND_IN = r'''#!/bin/bash
# Writes down the call, then does what the test's state folder says.
printf '%s\n' "$(basename "$0") $*" >> "$FAKE/calls"
name=$(basename "$0")
case "$name" in
  uname) cat "$FAKE/uname" ;;
  getconf) cat "$FAKE/long_bit" ;;
  df) printf 'Filesystem 1M-blocks Used Available Capacity Mounted\n/dev/x 100000 1000 %s 1%% /\n' "$(cat "$FAKE/free_mb")" ;;
  getent) case "$2" in video) echo 'video:x:45:sam' ;; audio) echo 'audio:x:30:sam' ;; *) exit 2 ;; esac ;;
  id) case "$*" in -u) cat "$FAKE/uid" ;; -g) echo 1002 ;; -un) echo sam ;; "-nG sam") cat "$FAKE/groups" ;; esac ;;
  hostname) cat "$FAKE/hostname" ;;
  hostnamectl) echo "$2" > "$FAKE/hostname" ;;
  timedatectl) echo Europe/Paris ;;
  systemctl) [ -e "$FAKE/old_camera_running" ] && [ "$1" = is-active ] ;;
  git) [ "$1" = -C ] && [ "$3" = rev-parse ] && exit "$(cat "$FAKE/git_checkout")"; exit 0 ;;
  sha256sum) "$FAKE_PYTHON" -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest(), sys.argv[1])' "$1" ;;
  curl)
    while [ $# -gt 0 ]; do case "$1" in -o|--output) out=$2; shift ;; http*) url=$1 ;; esac; shift; done
    case "$url" in
      *get.docker*) echo 'echo installed-docker' > "$out" ;;
      *) [ -e "$FAKE/download_fails" ] && exit 22; cat "$FAKE/model_bytes" > "$out" ;;
    esac ;;
  sudo)
    cmd=$1; shift
    case "$cmd" in
      docker) FAKE_SUDO=1 exec "$FAKE/bin/docker" "$@" ;;
      tee|cp) exec "$cmd" "$@" ;;
      sh) rm -f "$FAKE/no_docker"; touch "$FAKE/docker_group_active" ;;
      hostnamectl) exec "$FAKE/bin/hostnamectl" "$@" ;;
      *) : ;;
    esac ;;
  docker)
    [ -e "$FAKE/no_docker" ] && exit 127
    case "$*" in
      "compose version") exit 0 ;;
      info) [ -e "$FAKE/docker_group_active" ] || [ -n "${FAKE_SUDO:-}" ] ;;
      "ps -aq --filter label=com.docker.compose.project=desktalk-detector") cat "$FAKE/old_containers" 2>/dev/null; exit 0 ;;
      "volume ls -q "*"project=desktalk-detector"*) cat "$FAKE/old_volume" 2>/dev/null; exit 0 ;;
      compose\ *--volumes*|compose\ *down*" -v"*) rm -f "$FAKE/pairing-key" "$FAKE/models/"*; exit 0 ;;
      "compose up -d --build") [ -e "$FAKE/up_fails" ] && exit 1; exit 0 ;;
      "compose exec -T detector test -s "*) [ -s "$FAKE/models/$(basename "$7")" ] ;;
      "compose cp "*) cp "$3" "$FAKE/models/$(basename "${4#*:}")" ;;
      "compose exec -T detector mv "*) mv "$FAKE/models/$(basename "$6")" "$FAKE/models/$(basename "$7")" ;;
      "compose exec -T "*"app sh -c "*) cat > "$FAKE/link_stdin"; [ -e "$FAKE/link_fails" ] && { echo "No answer from the server, code $(cat "$FAKE/link_stdin")"; exit 1; }; echo linked ;;
      *) exit 0 ;;
    esac ;;
esac
'''  # noqa: E501


@dataclass
class Result:
    code: int
    out: str
    calls: list[str]


@dataclass
class FakePi:
    root: Path
    fake: Path
    proc: Path
    env_vars: dict[str, str] = field(default_factory=dict)

    @property
    def env_path(self) -> Path:
        return self.root / 'raspberry_pi' / '.env'

    @property
    def example(self) -> Path:
        return self.root / 'raspberry_pi' / '.env.example'

    @property
    def cmdline(self) -> Path:
        return self.fake / 'cmdline.txt'

    @property
    def config_txt(self) -> Path:
        return self.fake / 'config.txt'

    def state(self, name: str, value: str = '') -> None:
        (self.fake / name).write_text(value, encoding='utf-8')

    def run(self, *args: str, script: str = 'install.sh') -> Result:
        calls = self.fake / 'calls'
        calls.write_text('', encoding='utf-8')
        done = subprocess.run(['/bin/bash', str(self.root / 'raspberry_pi' / script), *args], capture_output=True,
                              text=True, timeout=30, check=False, env=self.env_vars)
        return Result(done.returncode, done.stdout + done.stderr, calls.read_text(encoding='utf-8').splitlines())

    def values(self) -> dict[str, str]:
        return env_file(self.env_path)

    def change_example(self, name: str, value: str) -> None:
        text = self.example.read_text(encoding='utf-8')
        self.example.write_text(re.sub(rf'^{name}=.*$', f'{name}={value}', text, flags=re.M), encoding='utf-8')


@pytest.fixture
def pi(tmp_path) -> FakePi:
    root, fake, proc = tmp_path / 'desk-talk', tmp_path / 'fake', tmp_path / 'proc'
    (root / 'raspberry_pi').mkdir(parents=True)
    (root / 'config').mkdir()
    (root / 'data').mkdir()
    for name in ('install.sh', 'update.sh', '.env.example', 'settings.pi.yaml', 'model_checksums.txt'):
        if (PI / name).exists():
            shutil.copy(PI / name, root / 'raspberry_pi' / name)
    shutil.copy(ROOT / 'config' / 'settings.yaml', root / 'config' / 'settings.yaml')
    (root / 'data' / 'events.jsonl').write_text('an event\n', encoding='utf-8')
    (root / 'data' / 'cloud.json').write_text('{"token": "keep me"}', encoding='utf-8')

    (fake / 'bin').mkdir(parents=True)
    (fake / 'models').mkdir()
    for name in ('uname', 'getconf', 'df', 'getent', 'id', 'hostname', 'hostnamectl', 'timedatectl', 'systemctl',
                 'git', 'sha256sum', 'curl', 'sudo', 'docker'):
        path = fake / 'bin' / name
        path.write_text(STAND_IN, encoding='utf-8')
        path.chmod(path.stat().st_mode | stat.S_IEXEC)

    (proc / 'asound').mkdir(parents=True)
    (proc / 'devices').write_text('Character devices:\n  1 mem\n 81 video4linux\n116 alsa\n253 dma_heap\n511 media\n\n'
                                  'Block devices:\n  8 media\n', encoding='utf-8')
    (proc / 'meminfo').write_text('MemTotal:        8062000 kB\n', encoding='utf-8')
    (proc / 'cmdline').write_text(f'{BOOT_LINE} {FLAGS}\n', encoding='utf-8')
    (proc / 'asound' / 'cards').write_text(' 0 [vc4hdmi0 ]: vc4-hdmi\n 1 [USB ]: USB-Audio - USB Audio\n',
                                           encoding='utf-8')
    (fake / 'snd').mkdir()

    made = FakePi(root, fake, proc)
    made.state('uname', 'aarch64\n')
    made.state('long_bit', '64\n')
    made.state('free_mb', '90000')
    made.state('uid', '1001\n')
    made.state('groups', 'sam docker video\n')
    made.state('hostname', 'desktalk\n')
    made.state('git_checkout', '1')
    made.state('docker_group_active')
    made.state('pairing-key', 'the key')
    made.state('model_bytes', 'the model')
    made.cmdline.write_text(f'{BOOT_LINE} {FLAGS}\n', encoding='utf-8')
    made.config_txt.write_text('camera_auto_detect=1\n', encoding='utf-8')
    made.change_example('SOUND_DEVICES', str(fake / 'snd'))
    made.change_example('SOUND_CARDS_FILE', str(proc / 'asound' / 'cards'))
    made.change_example('INSTALL_BOOT_CMDLINE', str(made.cmdline))
    made.change_example('INSTALL_BOOT_CONFIG', str(made.config_txt))
    made.env_vars = {'PATH': f'{fake / "bin"}:/usr/bin:/bin', 'FAKE': str(fake), 'FAKE_PYTHON': sys.executable,
                     'DESKTALK_PROC': str(proc), 'HOME': str(tmp_path), 'TMPDIR': str(tmp_path)}
    return made


def model_name() -> str:
    return load_config(root=ROOT, environ={}).pi.detector.model


def files_in(folder: Path) -> dict[str, bytes]:
    return {str(p.relative_to(folder)): p.read_bytes() for p in folder.rglob('*') if p.is_file()}


def docker_calls(result: Result) -> list[str]:
    return [line for line in result.calls if line.startswith('docker ')]


def trust_this_model(pi: FakePi) -> None:
    """The checksum list says what `model_bytes` really hashes to, as a real download would match the real list."""
    digest = hashlib.sha256((pi.fake / 'model_bytes').read_bytes()).hexdigest()
    (pi.root / 'raspberry_pi' / 'model_checksums.txt').write_text(f'{digest}  {model_name()}\n', encoding='utf-8')


# ── Which Pis it refuses ──

def test_refuses_a_32_bit_pi(pi):
    pi.state('uname', 'armv7l\n')
    result = pi.run('--dry-run')
    assert result.code == 1 and '32-bit' in result.out


def test_refuses_a_32_bit_system_on_a_64_bit_processor(pi):
    pi.state('long_bit', '32\n')
    result = pi.run('--dry-run')
    assert result.code == 1 and '32-bit' in result.out


def test_refuses_to_be_run_with_sudo(pi):
    pi.state('uid', '0\n')
    result = pi.run('--dry-run')
    assert result.code == 1 and 'without sudo' in result.out


def test_too_little_free_disk_is_refused_with_the_number_from_the_settings(pi):
    pi.state('free_mb', '100')
    pi.change_example('INSTALL_MIN_DISK_MB', '4321')
    result = pi.run('--dry-run')
    assert result.code == 1 and '4321' in result.out and '100' in result.out


def test_too_little_memory_is_refused(pi):
    (pi.proc / 'meminfo').write_text('MemTotal:   900000 kB\n', encoding='utf-8')
    result = pi.run('--dry-run')
    assert result.code == 1 and 'memory' in result.out


def test_a_small_pi_is_told_about_the_detectors_memory_limit(pi):
    (pi.proc / 'meminfo').write_text('MemTotal:   1950000 kB\n', encoding='utf-8')
    result = pi.run('--dry-run')
    assert result.code == 0 and 'DETECTOR_MEMORY_LIMIT' in result.out


def test_no_sound_cards_file_says_what_is_missing(pi):
    (pi.proc / 'asound' / 'cards').unlink()
    result = pi.run('--dry-run')
    assert result.code == 1 and 'cards' in result.out


def test_no_sound_devices_folder_says_what_is_missing(pi):
    (pi.fake / 'snd').rmdir()
    result = pi.run('--dry-run')
    assert result.code == 1 and 'snd' in result.out


def test_no_usb_speaker_is_not_an_error(pi):
    (pi.proc / 'asound' / 'cards').write_text(' 0 [vc4hdmi0 ]: vc4-hdmi\n', encoding='utf-8')
    result = pi.run()
    assert result.code == 0 and 'plugged in' in result.out


@pytest.mark.parametrize('args', [('--server', SERVER), ('--code', CODE), ('--nonsense',), ('--tz',),
                                  ('--tz', 'Not/AZone'), ('--tz', 'x;rm -rf y')])
def test_wrong_flags_are_refused_before_anything_happens(pi, args):
    result = pi.run(*args)
    assert result.code != 0 and not docker_calls(result) and not pi.env_path.exists()
    assert CODE not in result.out


# ── Dry run ──

def test_a_dry_run_changes_nothing(pi):
    before = files_in(pi.root)
    pi.cmdline.write_text(BOOT_LINE + '\n', encoding='utf-8')
    pi.state('hostname', 'raspberrypi\n')
    (pi.fake / 'docker_group_active').unlink()
    result = pi.run('--dry-run', '--server', SERVER, '--code', CODE)
    assert result.code == 0
    assert files_in(pi.root) == before
    assert not any(line.startswith(('sudo', 'hostnamectl', 'curl')) for line in result.calls)
    assert pi.cmdline.read_text(encoding='utf-8') == BOOT_LINE + '\n'
    assert CODE not in result.out and CODE not in '\n'.join(result.calls)


def test_a_dry_run_says_what_it_finds_and_what_it_would_do(pi):
    pi.cmdline.write_text(BOOT_LINE + '\n', encoding='utf-8')
    pi.state('hostname', 'raspberrypi\n')
    result = pi.run('--dry-run')
    for found in ('CAMERA_MEDIA_MAJOR=511', 'CAMERA_DMA_HEAP_MAJOR=253', 'CAMERA_VIDEO_GROUP=45', 'PI_AUDIO_GROUP=30',
                  'PI_USER_ID=1001', 'PI_GROUP_ID=1002', 'TZ=Europe/Paris', model_name()):
        assert found in result.out
    assert 'Would' in result.out and 'desktalk.local' in result.out and 'restart' in result.out


def test_a_dry_run_takes_the_same_checks_with_docker_not_yet_there(pi):
    pi.state('no_docker')
    result = pi.run('--dry-run')
    assert result.code == 0 and 'Would install Docker' in result.out
    assert not any(line.startswith('docker compose up') for line in result.calls)


def test_the_model_name_in_the_script_is_the_one_the_settings_give(pi):
    settings = pi.root / 'config' / 'settings.yaml'
    text = settings.read_text(encoding='utf-8').replace(f'model: {model_name()}', 'model: another-model.pt')
    settings.write_text(text, encoding='utf-8')
    assert 'another-model.pt' in pi.run('--dry-run').out


def test_settings_pi_yaml_beats_settings_yaml_for_the_model(pi):
    extra = pi.root / 'raspberry_pi' / 'settings.pi.yaml'
    text = extra.read_text(encoding='utf-8').replace('  detector:\n', '  detector:\n    model: pi-only.pt\n')
    extra.write_text(text, encoding='utf-8')
    assert 'pi-only.pt' in pi.run('--dry-run').out


# ── The .env file ──

def test_makes_env_from_the_example_and_fills_in_this_pi(pi):
    assert pi.run().code == 0
    values = pi.values()
    assert values['CAMERA_MEDIA_MAJOR'] == '511' and values['CAMERA_DMA_HEAP_MAJOR'] == '253'
    assert values['CAMERA_VIDEO_GROUP'] == '45' and values['PI_AUDIO_GROUP'] == '30'
    assert values['PI_USER_ID'] == '1001' and values['PI_GROUP_ID'] == '1002'
    assert values['TZ'] == 'Europe/Paris'
    assert set(values) == set(env_file(pi.example))


def test_a_block_device_with_the_same_name_is_not_taken_for_the_camera(pi):
    pi.run()
    assert pi.values()['CAMERA_MEDIA_MAJOR'] != '8'


def test_tz_flag_is_used_and_a_later_run_keeps_it(pi):
    pi.run('--tz', 'Asia/Tokyo')
    assert pi.values()['TZ'] == 'Asia/Tokyo'
    pi.run()
    assert pi.values()['TZ'] == 'Asia/Tokyo'


def test_running_twice_keeps_what_is_in_env_and_adds_a_new_name_once(pi):
    pi.run()
    text = pi.env_path.read_text(encoding='utf-8').replace('DETECTOR_MEMORY_LIMIT=2g', 'DETECTOR_MEMORY_LIMIT=1200m')
    pi.env_path.write_text(text.replace('PI_USER_ID=1001', 'PI_USER_ID=1234'), encoding='utf-8')
    with pi.example.open('a', encoding='utf-8') as example:
        example.write('\n# A new number from a later version.\nBRAND_NEW=7\n')
    pi.run()
    pi.run()
    after = pi.env_path.read_text(encoding='utf-8')
    values = pi.values()
    assert values['DETECTOR_MEMORY_LIMIT'] == '1200m' and values['PI_USER_ID'] == '1234'
    assert values['BRAND_NEW'] == '7' and after.count('BRAND_NEW=') == 1
    assert '# A new number from a later version.' in after
    assert values['CAMERA_MEDIA_MAJOR'] == '511'


def test_a_new_name_this_pi_can_answer_gets_this_pis_value(pi):
    pi.run()
    text = pi.env_path.read_text(encoding='utf-8')
    pi.env_path.write_text(re.sub(r'^PI_AUDIO_GROUP=.*\n', '', text, flags=re.M), encoding='utf-8')
    pi.run()
    assert pi.values()['PI_AUDIO_GROUP'] == '30'


def test_a_major_missing_from_the_pi_keeps_what_is_in_env_and_says_so(pi):
    (pi.proc / 'devices').write_text('Character devices:\n 81 video4linux\n\nBlock devices:\n', encoding='utf-8')
    result = pi.run()
    assert result.code == 0 and 'media' in result.out
    assert pi.values()['CAMERA_MEDIA_MAJOR'] == env_file(pi.example)['CAMERA_MEDIA_MAJOR']


# ── Docker ──

def test_docker_is_installed_only_if_missing(pi):
    assert not any('get.docker' in line for line in pi.run().calls)
    pi.state('no_docker')
    (pi.fake / 'docker_group_active').unlink()
    calls = pi.run().calls
    assert any('get.docker' in line for line in calls)
    assert any(line.startswith('sudo sh ') for line in calls)


def test_the_docker_group_is_added_only_if_missing_and_this_run_uses_sudo(pi):
    assert not any('usermod' in line for line in pi.run().calls)
    pi.state('groups', 'sam video\n')
    (pi.fake / 'docker_group_active').unlink()
    result = pi.run()
    assert result.code == 0 and 'sudo usermod -aG docker sam' in result.calls
    assert 'sudo docker compose up -d --build' in result.calls
    assert 'log' in result.out


def test_starts_the_containers(pi):
    assert 'docker compose up -d --build' in pi.run().calls


def test_a_failed_start_fails_and_says_where_to_look(pi):
    pi.state('up_fails')
    result = pi.run()
    assert result.code == 1 and 'logs' in result.out


# ── Keeping what's there ──

@pytest.mark.parametrize('flags', [(), ('--dry-run',)])
def test_running_twice_keeps_data_and_key(pi, flags):
    before = files_in(pi.root / 'data')
    first, second = pi.run(*flags), pi.run(*flags)
    assert first.code == second.code == 0
    assert files_in(pi.root / 'data') == before
    assert (pi.fake / 'pairing-key').read_text(encoding='utf-8') == 'the key'


@pytest.mark.parametrize('script', ['install.sh', 'update.sh'])
def test_never_removes_volumes(pi, script):
    text = (PI / script).read_text(encoding='utf-8')
    assert '--volumes' not in text and 'volume rm' not in text and 'prune' not in text
    assert not re.search(r'\bdown\b[^\n]* -v\b', text)
    pi.state('old_containers', 'abc\n')
    pi.state('old_volume', 'desktalk-detector_models\n')
    for line in pi.run(script=script).calls:
        assert '--volumes' not in line and 'volume rm' not in line


# ── The memory limits, the camera, restarting ──

def test_memory_flags_are_added_once_and_a_restart_is_asked_for(pi):
    pi.cmdline.write_text(BOOT_LINE + '\n', encoding='utf-8')
    (pi.proc / 'cmdline').write_text(BOOT_LINE + '\n', encoding='utf-8')
    first = pi.run()
    assert first.code == 0 and 'restart' in first.out
    assert pi.cmdline.read_text(encoding='utf-8') == f'{BOOT_LINE} {FLAGS}\n'
    pi.run()
    assert pi.cmdline.read_text(encoding='utf-8') == f'{BOOT_LINE} {FLAGS}\n'
    assert not any('reboot' in line for line in first.calls)


def test_only_the_missing_memory_flag_is_added(pi):
    pi.cmdline.write_text(f'{BOOT_LINE} cgroup_enable=memory\n', encoding='utf-8')
    pi.run()
    assert pi.cmdline.read_text(encoding='utf-8') == f'{BOOT_LINE} cgroup_enable=memory cgroup_memory=1\n'


def test_flags_already_there_and_in_use_mean_no_write_and_no_restart(pi):
    result = pi.run()
    assert not any(pi.cmdline.name in line for line in result.calls if line.startswith('sudo'))
    assert 'restart' not in result.out.lower().replace('restart: unless-stopped', '')


def test_flags_written_but_not_in_use_yet_still_ask_for_a_restart(pi):
    (pi.proc / 'cmdline').write_text(BOOT_LINE + '\n', encoding='utf-8')
    assert 'restart' in pi.run().out


def test_it_restarts_by_itself_only_with_the_flag(pi):
    pi.cmdline.write_text(BOOT_LINE + '\n', encoding='utf-8')
    assert 'sudo reboot' in pi.run('--reboot').calls


def test_the_reboot_flag_does_nothing_when_no_restart_is_needed(pi):
    assert 'sudo reboot' not in pi.run('--reboot').calls


def test_a_camera_already_on_is_left_alone(pi):
    result = pi.run()
    assert pi.config_txt.read_text(encoding='utf-8') == 'camera_auto_detect=1\n'
    assert not any(pi.config_txt.name in line for line in result.calls)


def test_a_camera_switched_off_is_switched_on_once(pi):
    pi.config_txt.write_text('dtparam=audio=on\ncamera_auto_detect=0\n', encoding='utf-8')
    assert 'restart' in pi.run().out
    pi.run()
    assert pi.config_txt.read_text(encoding='utf-8') == 'dtparam=audio=on\ncamera_auto_detect=1\n'


def test_a_camera_named_by_hand_is_left_alone(pi):
    text = 'camera_auto_detect=0\ndtoverlay=imx708\n'
    pi.config_txt.write_text(text, encoding='utf-8')
    pi.run()
    assert pi.config_txt.read_text(encoding='utf-8') == text


def test_the_old_camera_program_outside_docker_gets_a_line(pi):
    pi.state('old_camera_running')
    assert 'desk-talk-camera' in pi.run().out


# ── The name ──

def test_renames_the_pi_and_says_the_new_address_plainly(pi):
    pi.state('hostname', 'raspberrypi\n')
    result = pi.run()
    assert 'sudo hostnamectl set-hostname desktalk' in result.calls
    assert 'http://desktalk.local:5000' in result.out and 'raspberrypi.local' in result.out


def test_a_pi_with_the_right_name_is_not_renamed(pi):
    assert not any('hostnamectl' in line for line in pi.run().calls)


def test_keep_hostname_keeps_it_and_later_runs_remember(pi):
    pi.state('hostname', 'desktalk-pi\n')
    first = pi.run('--keep-hostname')
    assert not any('hostnamectl' in line for line in first.calls) and 'http://desktalk-pi.local:5000' in first.out
    second = pi.run()
    assert not any('hostnamectl' in line for line in second.calls)


def test_the_name_it_gives_is_the_one_the_app_shows():
    name = env_file(PI / '.env.example')['INSTALL_HOSTNAME']
    settings = yaml.safe_load((PI / 'settings.pi.yaml').read_text(encoding='utf-8'))
    assert settings['web']['public_address'] == f"{name}.local:{env_file(PI / '.env.example')['APP_HOST_PORT']}"


# ── The model ──

def test_fetches_the_chosen_model_and_copies_it_in(pi):
    trust_this_model(pi)
    result = pi.run()
    assert result.code == 0
    assert (pi.fake / 'models' / model_name()).read_text(encoding='utf-8') == 'the model'
    assert any(line.startswith('curl ') and model_name() in line for line in result.calls)
    assert any(line.startswith('docker compose cp ') for line in result.calls)
    assert not list((pi.fake / 'models').glob('.*'))


def test_a_model_already_there_is_not_fetched_again(pi):
    (pi.fake / 'models' / model_name()).write_text('old', encoding='utf-8')
    result = pi.run()
    assert not any(line.startswith('curl ') for line in result.calls)
    assert (pi.fake / 'models' / model_name()).read_text(encoding='utf-8') == 'old'


def test_a_failed_download_leaves_the_rest_working_and_says_how_to_retry(pi):
    pi.state('download_fails')
    result = pi.run()
    assert result.code == 0 and 'install.sh' in result.out and model_name() in result.out
    assert 'docker compose up -d --build' in result.calls
    assert not list((pi.fake / 'models').iterdir())


def test_a_download_with_the_wrong_checksum_is_not_used(pi):
    result = pi.run()       # model_bytes isn't the real model, so it doesn't match the real list
    assert result.code == 0 and 'checksum' in result.out.lower()
    assert not list((pi.fake / 'models').iterdir())


def test_a_model_with_no_checksum_listed_is_still_used_and_says_so(pi):
    (pi.root / 'raspberry_pi' / 'model_checksums.txt').write_text('', encoding='utf-8')
    result = pi.run()
    assert (pi.fake / 'models' / model_name()).is_file() and 'checksum' in result.out.lower()


def test_the_chosen_model_has_a_checksum():
    listed = (PI / 'model_checksums.txt').read_text(encoding='utf-8')
    assert re.search(rf'^[0-9a-f]{{64}}  {re.escape(model_name())}$', listed, re.M)


# ── The old setup ──

def test_the_old_detector_project_is_brought_down_and_its_models_carried_over(pi):
    pi.state('old_containers', 'abc\n')
    pi.state('old_volume', 'desktalk-detector_models\n')
    calls = docker_calls(pi.run())
    assert 'docker compose -p desktalk-detector down --remove-orphans' in calls
    carry = next(line for line in calls if 'desktalk-detector_models:/from:ro' in line)
    assert 'cp -an /from/. /models/' in carry
    assert calls.index('docker compose -p desktalk-detector down --remove-orphans') < calls.index(
        'docker compose up -d --build') < calls.index(carry)


def test_no_old_setup_means_nothing_extra(pi):
    calls = docker_calls(pi.run())
    assert not any(' -p desktalk-detector' in line or '/from:ro' in line for line in calls)


def test_a_dry_run_only_says_it_would_bring_the_old_setup_down(pi):
    pi.state('old_containers', 'abc\n')
    pi.state('old_volume', 'desktalk-detector_models\n')
    result = pi.run('--dry-run')
    assert 'desktalk-detector' in result.out and not any(' down' in line for line in result.calls)


# ── Linking to the cloud ──

def test_links_inside_the_app_container_without_showing_the_code(pi):
    pi.state('hostname', 'desktalk\n')
    result = pi.run('--server', SERVER, '--code', CODE)
    assert result.code == 0
    line = next(line for line in result.calls if ' app sh -c ' in line)
    assert 'scripts/link_cloud.py' in line and SERVER in line and 'desktalk' in line and '-u 1001:1002' in line
    assert (pi.fake / 'link_stdin').read_text(encoding='utf-8').strip() == CODE
    assert CODE not in result.out and CODE not in '\n'.join(result.calls)


def test_the_code_stays_out_of_sudos_log_too(pi):
    (pi.fake / 'docker_group_active').unlink()
    result = pi.run('--server', SERVER, '--code', CODE)
    assert any(line.startswith('sudo docker compose exec') for line in result.calls)
    assert CODE not in '\n'.join(result.calls)


def test_a_cloud_server_that_is_down_doesnt_stop_the_install_and_says_so(pi):
    pi.state('link_fails')
    result = pi.run('--server', SERVER, '--code', CODE)
    assert result.code == 0
    assert 'without' in result.out and 'install.sh' in result.out and SERVER in result.out
    assert CODE not in result.out


def test_no_flags_means_no_link(pi):
    assert not any(' app sh -c ' in line for line in pi.run().calls)


def test_the_website_gives_the_install_command():
    words = yaml.safe_load((ROOT / 'cloud' / 'prompts.yaml').read_text(encoding='utf-8'))['site']['setup_command']
    assert 'raspberry_pi/install.sh --server {server_url} --code {code}' in words


# ── update.sh ──

def test_update_fills_in_the_majors_again_and_needs_no_sudo(pi):
    pi.run()
    (pi.proc / 'devices').write_text('Character devices:\n 81 video4linux\n254 dma_heap\n509 media\n\nBlock devices:\n',
                                     encoding='utf-8')
    pi.state('hostname', 'elsewhere\n')
    pi.cmdline.write_text(BOOT_LINE + '\n', encoding='utf-8')
    result = pi.run(script='update.sh')
    assert result.code == 0
    assert pi.values()['CAMERA_MEDIA_MAJOR'] == '509' and pi.values()['CAMERA_DMA_HEAP_MAJOR'] == '254'
    assert not any(line.startswith(('sudo', 'hostnamectl')) for line in result.calls)
    assert 'docker compose up -d --build' in result.calls


def test_update_pulls_in_a_git_checkout(pi):
    pi.state('git_checkout', '0')
    result = pi.run(script='update.sh')
    assert any(line.endswith('pull --ff-only') for line in result.calls)


def test_update_says_so_when_this_is_no_git_checkout(pi):
    result = pi.run(script='update.sh')
    assert result.code == 0 and 'git' in result.out
    assert not any(line.endswith('pull --ff-only') for line in result.calls)


@pytest.mark.parametrize('flags', [('--dry-run',), ('--tz', 'Asia/Tokyo', '--dry-run')])
def test_update_dry_run_pulls_nothing(pi, flags):
    pi.state('git_checkout', '0')
    result = pi.run(*flags, script='update.sh')
    assert result.code == 0 and not any(line.endswith('pull --ff-only') for line in result.calls)
    assert not pi.env_path.exists()


def test_update_without_docker_says_to_install_first(pi):
    pi.state('no_docker')
    result = pi.run(script='update.sh')
    assert result.code == 1 and 'install.sh' in result.out


# ── The repo itself ──

def test_env_is_not_in_git_but_the_example_is():
    tracked = subprocess.run(['git', 'ls-files', 'raspberry_pi/.env', 'raspberry_pi/.env.example'], cwd=ROOT,
                             capture_output=True, text=True, check=True).stdout.split()
    assert tracked == ['raspberry_pi/.env.example']


def test_the_scripts_stop_on_errors_and_can_be_run():
    for name in ('install.sh', 'update.sh'):
        assert 'set -euo pipefail' in (PI / name).read_text(encoding='utf-8')
        assert os.access(PI / name, os.X_OK)
