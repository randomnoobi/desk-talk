"""
Reaching the real Pi from the tests in test_real_pi.py, all over SSH with a key.

    DESKTALK_PI=<host> uv run pytest -m pi -v

DESKTALK_PI       what you'd type after `ssh`, like `pi`. Without it every test skips.
DESKTALK_PI_DIR   the folder on the Pi holding compose.yaml (default ~/desk-talk/raspberry_pi).

Asked for by name only: a plain `uv run pytest -m "not slow"` doesn't even collect them.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

DEFAULT_DIR = '~/desk-talk/raspberry_pi'
SERVICES = ('app', 'camera', 'detector')
ON_THE_PI = Path(__file__).with_name('on_the_pi.py')
APP_ADDRESS = 'http://127.0.0.1:5000'
SSH_WAIT_SEC = 30                   # for the first connection
RUN_WAIT_SEC = 120                  # one command on the Pi may take this long, unless it says otherwise
HEALTHY_WAIT_SEC = 360              # after a restart. The detector's check runs every 30 s and a model takes a while
LOOK_EVERY_SEC = 2


class PiCommandFailed(AssertionError):
    """A command on the Pi failed."""


def wait_for[T](what: str, find: Callable[[], T | None], *, timeout: float, every: float = LOOK_EVERY_SEC) -> T:
    """Ask `find` until it answers with anything but None or False. Fails the test, naming `what`, at the time limit."""
    end = time.monotonic() + timeout
    while True:
        found = find()
        if found is not None and found is not False:
            return found
        if time.monotonic() > end:
            raise AssertionError(f'{what}: still not so after {timeout:g} s')
        time.sleep(every)


class Pi:
    def __init__(self, host: str, folder: str):
        self.host = host
        self.folder = folder
        self._cd = f'cd "$HOME"/{shlex.quote(folder[2:])}' if folder.startswith('~/') else f'cd {shlex.quote(folder)}'

    # ── commands ──
    def run(self, command: str, *, timeout: float = RUN_WAIT_SEC, stdin: str | None = None, check: bool = True) -> str:
        ssh = ['ssh', '-o', 'BatchMode=yes', '-o', f'ConnectTimeout={SSH_WAIT_SEC}', self.host, command]
        done = subprocess.run(ssh, capture_output=True, text=True, timeout=timeout, input=stdin, check=False)
        if check and done.returncode:
            raise PiCommandFailed(f'on the Pi: {command}\ncode {done.returncode}\n{done.stdout}\n{done.stderr}')
        return done.stdout

    def compose(self, *args: str, timeout: float = HEALTHY_WAIT_SEC) -> str:
        return self.run(f'{self._cd} && docker compose ' + ' '.join(shlex.quote(a) for a in args), timeout=timeout)

    def in_compose_folder(self, command: str, **kwargs: Any) -> str:
        return self.run(f'{self._cd} && {command}', **kwargs)

    def on_the_pi(self, *args: str, timeout: float = RUN_WAIT_SEC) -> Any:
        """Run tests/pi/on_the_pi.py on the Pi (it travels over SSH, so the Pi's copy doesn't matter)."""
        command = 'python3 - ' + ' '.join(shlex.quote(a) for a in args)
        return json.loads(self.run(command, timeout=timeout, stdin=ON_THE_PI.read_text(encoding='utf-8')))

    # ── the app, over HTTP on the Pi ──
    def get(self, path: str, *, timeout: float = RUN_WAIT_SEC) -> Any:
        """The app's JSON answer, or None if it isn't answering."""
        text = self.run(f'curl -s --max-time 5 {shlex.quote(APP_ADDRESS + path)}', timeout=timeout, check=False)
        try:
            return json.loads(text)
        except ValueError:
            return None

    def content_type(self, path: str) -> str:
        return self.run(f'curl -s --max-time 5 -o /dev/null -w "%{{content_type}}" {shlex.quote(APP_ADDRESS + path)}',
                        check=False)

    def pictures_received(self) -> int | None:
        health = self.get('/api/health')
        return health['camera']['pictures_received'] if health else None

    # ── the containers ──
    @staticmethod
    def container(service: str) -> str:
        return f'desktalk-{service}-1'      # `name: desktalk` in compose.yaml

    def state(self, service: str) -> dict[str, Any]:
        text = self.run(f'docker inspect {self.container(service)}', check=False)
        if not text.strip():
            return {'Running': False, 'Missing': True}
        info = json.loads(text)[0]
        return {**info['State'], 'RestartCount': info['RestartCount'], 'Mounts': info['Mounts']}

    def health(self) -> dict[str, str]:
        found = {}
        for service in SERVICES:
            state = self.state(service)
            found[service] = (state.get('Health') or {}).get('Status', 'not running' if not state['Running'] else '?')
        return found

    def wait_until_healthy(self, timeout: float = HEALTHY_WAIT_SEC) -> None:
        last: dict[str, str] = {}

        def all_healthy() -> bool:
            nonlocal last
            last = self.health()
            return all(status == 'healthy' for status in last.values())
        try:
            wait_for('all three containers healthy', all_healthy, timeout=timeout, every=3)
        except AssertionError as error:
            raise AssertionError(f'{error}. They say: {last}') from None

    def restore_stack(self) -> None:
        """Back to what compose.yaml says, with its own settings: `up -d` starts what's stopped and replaces what a test
        changed."""
        self.compose('up', '-d')
        self.wait_until_healthy()

    # ── what the app runs with ──
    def in_app(self, code: str) -> Any:
        """Run Python in the app's container, with its own code and settings. It prints JSON."""
        return json.loads(self.run(f'docker exec -w /desk-talk {self.container("app")} python -c {shlex.quote(code)}'))

    def settings(self) -> dict[str, Any]:
        return self.in_app('import json; from desktalk.config import load_config; c = load_config(); '
                           'print(json.dumps({"pi": c.pi.model_dump(mode="json"), '
                           '"restore_max_age_sec": c.object_states.restore_max_age_sec, '
                           '"pi_detector_wait": c.lens.wait_before_saying_sec.pi_detector}))')

    def data_folder(self) -> str:
        """Where the app's data folder is on the Pi (DESKTALK_DATA in .env)."""
        for mount in self.state('app')['Mounts']:
            if mount['Destination'] == '/desk-talk/data':
                return mount['Source']
        raise AssertionError("the app's container has no data folder")

    def today_lines(self, name: str) -> list[dict[str, Any]]:
        """Today's lines of one event log file, all that can be read."""
        text = self.run(f'cat {shlex.quote(self.data_folder())}/events/$(date +%F)/{name} 2>/dev/null || true')
        lines = []
        for line in text.splitlines():
            try:
                lines.append(json.loads(line))
            except ValueError:
                continue
        return lines

    def uptime_sec(self) -> float:
        return float(self.run('cut -d" " -f1 /proc/uptime'))

    def started_after_boot_sec(self, service: str) -> float:
        """Seconds after boot this container's first program started. The Pi's clock has no battery and can jump after
        a power cut, so this comes from the kernel's count since boot and not from the time of day."""
        pid = self.state(service)['Pid']
        # The field numbers count from the end of the name in brackets, which can hold spaces. starttime is field 22.
        text = self.run(f'cat /proc/{pid}/stat')
        ticks = int(re.sub(r'^.*\) ', '', text.strip()).split()[19])
        return ticks / int(self.run('getconf CLK_TCK'))

    def app_log(self) -> str:
        return self.run(f'cat {shlex.quote(self.data_folder())}/logs/desktalk.log 2>/dev/null || true')


@pytest.fixture(scope='session')
def pi() -> Pi:
    host = os.environ.get('DESKTALK_PI')
    if not host:
        pytest.skip('Say which Pi: DESKTALK_PI=<what you would type after ssh> uv run pytest -m pi -v')
    the_pi = Pi(host, os.environ.get('DESKTALK_PI_DIR', DEFAULT_DIR))
    try:
        the_pi.in_compose_folder('test -f compose.yaml')
    except PiCommandFailed as error:
        pytest.fail(f'No compose.yaml on {host} in {the_pi.folder}; set DESKTALK_PI_DIR. {error}')
    return the_pi


@pytest.fixture
def stack(pi: Pi) -> Iterator[Pi]:
    """For a test that stops or changes containers: starts from a healthy stack, and whatever the test did, puts it
    back. If that fails too, the test shows an error here saying what's still wrong."""
    pi.wait_until_healthy()
    try:
        yield pi
    finally:
        pi.restore_stack()


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Only `-m pi` runs these. They need a real Pi and take many minutes."""
    if re.search(r'\bpi\b', config.getoption('markexpr') or ''):
        return
    kept = [item for item in items if not item.get_closest_marker('pi')]
    if len(kept) != len(items):
        config.hook.pytest_deselected(items=[item for item in items if item.get_closest_marker('pi')])
        items[:] = kept
