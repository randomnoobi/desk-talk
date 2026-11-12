"""
On the real Pi: Desk Talk starts by itself when the Pi boots, and the journal, the containers' logs too, stays on the
SD card within its size and age. raspberry_pi/install.sh sets both up, so on a Pi set up before they came in, these fail
until install.sh is run again there; each says so.

The kernel may number the camera's kinds of device differently at any boot. desktalk.service writes this boot's
numbers into .env (device_numbers.sh) before it starts the containers, and the camera checks here say if the camera
container still has the old ones.

    DESKTALK_PI=<host> uv run pytest -m pi tests/pi/test_real_pi_start_on_boot.py -v

The tests of `effective_settings` and `character_device_numbers` at the end need no Pi and run with the others.
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path

import pytest

from tests.pi.conftest import SERVICES, Pi
from tests.unit.test_pi_compose_file import env_file

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / 'raspberry_pi' / '.env.example'
UNITS = ('docker.service', 'containerd.service', 'desktalk.service')
CAMERA_DEVICE_KINDS = ('video4linux', 'media', 'dma_heap')     # the CAMERA_*_MAJOR numbers in .env
RUN_INSTALL = ('Not set up yet: run **on the Pi** `bash ~/desk-talk/raspberry_pi/install.sh` (it asks for sudo once). '
               'This fails until then.')


def effective_settings(text: str) -> dict[str, str]:
    """What journald ends up with, from `systemd-analyze cat-config systemd/journald.conf`, which prints its files in
    the order it reads them: the last value given for a name wins. Lines starting with # are the defaults, unset."""
    found: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(('#', ';', '[')) or '=' not in line:
            continue
        name, value = line.split('=', 1)
        found[name.strip()] = value.strip()
    return found


def character_device_numbers(text: str) -> dict[str, int]:
    """The number of each kind of character device, from /proc/devices. Block devices come after "Block devices:"
    and may use the same names, so reading stops there."""
    numbers: dict[str, int] = {}
    for line in text.splitlines():
        if line.strip() == 'Block devices:':
            break
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit():
            numbers[parts[1]] = int(parts[0])
    return numbers


def pi_values(pi: Pi) -> dict[str, str]:
    """The Pi's own .env, over .env.example here, as install.sh reads them."""
    values = env_file(EXAMPLE)
    for line in pi.in_compose_folder('cat .env 2>/dev/null || true').splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            name, value = line.split('=', 1)
            values[name] = value
    return values


@pytest.mark.pi
def test_docker_and_desk_talk_start_on_boot(pi: Pi):
    answers = pi.run(f'systemctl is-enabled {" ".join(UNITS)} 2>&1', check=False).split()
    assert dict(zip(UNITS, answers, strict=False)) == dict.fromkeys(UNITS, 'enabled'), RUN_INSTALL


@pytest.mark.pi
def test_the_service_runs_as_the_pis_user_in_the_compose_folder_and_worked(pi: Pi):
    shown = dict(line.split('=', 1) for line in pi.run(
        'systemctl show desktalk.service -p User -p WorkingDirectory -p FragmentPath -p ActiveState -p Result',
        check=False).splitlines() if '=' in line)
    assert shown.get('FragmentPath'), RUN_INSTALL
    user = pi.run('id -un').strip()
    assert shown['User'] == user, f"desktalk.service runs as {shown['User'] or 'root'}, not as {user}. {RUN_INSTALL}"
    assert shown['WorkingDirectory'] == pi.in_compose_folder('pwd -P').strip()
    assert 'docker' in pi.run(f'id -nG {shlex.quote(user)}').split(), f'{user} is not in the docker group'
    # Only root may change what runs at boot.
    assert pi.run(f'stat -c "%U %a" {shlex.quote(shown["FragmentPath"])}').strip() == 'root 644'
    assert (shown['ActiveState'], shown['Result']) == ('active', 'success'), (
        'desktalk.service did not start the containers. See why, on the Pi: journalctl -u desktalk.service')


@pytest.mark.pi
def test_the_journal_stays_on_the_card_within_its_size_and_age(pi: Pi):
    values = pi_values(pi)
    settings = effective_settings(pi.run('systemd-analyze cat-config systemd/journald.conf'))
    wanted = {'Storage': 'persistent', 'SystemMaxUse': values['JOURNAL_MAX_USE'],
              'MaxRetentionSec': values['JOURNAL_KEEP'], 'MaxFileSec': values['JOURNAL_FILE_SPAN']}
    assert {name: settings.get(name) for name in wanted} == wanted, RUN_INSTALL
    written = pi.run('test -f /var/log/journal/$(cat /etc/machine-id)/system.journal && echo yes || echo no')
    assert written.strip() == 'yes', ('journald has the settings but still writes to memory: restart it, on the Pi: '
                                      'sudo systemctl restart systemd-journald')


@pytest.mark.pi
@pytest.mark.parametrize('service', SERVICES)
def test_each_container_logs_to_the_journal_under_its_name(pi: Pi, service: str):
    config = pi.run(f'docker inspect -f \'{{{{.HostConfig.LogConfig.Type}}}} '
                    f'{{{{index .HostConfig.LogConfig.Config "tag"}}}}\' {pi.container(service)}').strip()
    assert config == f'journald desktalk-{service}', (
        f'{pi.container(service)} logs with "{config}": it was made before compose.yaml changed. On the Pi, in '
        f'{pi.folder}: docker compose up -d (install.sh and update.sh do it)')
    assert pi.run(f'journalctl -t desktalk-{service} -n 1 -q --no-pager').strip(), (
        f'The journal has no lines from desktalk-{service}. Is your user in the adm group? On the Pi: id -nG')


@pytest.mark.pi
def test_the_camera_container_may_open_the_devices_this_boot_numbered(pi: Pi):
    numbers = character_device_numbers(pi.run('cat /proc/devices'))
    rules = json.loads(pi.run(
        f"docker inspect -f '{{{{json .HostConfig.DeviceCgroupRules}}}}' {pi.container('camera')}")) or []
    wanted = {kind: f'c {numbers[kind]}:* rw' for kind in CAMERA_DEVICE_KINDS if kind in numbers}
    assert set(wanted) == set(CAMERA_DEVICE_KINDS), f'The Pi lists no {set(CAMERA_DEVICE_KINDS) - set(wanted)} devices'
    missing = {kind: rule for kind, rule in wanted.items() if rule not in rules}
    assert not missing, (
        f'The camera container may not open {", ".join(missing)}: it allows {rules}, but at this boot the Pi '
        f'numbered them {wanted}. The kernel numbered them differently at this boot and the container was made '
        'with the old numbers, so the camera gets "Operation not permitted". Fix it: run **on the Pi** '
        '`cd ~/desk-talk/raspberry_pi && bash device_numbers.sh && docker compose up -d`. desktalk.service does '
        'that at each boot once install.sh has been run again there.')


@pytest.mark.pi
def test_the_service_writes_the_device_numbers_before_starting_the_containers(pi: Pi):
    shown = pi.run('systemctl show desktalk.service -p ExecStartPre', check=False)
    assert 'device_numbers.sh' in shown, RUN_INSTALL


# ── No Pi needed ──

def test_the_last_value_read_wins():
    text = ('# /usr/lib/systemd/journald.conf.d/40-rpi-volatile-storage.conf\n[Journal]\nStorage=volatile\n\n'
            '# /etc/systemd/journald.conf.d/60-desktalk.conf\n[Journal]\nStorage=persistent\nSystemMaxUse=200M\n')
    assert effective_settings(text) == {'Storage': 'persistent', 'SystemMaxUse': '200M'}


def test_defaults_shown_as_comments_count_as_unset():
    assert effective_settings('[Journal]\n#Storage=auto\n#MaxFileSec=1month\nForwardToSyslog=yes\n') == {
        'ForwardToSyslog': 'yes'}


PROC_DEVICES = ('Character devices:\n  1 mem\n 81 video4linux\n116 alsa\n237 media\n252 dma_heap\n\n'
                'Block devices:\n  7 loop\n179 mmc\n259 blkext\n510 media\n')


def test_the_camera_kinds_are_read_from_the_character_devices():
    numbers = character_device_numbers(PROC_DEVICES)
    assert {kind: numbers.get(kind) for kind in CAMERA_DEVICE_KINDS} == {
        'video4linux': 81, 'media': 237, 'dma_heap': 252}


def test_a_block_device_with_the_same_name_is_not_taken():
    text = 'Character devices:\n 81 video4linux\n\nBlock devices:\n510 media\n  7 dma_heap\n'
    numbers = character_device_numbers(text)
    assert numbers == {'video4linux': 81}
    assert numbers.get('media') is None
    assert numbers.get('dma_heap') is None
