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
    return [line for line in calls if 'builder prune' in line and '--max-used-space' in line]


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


def test_a_cut_that_fails_doesnt_fail_the_update(pi: FakePi):
    """The new containers are already running by then, so a Docker that won't prune is only said."""
    failing = pi.fake / 'failing'
    failing.mkdir()
    docker = failing / 'docker'
    docker.write_text('#!/bin/bash\ncase "$*" in *"builder prune"*) exit 1 ;; esac\n'
                      'exec "$FAKE/bin/docker" "$@"\n', encoding='utf-8')
    docker.chmod(docker.stat().st_mode | stat.S_IEXEC)
    pi.env_vars['PATH'] = f'{failing}:{pi.env_vars["PATH"]}'
    result = pi.run(script='update.sh')
    assert result.code == 0 and "safe to go on" in result.out


def test_it_never_removes_what_the_containers_use(pi: FakePi):
    text = (PI / 'update.sh').read_text(encoding='utf-8')
    assert 'volume rm' not in text and 'volume prune' not in text and 'system prune' not in text
    assert 'image prune -a' not in text
    for line in pi.run(script='update.sh').calls:
        assert not line.startswith(('docker volume rm', 'docker volume prune', 'docker rmi', 'docker image rm'))
