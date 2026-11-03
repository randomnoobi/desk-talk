"""raspberry_pi/install.sh and update.sh, run for real in a made-up Pi: a copy of the files they read in a temporary
folder, with stand-ins on PATH for everything that would touch the machine (docker, sudo, curl, hostnamectl ...). Each
stand-in writes down how it was called in `calls`, so nothing here can change this computer."""

from __future__ import annotations

import hashlib
import http.server
import importlib.util
import json
import os
import re
import shlex
import shutil
import socket
import stat
import subprocess
import sys
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest
import yaml

from desktalk.config import load_config
from tests.unit.test_pi_compose_file import env_file

ROOT = Path(__file__).resolve().parents[2]
PI = ROOT / 'raspberry_pi'
CODE = 'SECRET-CODE'
TOKEN = 'THE-SECRET-TOKEN'
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
  python3) printf '%s\n' "${DESKTALK_LINK_CODE:-}" > "$FAKE/linker_got"; exec "$FAKE_PYTHON" -S "$@" ;;
  sudo)
    cmd=$1; shift
    case "$cmd" in
      docker) FAKE_SUDO=1 exec "$FAKE/bin/docker" "$@" ;;
      tee|cp|mv) exec "$cmd" "$@" ;;
      sh) rm -f "$FAKE/no_docker"; touch "$FAKE/docker_group_active" ;;
      hostnamectl) exec "$FAKE/bin/hostnamectl" "$@" ;;
      *) : ;;
    esac ;;
  docker)
    [ -n "${DESKTALK_LINK_CODE:-}" ] && touch "$FAKE/docker_saw_the_code"
    [ -e "$FAKE/no_docker" ] && exit 127
    case "$*" in
      "compose version") exit 0 ;;
      "image inspect "*) [ -e "$FAKE/images_built" ] ;;
      "system df "*) cat "$FAKE/docker_holds" 2>/dev/null; exit 0 ;;
      info) [ -e "$FAKE/docker_group_active" ] || [ -n "${FAKE_SUDO:-}" ] ;;
      "ps -aq --filter label=com.docker.compose.project=desktalk-detector") cat "$FAKE/old_containers" 2>/dev/null; exit 0 ;;
      "volume ls -q "*"project=desktalk-detector"*) cat "$FAKE/old_volume" 2>/dev/null; exit 0 ;;
      compose\ *--volumes*|compose\ *down*" -v"*) rm -f "$FAKE/pairing-key" "$FAKE/models/"*; exit 0 ;;
      "compose up -d --build") [ -e "$FAKE/up_fails" ] && exit 1; exit 0 ;;
      "compose exec -T detector test -s "*) [ -s "$FAKE/models/$(basename "$7")" ] ;;
      "compose cp "*) cp "$3" "$FAKE/models/$(basename "${4#*:}")" ;;
      "compose exec -T detector mv "*) mv "$FAKE/models/$(basename "$6")" "$FAKE/models/$(basename "$7")" ;;
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
    root, fake, proc = tmp_path / 'desk talk', tmp_path / 'fake', tmp_path / 'proc'       # a space, on purpose
    (root / 'raspberry_pi').mkdir(parents=True)
    (root / 'config').mkdir()
    (root / 'data').mkdir()
    for name in ('install.sh', 'update.sh', 'link_pi.py', '.env.example', 'settings.pi.yaml', 'model_checksums.txt',
                 'compose.yaml', 'settings.mine.example.yaml'):
        if (PI / name).exists():
            shutil.copy(PI / name, root / 'raspberry_pi' / name)
    shutil.copy(ROOT / 'config' / 'settings.yaml', root / 'config' / 'settings.yaml')
    (root / 'config' / 'prompts.yaml').symlink_to(ROOT / 'config' / 'prompts.yaml')
    for name in ('desktalk', 'shared', 'scripts'):       # what the real linker runs from
        (root / name).symlink_to(ROOT / name)
    (root / 'data' / 'events.jsonl').write_text('an event\n', encoding='utf-8')
    (root / 'data' / 'cloud.json').write_text('{"token": "keep me"}', encoding='utf-8')

    (fake / 'bin').mkdir(parents=True)
    (fake / 'models').mkdir()
    for name in ('uname', 'getconf', 'df', 'getent', 'id', 'hostname', 'hostnamectl', 'timedatectl', 'systemctl',
                 'git', 'sha256sum', 'curl', 'sudo', 'docker', 'python3'):
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


class CloudStandIn(http.server.BaseHTTPRequestHandler):
    """The cloud server's claim route, on this computer only."""

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.server.claims.append(body)  # type: ignore[attr-defined]
        right = body['code'] == CODE
        reply = {'device_id': 'dev-1', 'token': TOKEN} if right else {'detail': f"The code {body['code']} is no good"}
        data = json.dumps(reply).encode('utf-8')
        self.send_response(200 if right else 400)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_args):
        pass


@dataclass
class Cloud:
    url: str
    claims: list[dict]


@pytest.fixture
def cloud() -> Iterator[Cloud]:
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), CloudStandIn)
    server.claims = []  # type: ignore[attr-defined]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield Cloud(f'http://127.0.0.1:{server.server_address[1]}', server.claims)  # type: ignore[attr-defined]
    server.shutdown()
    server.server_close()


def nothing_listening() -> str:
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return f'http://127.0.0.1:{s.getsockname()[1]}'


def model_name() -> str:
    """The model install.sh fetches: settings.pi.yaml's, else config/settings.yaml's (the user's own file aside)."""
    pi = yaml.safe_load((PI / 'settings.pi.yaml').read_text(encoding='utf-8'))
    return pi.get('pi', {}).get('detector', {}).get('model') or load_config(root=ROOT, environ={}).pi.detector.model


def files_in(folder: Path) -> dict[str, bytes]:
    return {str(p.relative_to(folder)): p.read_bytes() for p in folder.rglob('*') if p.is_file()}


def unquoted(text: str) -> str:
    """A printed command with bash's quoting of spaces taken out, to compare it with a path."""
    return text.replace('\\ ', ' ')


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


def test_with_the_images_built_only_the_small_disk_minimum_applies(pi):
    pi.state('images_built')
    pi.change_example('INSTALL_MIN_DISK_MB', '50000')
    pi.change_example('INSTALL_MIN_DISK_BUILT_MB', '2000')
    pi.state('free_mb', '3500')
    assert pi.run().code == 0
    pi.state('free_mb', '1000')
    result = pi.run()
    assert result.code == 1 and '2000' in result.out


def test_the_images_it_asks_about_are_the_ones_compose_builds(pi):
    pi.state('images_built')
    asked = [line.split()[-1] for line in pi.run().calls if line.startswith('docker image inspect')]
    services = yaml.safe_load((PI / 'compose.yaml').read_text(encoding='utf-8'))['services']
    assert sorted(asked) == sorted(service['image'] for service in services.values())


def test_what_docker_already_holds_counts_towards_a_first_build(pi):
    pi.change_example('INSTALL_MIN_DISK_MB', '14000')
    pi.state('free_mb', '11000')
    pi.state('docker_holds', 'Images=1.5GB\nContainers=651.3kB\nLocal Volumes=9GB\nBuild Cache=2.5GB\n')
    result = pi.run()
    assert result.code == 0 and 'docker compose up -d --build' in result.calls


def test_a_refusal_counts_only_images_and_build_steps_and_says_what_to_run(pi):
    pi.change_example('INSTALL_MIN_DISK_MB', '14000')
    pi.state('free_mb', '11000')
    pi.state('docker_holds', 'Images=0B\nContainers=1GB\nLocal Volumes=9GB\nBuild Cache=900MB\n')
    result = pi.run()
    assert result.code == 1 and '14000' in result.out and '900' in result.out
    assert 'docker builder prune' in result.out


def test_a_card_too_small_with_nothing_from_docker_doesnt_talk_about_pruning(pi):
    pi.change_example('INSTALL_MIN_DISK_MB', '14000')
    pi.state('free_mb', '11000')
    result = pi.run()
    assert result.code == 1 and 'prune' not in result.out


def test_update_checks_the_disk_too(pi):
    pi.state('images_built')
    pi.state('free_mb', '10')
    assert pi.run(script='update.sh').code == 1


def test_a_good_build_clears_old_images_and_keeps_the_build_steps_while_there_is_room(pi):
    calls = pi.run().calls
    assert 'docker image prune -f' in calls
    assert calls.index('docker compose up -d --build') < calls.index('docker image prune -f')
    assert not any('builder prune' in line for line in calls)


def test_a_filling_card_gets_its_build_steps_cleared(pi):
    pi.state('images_built')
    pi.change_example('INSTALL_MIN_DISK_BUILT_MB', '100')
    pi.change_example('INSTALL_CLEAR_BUILDS_BELOW_MB', '3000')
    pi.state('free_mb', '2999')
    assert 'docker builder prune -af' in pi.run().calls
    pi.state('free_mb', '3000')
    assert not any('builder prune' in line for line in pi.run().calls)


def test_a_failed_build_clears_nothing(pi):
    pi.state('up_fails')
    pi.state('free_mb', '12500')
    pi.change_example('INSTALL_MIN_DISK_MB', '12000')
    pi.change_example('INSTALL_CLEAR_BUILDS_BELOW_MB', '13000')
    assert not any('prune' in line for line in pi.run().calls)


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


@pytest.mark.parametrize('args', [('--server', 'http://x:1'), ('--code', CODE), ('--nonsense',), ('--tz',),
                                  ('--tz', 'Not/AZone'), ('--tz', 'x;rm -rf y'), ('--tz', 'Europe'),
                                  ('--update', '--server', 'http://x:1', '--code', CODE)])
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
    result = pi.run('--dry-run', '--server', 'http://x:1', '--code', CODE)
    assert result.code == 0
    assert files_in(pi.root) == before
    assert not any(line.startswith(('sudo', 'hostnamectl', 'curl', 'python3')) for line in result.calls)
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
    settings = pi.root / 'raspberry_pi' / 'settings.pi.yaml'
    text = settings.read_text(encoding='utf-8').replace(f'model: {model_name()}', 'model: another-model.pt')
    settings.write_text(text, encoding='utf-8')
    assert 'another-model.pt' in pi.run('--dry-run').out


def test_with_no_model_in_settings_pi_yaml_the_one_in_settings_yaml_is_used(pi):
    extra = pi.root / 'raspberry_pi' / 'settings.pi.yaml'
    extra.write_text(extra.read_text(encoding='utf-8').replace(f'model: {model_name()}', ''), encoding='utf-8')
    settings = pi.root / 'config' / 'settings.yaml'
    old = load_config(root=ROOT, environ={}).pi.detector.model
    settings.write_text(settings.read_text(encoding='utf-8').replace(f'model: {old}', 'model: from-settings.pt'),
                        encoding='utf-8')
    assert 'from-settings.pt' in pi.run('--dry-run').out


def test_settings_pi_yaml_beats_settings_yaml_for_the_model(pi):
    extra = pi.root / 'raspberry_pi' / 'settings.pi.yaml'
    text = extra.read_text(encoding='utf-8').replace('  detector:\n', '  detector:\n    model: pi-only.pt\n')
    extra.write_text(text, encoding='utf-8')
    assert 'pi-only.pt' in pi.run('--dry-run').out


# ── The user's own settings ──

def test_the_users_own_settings_file_is_made_from_the_example_and_left_alone_after(pi):
    mine = pi.root / 'raspberry_pi' / 'mine' / 'settings.mine.yaml'
    assert pi.run().code == 0
    assert mine.read_text(encoding='utf-8') == (PI / 'settings.mine.example.yaml').read_text(encoding='utf-8')
    mine.write_text('llm:\n  ai_url: http://my-laptop.local:11434/v1\n', encoding='utf-8')
    for args in ((), ('--update',)):
        assert pi.run(*args).code == 0
        assert 'my-laptop' in mine.read_text(encoding='utf-8')


def test_update_sh_keeps_the_users_own_settings(pi):
    mine = pi.root / 'raspberry_pi' / 'mine' / 'settings.mine.yaml'
    mine.parent.mkdir()
    mine.write_text('llm:\n  ai_url: http://my-laptop.local:11434/v1\n', encoding='utf-8')
    assert pi.run(script='update.sh').code == 0
    assert 'my-laptop' in mine.read_text(encoding='utf-8')


def test_a_dry_run_makes_no_settings_file(pi):
    result = pi.run('--dry-run')
    assert result.code == 0 and 'settings.mine.yaml' in result.out
    assert not (pi.root / 'raspberry_pi' / 'mine').exists()


def test_the_users_own_settings_beat_settings_pi_yaml_for_the_model(pi):
    mine = pi.root / 'raspberry_pi' / 'mine' / 'settings.mine.yaml'
    mine.parent.mkdir()
    mine.write_text('pi:\n  detector:\n    model: mine-only.pt\n', encoding='utf-8')
    assert 'mine-only.pt' in pi.run('--dry-run').out


# ── The .env file ──

def test_makes_env_from_the_example_and_fills_in_this_pi(pi):
    assert pi.run().code == 0
    values = pi.values()
    assert values['CAMERA_MEDIA_MAJOR'] == '511' and values['CAMERA_DMA_HEAP_MAJOR'] == '253'
    assert values['CAMERA_VIDEO_GROUP'] == '45' and values['PI_AUDIO_GROUP'] == '30'
    assert values['PI_USER_ID'] == '1001' and values['PI_GROUP_ID'] == '1002'
    assert values['TZ'] == 'Europe/Paris'
    assert set(values) == set(env_file(pi.example))


@pytest.mark.parametrize('text', ['', '\n\n'])
def test_an_empty_env_gets_filled_in(pi, text):
    pi.env_path.write_text(text, encoding='utf-8')
    assert pi.run().code == 0
    assert set(pi.values()) == set(env_file(pi.example))
    assert pi.values()['CAMERA_MEDIA_MAJOR'] == '511'


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
    assert '--volumes' not in text and 'volume rm' not in text and 'volume prune' not in text
    assert 'system prune' not in text
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


def test_boot_settings_are_swapped_in_whole_and_a_copy_is_kept(pi):
    pi.cmdline.write_text(BOOT_LINE + '\n', encoding='utf-8')
    calls = pi.run().calls
    assert any(line.startswith('sudo mv ') and line.endswith(str(pi.cmdline)) for line in calls)
    assert not any(line == f'sudo tee {pi.cmdline}' for line in calls)
    assert Path(f'{pi.cmdline}.before-desktalk').read_text(encoding='utf-8') == BOOT_LINE + '\n'
    assert not list(pi.fake.glob('*.new'))


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


def test_a_camera_with_no_line_gets_one_under_all_once(pi):
    pi.config_txt.write_text('dtparam=audio=on\n[cm5]\ndtoverlay=dwc2\n', encoding='utf-8')
    assert 'restart' in pi.run().out
    pi.run()
    assert pi.config_txt.read_text(encoding='utf-8') == ('dtparam=audio=on\n[cm5]\ndtoverlay=dwc2\n'
                                                         '[all]\ncamera_auto_detect=1\n')


def test_a_camera_line_only_for_another_pi_doesnt_count(pi):
    pi.config_txt.write_text('[cm4]\ncamera_auto_detect=1\n', encoding='utf-8')
    pi.run()
    assert pi.config_txt.read_text(encoding='utf-8') == '[cm4]\ncamera_auto_detect=1\n[all]\ncamera_auto_detect=1\n'


@pytest.mark.parametrize('text', ['camera_auto_detect=0\n', 'camera_auto_detect=0\ndtoverlay=imx708\n',
                                  'dtoverlay=imx219,cam0\n', 'dtoverlay=ov5647\n'])
def test_a_camera_set_by_hand_is_left_alone_and_it_says_so(pi, text):
    pi.config_txt.write_text(text, encoding='utf-8')
    result = pi.run()
    assert pi.config_txt.read_text(encoding='utf-8') == text and 'left' in result.out
    assert 'restart' not in result.out.lower()


def test_the_old_camera_program_outside_docker_gets_a_line(pi):
    pi.state('old_camera_running')
    assert 'desk-talk-camera' in pi.run().out


# ── The name ──

def test_renames_a_pi_with_the_stock_name_and_says_the_new_address_plainly(pi):
    pi.state('hostname', 'raspberrypi\n')
    result = pi.run()
    assert 'sudo hostnamectl set-hostname desktalk' in result.calls
    assert 'http://desktalk.local:5000' in result.out and 'raspberrypi.local' in result.out
    assert pi.values()['PUBLIC_NAME'] == 'desktalk.local'


@pytest.mark.parametrize('name', ['desktalk', 'desktalk-pi', 'kitchen'])
def test_a_pi_someone_named_is_not_renamed(pi, name):
    pi.state('hostname', f'{name}\n')
    result = pi.run()
    assert not any('hostnamectl' in line for line in result.calls)
    assert f'http://{name}.local:5000' in result.out
    assert pi.values()['PUBLIC_NAME'] == f'{name}.local'


def test_keep_hostname_keeps_the_stock_name_and_later_runs_remember(pi):
    pi.state('hostname', 'raspberrypi\n')
    first = pi.run('--keep-hostname')
    assert not any('hostnamectl' in line for line in first.calls) and 'http://raspberrypi.local:5000' in first.out
    second = pi.run()
    assert not any('hostnamectl' in line for line in second.calls)
    assert pi.values()['PUBLIC_NAME'] == 'raspberrypi.local'


def test_a_name_with_a_dot_gives_one_local(pi):
    pi.state('hostname', 'kitchen.example.com\n')
    result = pi.run()
    assert pi.values()['PUBLIC_NAME'] == 'kitchen.local' and 'http://kitchen.local:5000' in result.out


def test_the_stock_name_with_a_dot_still_counts_as_the_stock_name(pi):
    pi.state('hostname', 'raspberrypi.lan\n')
    assert 'sudo hostnamectl set-hostname desktalk' in pi.run().calls


def test_the_address_follows_the_port_in_env(pi):
    pi.run()
    text = pi.env_path.read_text(encoding='utf-8')
    pi.env_path.write_text(text.replace('APP_HOST_PORT=5000', 'APP_HOST_PORT=8123'), encoding='utf-8')
    assert 'http://desktalk.local:8123' in pi.run().out


def test_compose_hands_the_app_its_address_from_the_real_name_and_port():
    app = yaml.safe_load((PI / 'compose.yaml').read_text(encoding='utf-8'))['services']['app']
    assert re.fullmatch(r'\$\{PUBLIC_NAME:-[^}]+\}:\$\{APP_HOST_PORT:-\d+\}', app['environment']['PUBLIC_ADDRESS'])
    assert app['environment']['OUTSIDE_PORT'].startswith('${APP_HOST_PORT:-')


def test_the_name_in_the_example_is_the_name_it_gives():
    values = env_file(PI / '.env.example')
    assert values['PUBLIC_NAME'] == f"{values['INSTALL_HOSTNAME']}.local"


# ── The model ──

def test_fetches_the_chosen_model_and_copies_it_in(pi):
    trust_this_model(pi)
    result = pi.run()
    assert result.code == 0
    assert (pi.fake / 'models' / model_name()).read_text(encoding='utf-8') == 'the model'
    fetch = next(line for line in result.calls if line.startswith('curl ') and model_name() in line)
    example = env_file(PI / '.env.example')
    assert f"--max-time {example['INSTALL_DOWNLOAD_MAX_SEC']}" in fetch
    assert f"--speed-limit {example['INSTALL_DOWNLOAD_SLOWEST_BYTES_SEC']}" in fetch
    assert f"--speed-time {example['INSTALL_DOWNLOAD_STALL_SEC']}" in fetch
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
    assert result.code == 0 and model_name() in result.out
    assert shlex.join(['bash', str(pi.root / 'raspberry_pi' / 'install.sh')]).replace("'", '') in unquoted(result.out)
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

def saved_link(pi: FakePi) -> dict:
    return json.loads((pi.root / 'data' / 'cloud.json').read_text(encoding='utf-8'))


@pytest.fixture
def unlinked(pi) -> FakePi:
    (pi.root / 'data' / 'cloud.json').unlink()
    return pi


def test_the_code_is_used_before_docker_and_the_build(unlinked, cloud):
    unlinked.state('no_docker')
    unlinked.env_vars['DESKTALK_LINK_CODE'] = CODE
    calls = unlinked.run('--server', cloud.url).calls
    linker = next(i for i, line in enumerate(calls) if line.startswith('python3 '))
    assert not any(line.startswith(('docker compose up', 'sudo sh')) or 'get.docker' in line for line in calls[:linker])
    assert cloud.claims == [{'code': CODE, 'name': 'desktalk'}]


def test_the_link_is_saved_where_the_app_reads_it_only_for_the_pis_user(unlinked, cloud):
    unlinked.env_vars['DESKTALK_LINK_CODE'] = CODE
    result = unlinked.run('--server', cloud.url)
    assert result.code == 0
    link = saved_link(unlinked)
    assert link['server'] == cloud.url and link['device_id'] == 'dev-1' and link['token'] == TOKEN
    assert link['stopped'] is False and link['recent_ids'] == [] and link['place_ts'] == link['start_ts']
    assert stat.S_IMODE((unlinked.root / 'data' / 'cloud.json').stat().st_mode) == 0o600
    assert load_config(root=ROOT, environ={}).cloud.link_key == 'cloud'
    assert TOKEN not in result.out and 'dev-1' in result.out


def test_linking_the_same_pi_again_keeps_its_place(unlinked, cloud):
    (unlinked.root / 'data' / 'cloud.json').write_text(json.dumps({
        'server': 'http://old:1', 'device_id': 'dev-1', 'token': 'old', 'place_ts': 123.0, 'start_ts': 100.0,
        'recent_ids': ['a']}), encoding='utf-8')
    unlinked.env_vars['DESKTALK_LINK_CODE'] = CODE
    unlinked.run('--server', cloud.url)
    link = saved_link(unlinked)
    assert link['place_ts'] == 123.0 and link['recent_ids'] == ['a'] and link['token'] == TOKEN


def test_the_code_from_the_environment_is_in_no_arguments_and_no_output(unlinked, cloud):
    unlinked.env_vars['DESKTALK_LINK_CODE'] = CODE
    result = unlinked.run('--server', cloud.url)
    assert CODE not in '\n'.join(result.calls) and CODE not in result.out
    assert (unlinked.fake / 'linker_got').read_text(encoding='utf-8').strip() == CODE
    assert not (unlinked.fake / 'docker_saw_the_code').exists()


def test_the_code_flag_for_use_by_hand_still_stays_out_of_the_linkers_arguments(unlinked, cloud):
    result = unlinked.run('--server', cloud.url, '--code', CODE)
    assert result.code == 0 and cloud.claims[0]['code'] == CODE
    assert CODE not in '\n'.join(result.calls)
    assert not (unlinked.fake / 'docker_saw_the_code').exists()


def test_a_refused_code_says_so_finishes_the_install_and_doesnt_exit_0(unlinked, cloud):
    unlinked.env_vars['DESKTALK_LINK_CODE'] = 'WRONG-CODE'
    result = unlinked.run('--server', cloud.url)
    assert result.code == 3
    assert 'NOT linked' in result.out and 'WRONG-CODE' not in result.out
    assert 'docker compose up -d --build' in result.calls
    assert not (unlinked.root / 'data' / 'cloud.json').exists()


def test_a_cloud_server_that_is_down_says_so_and_how_to_try_again(unlinked):
    unlinked.env_vars['DESKTALK_LINK_CODE'] = CODE
    server = nothing_listening()
    result = unlinked.run('--server', server)
    assert result.code == 3 and 'NOT linked' in result.out and CODE not in result.out
    assert 'docker compose up -d --build' in result.calls
    retry = next(line for line in result.out.splitlines() if 'DESKTALK_LINK_CODE=' in line)
    assert unquoted(str(unlinked.root / 'raspberry_pi' / 'install.sh')) in unquoted(retry) and server in retry


def test_a_failed_link_and_no_docker_doesnt_hide_a_failed_start(unlinked):
    unlinked.env_vars['DESKTALK_LINK_CODE'] = CODE
    unlinked.state('up_fails')
    assert unlinked.run('--server', nothing_listening()).code == 1


def test_a_data_folder_it_cant_write_to_doesnt_use_up_the_code(unlinked, cloud):
    data = unlinked.root / 'data'
    data.chmod(0o500)
    try:
        unlinked.env_vars['DESKTALK_LINK_CODE'] = CODE
        result = unlinked.run('--server', cloud.url)
    finally:
        data.chmod(0o700)
    assert result.code == 3 and cloud.claims == []


def test_running_the_same_command_again_after_a_failed_build_stays_linked(unlinked, cloud):
    unlinked.env_vars['DESKTALK_LINK_CODE'] = CODE
    unlinked.state('up_fails')
    assert unlinked.run('--server', cloud.url).code == 1
    (unlinked.fake / 'up_fails').unlink()
    before = saved_link(unlinked)
    result = unlinked.run('--server', cloud.url + '/')
    assert result.code == 0 and 'already linked' in result.out and 'NOT linked' not in result.out
    assert len(cloud.claims) == 1 and saved_link(unlinked) == before


def link_file(pi: FakePi, **changes) -> None:
    link = {'server': 'http://old:1', 'device_id': 'dev-0', 'token': 'old', 'stopped': False, 'place_ts': 1.0,
            'start_ts': 1.0, 'recent_ids': []}
    (pi.root / 'data' / 'cloud.json').write_text(json.dumps({**link, **changes}), encoding='utf-8')


@pytest.mark.parametrize('changes', [{}, {'stopped': True}, {'token': ''}, {'device_id': 5}])
def test_a_link_to_another_server_or_a_dead_one_is_linked_again(unlinked, cloud, changes):
    link_file(unlinked, server=cloud.url if changes else 'http://elsewhere:1', **changes)
    unlinked.env_vars['DESKTALK_LINK_CODE'] = CODE
    assert unlinked.run('--server', cloud.url).code == 0
    assert len(cloud.claims) == 1 and saved_link(unlinked)['token'] == TOKEN


def test_a_data_folder_that_cant_be_made_says_why(unlinked, cloud):
    unlinked.change_example('DESKTALK_DATA', '../data/events.jsonl/inside')
    unlinked.env_vars['DESKTALK_LINK_CODE'] = CODE
    result = unlinked.run('--server', cloud.url)
    assert result.code == 1 and 'events.jsonl/inside' in result.out and cloud.claims == []


def test_no_flags_means_no_link(pi):
    assert not any(line.startswith('python3 ') for line in pi.run().calls)


def link_pi():
    spec = importlib.util.spec_from_file_location('link_pi_script', PI / 'link_pi.py')
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_linker_reads_the_same_settings_and_words_the_app_does():
    config = load_config(root=ROOT, environ={})
    shim = link_pi().settings_for_the_linker(ROOT)
    assert shim.cloud.link_key == config.cloud.link_key
    assert shim.cloud.timeout_sec == config.cloud.timeout_sec
    assert shim.cloud.error_text_chars == config.cloud.error_text_chars
    for name in ('link_saved', 'link_bad_address', 'link_refused_because', 'link_code_refused', 'link_no_answer'):
        assert getattr(shim.prompts.cloud, name) == getattr(config.prompts.cloud, name)


def website_command() -> str:
    return yaml.safe_load((ROOT / 'cloud' / 'prompts.yaml').read_text(encoding='utf-8'))['site']['setup_command']


def test_the_website_gives_the_install_command_with_the_code_out_of_the_arguments():
    words = website_command()
    assert 'DESKTALK_LINK_CODE={code} bash ~/desk-talk/raspberry_pi/install.sh --server {server_url}' in words
    assert '--code' not in words


def run_website_command(tmp_path: Path, pull_status: int, *, clone_made: bool) -> subprocess.CompletedProcess[str]:
    """The website's words, really run in bash, with a stand-in git and a made-up home folder."""
    home, bin_folder = tmp_path / 'home', tmp_path / 'bin'
    home.mkdir()
    bin_folder.mkdir()
    git = bin_folder / 'git'
    git.write_text(f'''#!/bin/bash
echo "git $*" >> "$HOME/git-calls"
case "$*" in *pull*) echo "the reason git gives" >&2; exit {pull_status} ;; esac
mkdir -p "$HOME/desk-talk/.git" "$HOME/desk-talk/raspberry_pi"
echo 'echo "install got code $DESKTALK_LINK_CODE and $*"' > "$HOME/desk-talk/raspberry_pi/install.sh"
''', encoding='utf-8')
    git.chmod(0o755)
    if clone_made:
        (home / 'desk-talk' / '.git').mkdir(parents=True)
    command = website_command().format(server_url='http://x:1', code=CODE)
    return subprocess.run(['/bin/bash', '-c', command], capture_output=True, text=True, timeout=30, check=False,
                          env={'HOME': str(home), 'PATH': f'{bin_folder}:/usr/bin:/bin'})


def test_the_website_command_clones_on_a_clean_card(tmp_path):
    done = run_website_command(tmp_path, 0, clone_made=False)
    assert 'clone' in (tmp_path / 'home' / 'git-calls').read_text(encoding='utf-8')
    assert f'install got code {CODE} and --server http://x:1' in done.stdout
    assert 'cannot change' not in done.stderr


def test_the_website_command_pulls_an_old_clone(tmp_path):
    run_website_command(tmp_path, 0, clone_made=True)
    calls = (tmp_path / 'home' / 'git-calls').read_text(encoding='utf-8')
    assert 'pull --ff-only' in calls and 'clone' not in calls


def test_the_website_command_shows_a_failed_pull_and_says_what_to_do(tmp_path):
    done = run_website_command(tmp_path, 1, clone_made=True)
    assert 'the reason git gives' in done.stderr
    assert 'run this again' in done.stdout + done.stderr


def test_the_website_command_gets_git_if_the_card_has_none():
    assert 'command -v git' in website_command() and 'apt-get install -y git' in website_command()


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
