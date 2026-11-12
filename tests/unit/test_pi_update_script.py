"""raspberry_pi/update.sh keeping Docker's saved build steps under a size, in the same made-up Pi as the install tests
(tests/unit/test_pi_install_script.py): stand-ins on PATH write down every call, so nothing here touches this
computer's Docker."""

from __future__ import annotations

import re
import stat

from tests.unit.test_pi_compose_file import env_file
from tests.unit.test_pi_install_script import PI, FakePi
from tests.unit.test_pi_install_script import pi as made_up_pi

pi = made_up_pi          # the made-up Pi, as a fixture of this file too

SETTING = 'INSTALL_KEEP_BUILD_STEPS_MB'


def prunes(calls: list[str]) -> list[str]:
    return [line for line in calls if 'builder prune' in line
            and ('--max-used-space' in line or '--keep-storage' in line)]


def docker_that(pi: FakePi, case: str) -> None:
    """A docker in front of the made-up Pi's own, doing what `case` (a bash case branch) says first."""
    front = pi.fake / 'front'
    front.mkdir()
    docker = front / 'docker'
    docker.write_text(f'#!/bin/bash\ncase "$*" in {case} esac\nexec "$FAKE/bin/docker" "$@"\n', encoding='utf-8')
    docker.chmod(docker.stat().st_mode | stat.S_IEXEC)
    pi.env_vars['PATH'] = f'{front}:{pi.env_vars["PATH"]}'


# What Docker before 28 says to a flag it doesn't know.
OLD_DOCKER = ('*"--max-used-space"*) printf \'%s\\n\' "docker $*" >> "$FAKE/calls"; '
              'echo "unknown flag: --max-used-space" >&2; exit 125 ;;')


def test_the_size_to_keep_is_in_the_example_with_a_comment():
    lines = (PI / '.env.example').read_text(encoding='utf-8').splitlines()
    [at] = [i for i, line in enumerate(lines) if line.startswith(f'{SETTING}=')]
    assert lines[at - 1].startswith('#')
    # A whole build's steps take about 13 GB: keeping less than that makes every update a whole new build.
    assert int(env_file(PI / '.env.example')[SETTING]) >= 13000


def test_after_a_good_update_the_build_steps_are_cut_down_to_the_size(pi: FakePi):
    pi.change_example(SETTING, '20000')
    result = pi.run(script='update.sh')
    assert result.code == 0
    assert prunes(result.calls) == ['docker builder prune -af --max-used-space 20000mb']
    assert result.calls.index('docker compose up -d --build') < result.calls.index(prunes(result.calls)[0])
    assert '20000 MB' in result.out


def test_the_users_own_env_wins(pi: FakePi):
    pi.run()                                      # install.sh writes the .env, with this setting in it
    text = pi.env_path.read_text(encoding='utf-8')
    assert f'{SETTING}=' in text
    pi.env_path.write_text(re.sub(rf'^{SETTING}=.*$', f'{SETTING}=15000', text, flags=re.M), encoding='utf-8')
    assert prunes(pi.run(script='update.sh').calls) == ['docker builder prune -af --max-used-space 15000mb']


def test_install_alone_doesnt_cut_them(pi: FakePi):
    assert prunes(pi.run().calls) == []


def test_a_failed_build_cuts_nothing(pi: FakePi):
    pi.state('images_built')
    pi.state('up_fails')
    result = pi.run(script='update.sh')
    assert result.code != 0 and prunes(result.calls) == []


def test_a_dry_run_only_says_it_would(pi: FakePi):
    result = pi.run('--dry-run', script='update.sh')
    assert result.code == 0 and prunes(result.calls) == []
    assert 'Would keep' in result.out


def test_an_older_docker_gets_the_flag_it_knows(pi: FakePi):
    """--max-used-space is new in Docker 28; before that, the same thing was --keep-storage."""
    docker_that(pi, OLD_DOCKER)
    result = pi.run(script='update.sh')
    assert result.code == 0
    assert prunes(result.calls)[-1] == 'docker builder prune -af --keep-storage 20000mb'
    assert "weren't cut down" not in result.out


def test_a_cut_that_fails_doesnt_fail_the_update_but_says_how_much_they_take(pi: FakePi):
    """The new containers are already running by then, so a Docker that won't prune is only said, plainly."""
    docker_that(pi, '*"builder prune"*) echo "permission denied" >&2; exit 1 ;;')
    pi.state('docker_holds', 'Images=5.1GB\nBuild Cache=14.2GB\n')
    result = pi.run(script='update.sh')
    assert result.code == 0
    assert "weren't cut down" in result.out and '14.2GB' in result.out
    assert 'docker system df' in result.out and 'permission denied' in result.out
    assert "It's safe to go on" not in result.out


def test_it_never_removes_what_the_containers_use(pi: FakePi):
    text = (PI / 'update.sh').read_text(encoding='utf-8')
    assert 'volume rm' not in text and 'volume prune' not in text and 'system prune' not in text
    assert 'image prune -a' not in text
    for line in pi.run(script='update.sh').calls:
        assert not line.startswith(('docker volume rm', 'docker volume prune', 'docker rmi', 'docker image rm'))


def test_an_update_gives_desk_talk_its_own_room_after_the_build_and_before_the_cut(pi: FakePi):
    result = pi.run(script='update.sh')
    assert result.code == 0, result.out
    calls = result.calls
    assert calls.index('docker compose up -d --build') < calls.index('data_room.sh make') \
        < calls.index('docker compose up -d --no-build') < calls.index(prunes(calls)[0])


def test_a_dry_run_update_passes_the_dry_run_to_the_room(pi: FakePi):
    result = pi.run('--dry-run', script='update.sh')
    assert result.code == 0, result.out
    assert 'data_room.sh make --dry-run' in result.calls and 'data_room.sh make' not in result.calls
