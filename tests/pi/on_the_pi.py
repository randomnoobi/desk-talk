"""
The half of the real Pi tests that runs on the Pi itself. Standard library only, on the Pi's own Python.

The tests on the Mac send this file over SSH for the two things that read a lot on the Pi:

    python3 - data --data <folder>                  are the event log and SQLite readable?
    python3 - connections --seconds 90              who do the containers talk to, and how much leaves over Wi-Fi?

A person starts the third by hand, because it has to keep going while the Pi has no network and no SSH
(docs/PI_CHECKS.md says when):

    python3 tests/pi/on_the_pi.py no-network

It notes what it sees in data/pi_checks/no_network.json, and a test reads that file once Wi-Fi is back. `problems()`
and `off_the_pi()` are plain functions, so tests/pi/test_on_the_pi_rules.py can check them with no Pi.
"""

from __future__ import annotations

import argparse
import contextlib
import ipaddress
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent.parent if '__file__' in globals() else Path.cwd()
CONTAINERS = ('desktalk-app-1', 'desktalk-camera-1', 'desktalk-detector-1')
APP_ADDRESS = 'http://127.0.0.1:5000'
APP_PORT = 5000
ASK_WAIT_SEC = 4                    # one question to the app may take this long
RESULTS = Path('pi_checks') / 'no_network.json'     # inside the data folder

# How the no-network run goes
OFFLINE_MINUTES = 6                 # how long it watches with no network: Lens says "nothing on the desk" after 3
WAIT_FOR_OFFLINE_MINUTES = 15       # how long the person has to switch the Wi-Fi off
SAMPLE_EVERY_SEC = 10
COME_BACK_SEC = 120                 # the app restarted on purpose: how long it may take to be healthy again
ENOUGH_GOOD_SAMPLES = 0.8           # of the looks after that time, this share must be all healthy with pictures coming

SAID = 'Said a message out loud'
NO_CARD = 'No sound card with'
COULD_NOT = 'Could not say a message out loud'

MONOTONIC_LINE = re.compile(r'\[\s*([\d.]+)\]')
WIFI_CONNECTED = 'device (wlan0): Activation: successful'
JOURNAL_STARTS_BY_SEC = 120         # NetworkManager starts in seconds. If its first line is later, the rest has gone
NO_EARLIER_JOURNAL = ('no persistent journal', 'No entries', 'No journal')
SHUTDOWN_LINE = re.compile(r'Reached target (shutdown|reboot|poweroff|halt|final)\.target|systemd-shutdown|'
                           r'Journal stopped|Reached target System (Shutdown|Reboot|Power Off|Halt)')
LOG_TIME = re.compile(r'\d{4}-\d\d-\d\d \d\d:\d\d:\d\d')
RESTORED_LINE = re.compile(r'Picked up (\d+) object states from before the restart')
APP_STARTED = 'Picked up '
APP_STOPPING = 'uvicorn.error: Shutting down'


# ── reading a file that may be cut off ──
def read_json_lines(path: Path) -> tuple[list[dict[str, Any]], int]:
    """The lines of a .jsonl file, and how many can't be read."""
    good, broken = [], 0
    with open(path, encoding='utf-8', errors='replace') as lines:
        for line in lines:
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except ValueError:
                broken += 1
                continue
            if isinstance(value, dict):
                good.append(value)
            else:
                broken += 1
    return good, broken


def check_data(data: Path) -> dict[str, Any]:
    """What a look through the data folder finds. Nothing here writes to it."""
    files = {}
    for path in sorted(data.glob('events/*/*.jsonl')):
        good, broken = read_json_lines(path)
        files[str(path.relative_to(data))] = {'lines': len(good), 'broken': broken}
    states: dict[str, Any] = {'readable': False}
    database = data / 'object_states.sqlite'
    if database.exists():
        with contextlib.closing(sqlite3.connect(f'file:{database}?mode=ro', uri=True, timeout=10)) as db:
            rows = db.execute('SELECT object, new_state, ts, type FROM events WHERE type != ? ORDER BY ts, id',
                              ('moved',)).fetchall()
            states = {'readable': True,
                      'integrity': db.execute('PRAGMA integrity_check').fetchone()[0],
                      'rows': [{'object': r[0], 'state': r[1], 'ts': r[2], 'type': r[3]} for r in rows]}
    return {'event_files': files, 'states': states}


# ── who the containers talk to ──
def _address(text: str) -> tuple[str, int]:
    """'0100007F:1F90' from /proc/net/tcp as ('127.0.0.1', 8080). The kernel writes each group of 4 bytes backwards."""
    address, port = text.split(':')
    raw = bytes.fromhex(address)
    swapped = b''.join(raw[i:i + 4][::-1] for i in range(0, len(raw), 4))
    return str(ipaddress.ip_address(swapped)), int(port, 16)


def parse_proc_net(text: str, kind: str) -> list[dict[str, Any]]:
    """The connections in a /proc/net/tcp-style file, leaving out those listening and with no other end."""
    found = []
    for line in text.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 4:
            continue
        local, remote = _address(parts[1]), _address(parts[2])
        if remote[1] == 0 and not kind.startswith('udp'):       # a TCP socket with no other end is only listening
            continue
        found.append({'kind': kind, 'local': local[0], 'local_port': local[1], 'remote': remote[0],
                      'remote_port': remote[1]})
    return found


def _plain_ip(address: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    ip = ipaddress.ip_address(address)
    return ip.ipv4_mapped if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped else ip


def off_the_pi(connections: Iterable[Mapping[str, Any]], docker_network: str, wifi_network: str,
               app_port: int = APP_PORT) -> list[Mapping[str, Any]]:
    """The connections that go off the Pi. Allowed: the container itself, Docker's own network, and a page someone
    on the Wi-Fi opened (it comes in to the app's port; nothing may go out to the Wi-Fi by itself). A UDP socket with
    no other end can send anywhere and doesn't say where, so it counts, unless it's only open to the container itself
    (Docker's name lookup, say)."""
    docker, wifi = ipaddress.ip_network(docker_network), ipaddress.ip_network(wifi_network)
    off = []
    for connection in connections:
        if connection['remote_port'] == 0:
            if not _plain_ip(str(connection['local'])).is_loopback:
                off.append(connection)
            continue
        remote = _plain_ip(str(connection['remote']))
        if remote.is_loopback or remote in docker:
            continue
        if remote in wifi and connection['local_port'] == app_port:
            continue
        off.append(connection)
    return off


def pages_on_the_wifi(connections: Iterable[Mapping[str, Any]], wifi_network: str,
                      app_port: int = APP_PORT) -> list[Mapping[str, Any]]:
    """Someone on the Wi-Fi has the page open."""
    wifi = ipaddress.ip_network(wifi_network)
    return [c for c in connections if c['local_port'] == app_port and _plain_ip(str(c['remote'])) in wifi]


def _wifi_sent_bytes(device: str) -> int:
    return int(Path(f'/sys/class/net/{device}/statistics/tx_bytes').read_text(encoding='utf-8'))


def _wifi() -> tuple[str, str]:
    """The device of the default route and the network it is on, like ('wlan0', '192.168.86.0/24')."""
    routes = subprocess.run(['ip', '-j', '-4', 'route'], capture_output=True, text=True, check=True).stdout
    table = json.loads(routes)
    device = next(route['dev'] for route in table if route['dst'] == 'default')
    return device, next(route['dst'] for route in table if route['dev'] == device and route['dst'] != 'default')


def watch_connections(seconds: float) -> dict[str, Any]:
    device, wifi = _wifi()
    sent_before, started = _wifi_sent_bytes(device), time.monotonic()
    seen: dict[str, dict[str, Any]] = {}
    looks = 0
    while time.monotonic() - started < seconds:
        for container in CONTAINERS:
            for kind in ('tcp', 'tcp6', 'udp', 'udp6'):
                done = subprocess.run(['docker', 'exec', container, 'cat', f'/proc/net/{kind}'], capture_output=True,
                                      text=True, check=False)
                if done.returncode:     # a container may have no IPv6 files
                    continue
                for connection in parse_proc_net(done.stdout, kind):
                    # Ports the container picks itself change every time, so one line per other end is enough
                    key = f"{container} {connection['remote']}:{connection['remote_port']} {connection['local_port']}"
                    seen[key] = {**connection, 'container': container}
        looks += 1
    spent = time.monotonic() - started
    return {'looks': looks, 'seconds': spent, 'connections': list(seen.values()), 'wifi_device': device,
            'wifi_network': wifi, 'wifi_sent_bytes': _wifi_sent_bytes(device) - sent_before}


# ── the run with no network ──
def _ask_app(path: str) -> dict[str, Any] | None:
    try:
        with urllib.request.urlopen(APP_ADDRESS + path, timeout=ASK_WAIT_SEC) as reply:
            return json.load(reply)
    except (OSError, ValueError, urllib.error.URLError):
        return None


def has_network() -> bool:
    """Is there a way off the Pi? Docker's own networks have no default route, so they don't count."""
    for line in Path('/proc/net/route').read_text(encoding='utf-8').splitlines()[1:]:
        parts = line.split()
        if len(parts) > 1 and parts[1] == '00000000':
            return True
    return False


def _healthy() -> dict[str, str]:
    states = {}
    for container in CONTAINERS:
        done = subprocess.run(['docker', 'inspect', '-f', '{{.State.Health.Status}}', container], capture_output=True,
                              text=True, check=False)
        states[container] = done.stdout.strip() or 'missing'
    return states


def _newest_ts(path: Path) -> float | None:
    """The time on the last line of a .jsonl file (it can be huge, so only the end is read)."""
    try:
        with open(path, 'rb') as file:
            file.seek(0, os.SEEK_END)
            file.seek(max(0, file.tell() - 4096))
            lines = file.read().decode('utf-8', 'replace').splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        with contextlib.suppress(ValueError):
            return float(json.loads(line)['ts'])
    return None


def _sample(data: Path, started: float) -> dict[str, Any]:
    now = time.time()
    health = _ask_app('/api/health') or {}
    status = _ask_app('/api/status') or {}
    preferences = _ask_app('/api/preferences') or {}
    today = data / 'events' / time.strftime('%Y-%m-%d')
    return {'at': now, 'after_sec': now - started, 'network': has_network(), 'containers': _healthy(),
            'app_answers': bool(status), 'ai': status.get('llm'), 'quiet_now': preferences.get('quiet_now'),
            'pictures': (health.get('camera') or {}).get('pictures_received'),
            'newest_detection_ts': _newest_ts(today / 'detections.jsonl'),
            'newest_event_ts': _newest_ts(today / 'events.jsonl')}


def _write(path: Path, results: Mapping[str, Any]) -> None:
    """All or nothing: the run may lose its power too."""
    path.parent.mkdir(parents=True, exist_ok=True)
    new = path.with_name(path.name + '.new')
    new.write_text(json.dumps(results, indent=1), encoding='utf-8')
    os.replace(new, path)


def _count_log_lines(log: Path, start: int) -> dict[str, int]:
    try:
        with open(log, 'rb') as file:
            file.seek(0 if os.path.getsize(log) < start else start)
            text = file.read().decode('utf-8', 'replace')
    except OSError:
        return {'said': 0, 'no_card': 0, 'could_not': 0}
    return {'said': text.count(SAID), 'no_card': text.count(NO_CARD), 'could_not': text.count(COULD_NOT)}


def run_no_network(data: Path, out: Path, *, minutes: float, wait_minutes: float, every_sec: float,
                   pretend: bool) -> dict[str, Any]:
    """Wait for the network to go, restart the app once with none there, then watch. Every step is saved at once, so
    a run that gets cut off still says how far it got."""
    print('Started: waiting for the network to go.', flush=True)
    results: dict[str, Any] = {'started_at': time.time(), 'pretend': pretend, 'minutes_wanted': minutes,
                               'finished_at': None, 'never_offline': False, 'network_came_back': False,
                               'app_restart_ok': None, 'samples': [], 'speak': {}}
    _write(out, results)
    give_up = time.monotonic() + wait_minutes * 60
    while has_network() and not pretend:
        if time.monotonic() > give_up:
            results['never_offline'] = True
            results['finished_at'] = time.time()
            _write(out, results)
            return results
        time.sleep(1)
    started = time.time()
    results['offline_at'] = started
    log = data / 'logs' / 'desktalk.log'
    log_start = log.stat().st_size if log.exists() else 0
    # With nothing to say so far today Lens can stay quiet, so a fresh start also gets its "nothing on the desk" line.
    restart = subprocess.run(['docker', 'restart', CONTAINERS[0]], capture_output=True, text=True, check=False)
    results['app_restart_ok'] = restart.returncode == 0
    _write(out, results)
    while time.time() - started < minutes * 60:
        sample = _sample(data, started)
        results['samples'].append(sample)
        results['speak'] = _count_log_lines(log, log_start)
        if sample['network'] and not pretend:
            results['network_came_back'] = True
            _write(out, results)
            break
        _write(out, results)
        time.sleep(every_sec)
    results['speak'] = _count_log_lines(log, log_start)
    results['finished_at'] = time.time()
    _write(out, results)
    return results


def problems(results: Mapping[str, Any], *, come_back_sec: float = COME_BACK_SEC,
             enough: float = ENOUGH_GOOD_SAMPLES) -> list[str]:
    """What's wrong with a no-network run, in words. Nothing means it worked."""
    if results.get('never_offline'):
        return ['the network never went away, so this run shows nothing']
    if not results.get('finished_at'):
        return ['the run never finished: the Pi lost power, or it got stopped']
    found = []
    if results.get('pretend'):
        found.append('this was only a try with --pretend-offline: the network was on')
    if results.get('network_came_back'):
        found.append('the network came back before the run was over')
    if not results.get('app_restart_ok'):
        found.append("the app couldn't be restarted with no network")
    settled = [s for s in results.get('samples', []) if s['after_sec'] >= come_back_sec]
    if len(settled) < 2:        # new pictures can only be told from two looks
        return [*found, 'no looks at the stack after it had time to settle']
    offline = results.get('offline_at', 0)
    good = 0
    for before, after in zip(settled, settled[1:], strict=False):
        pictures = before.get('pictures') or 0
        if (all(state == 'healthy' for state in after['containers'].values()) and after['app_answers']
                and (after.get('pictures') or 0) > pictures):
            good += 1
    if good < enough * (len(settled) - 1):
        found.append(f'only {good} of {len(settled) - 1} looks had everything healthy and new pictures')
    if any(s.get('ai') for s in settled):
        found.append('an AI is set up, and this check is for a Pi with none')
    if not any((s.get('newest_detection_ts') or 0) > offline for s in settled):
        found.append('no pictures were written to the event log while there was no network')
    return found


# ── what the journal and the app's log say about a boot ──
def first_wifi_after_boot(journal: str) -> float | None:
    """Seconds after boot the Wi-Fi first connected, from `journalctl -b -u NetworkManager -o short-monotonic`. None
    when it can't be told: no lines, no such line, or the start of this boot has already gone from the journal."""
    times = [(float(found[1]), line) for line in journal.splitlines() if (found := MONOTONIC_LINE.match(line))]
    if not times or times[0][0] > JOURNAL_STARTS_BY_SEC:
        return None
    return next((when for when, line in times if WIFI_CONNECTED in line), None)


def how_boot_ended(journal: str) -> str | None:
    """'clean' or 'not clean', from `journalctl -b -1`, or None when there's no such journal to read."""
    lines = [line for line in journal.splitlines() if line.strip()]
    if not lines or any(word in lines[0] for word in NO_EARLIER_JOURNAL):
        return None
    return 'clean' if any(SHUTDOWN_LINE.search(line) for line in lines) else 'not clean'


def _log_before_and_after(log: str, started: str) -> tuple[list[str], list[str]]:
    """The app's log lines from before and after `started` ('2026-11-01 10:06:00', the Pi's local time). A line with
    no time on it is left out."""
    before, after = [], []
    for line in log.splitlines():
        found = LOG_TIME.match(line)
        if found:
            (after if found[0] >= started else before).append(line)
    return before, after


def restored_since(log: str, started: str) -> int | None:
    """How many object states the app says it picked up at this start. None if it hasn't said."""
    found = RESTORED_LINE.findall('\n'.join(_log_before_and_after(log, started)[1]))
    return int(found[-1]) if found else None


def app_stopped_cleanly(log: str, started: str) -> bool | None:
    """Did the app's run before `started` get to say goodbye? A reboot lets it, and a pulled plug doesn't. None if
    nothing ran before."""
    before = _log_before_and_after(log, started)[0]
    last_start = next((i for i in range(len(before) - 1, -1, -1) if APP_STARTED in before[i]), None)
    if last_start is None:
        return None
    return any(APP_STOPPING in line for line in before[last_start:])


def speak_result(results: Mapping[str, Any]) -> str:
    """'said', 'no card', 'could not' or 'nothing to say': what the app's log shows about speaking in that run."""
    speak = results.get('speak') or {}
    if speak.get('said'):
        return 'said'
    if speak.get('could_not'):
        return 'could not'
    return 'no card' if speak.get('no_card') else 'nothing to say'


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    data = commands.add_parser('data', help='is the event log and SQLite readable?')
    data.add_argument('--data', type=Path, required=True)
    connections = commands.add_parser('connections', help='who do the containers talk to?')
    connections.add_argument('--seconds', type=float, required=True)
    offline = commands.add_parser('no-network', help='note how the Pi does with no network')
    offline.add_argument('--data', type=Path, default=REPO / 'data', help='the data folder')
    offline.add_argument('--out', type=Path, help='where to write the results (default: pi_checks/ in the data folder)')
    offline.add_argument('--minutes', type=float, default=OFFLINE_MINUTES)
    offline.add_argument('--wait-minutes', type=float, default=WAIT_FOR_OFFLINE_MINUTES)
    offline.add_argument('--every-sec', type=float, default=SAMPLE_EVERY_SEC)
    offline.add_argument('--pretend-offline', action='store_true', help='to try this script with the network on')
    args = parser.parse_args(argv)
    if args.command == 'data':
        print(json.dumps(check_data(args.data)))
    elif args.command == 'connections':
        print(json.dumps(watch_connections(args.seconds)))
    else:
        out = args.out or args.data / RESULTS
        results = run_no_network(args.data, out, minutes=args.minutes, wait_minutes=args.wait_minutes,
                                 every_sec=args.every_sec, pretend=args.pretend_offline)
        print(f'Done. Results are in {out}. What the test will say: {problems(results) or "all good"}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
