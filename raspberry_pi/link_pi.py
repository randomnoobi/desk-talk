#!/usr/bin/env python3
"""
Link this Pi to a cloud server on the Pi's own Python, so install.sh can use the one-time code at once: the code runs
out in minutes, and Docker's install and the first build take far longer. So this needs nothing installed.

    DESKTALK_LINK_CODE=ABCD-EFGH python3 raspberry_pi/link_pi.py --data data --server http://192.168.1.9:8080

It is scripts/link_cloud.py's `run`, with what that gets from the app's settings read here without pydantic or a YAML
reader. The app reads the file it saves in the data folder, every round, so no restart is needed. Only the Pi's own
user may read that file: it holds the token. (tests/unit/test_pi_install_script.py runs this with no packages.)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / 'shared')]

_LINE = re.compile(r' {2}(\w+): (.*)')
SUFFIX = '.json'


def _section(path: Path, name: str) -> dict[str, str]:
    """The plain `name: value` lines right under a top-level section, as text, without quotes or a comment."""
    found: dict[str, str] = {}
    inside = False
    for line in path.read_text(encoding='utf-8').splitlines():
        if line and not line[0].isspace() and not line.startswith('#'):
            inside = line.split('#')[0].strip() == f'{name}:'
        match = _LINE.fullmatch(line) if inside else None
        if match:
            value = match[2].strip()
            if value[:1] in ('"', "'"):
                found[match[1]] = value[1:value.rindex(value[0])]
            else:
                found[match[1]] = value.partition(' #')[0].strip()
    return found


def settings_for_the_linker(root: Path) -> Any:
    """What scripts/link_cloud.py's `run` reads from the app's config, from the same two files."""
    cloud = _section(root / 'config' / 'settings.yaml', 'cloud')
    return SimpleNamespace(
        prompts=SimpleNamespace(cloud=SimpleNamespace(**_section(root / 'config' / 'prompts.yaml', 'cloud'))),
        cloud=SimpleNamespace(link_key=cloud['link_key'], timeout_sec=float(cloud['timeout_sec']),
                              error_text_chars=int(cloud['error_text_chars'])))


class LinkFile:
    """The two things `run` asks of a store, in the app's JsonFileStore format. (That class can't be imported here:
    its package needs the app's packages.) Saves are all-or-nothing, and only this user may read the file."""

    def __init__(self, folder: Path):
        self.folder = folder

    def load(self, key: str) -> Any:
        try:
            return json.loads((self.folder / f'{key}{SUFFIX}').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return None

    def save(self, key: str, value: Any) -> None:
        handle, new = tempfile.mkstemp(dir=self.folder, prefix=f'.{key}.', suffix='.tmp')      # mode 0600
        try:
            with os.fdopen(handle, 'w', encoding='utf-8') as file:
                json.dump(value, file, indent=2)
                file.flush()
                os.fsync(file.fileno())
            os.replace(new, self.folder / f'{key}{SUFFIX}')
        except BaseException:
            Path(new).unlink(missing_ok=True)
            raise


def main() -> int:
    from desktalk.adapters.cloud import claim
    from desktalk_shared.clock import SystemClock
    from scripts.link_cloud import run

    parser = argparse.ArgumentParser(description='Link this Pi to a cloud server, with no packages installed.')
    parser.add_argument('--data', required=True, type=Path, help="the app's data folder on the Pi")
    args, rest = parser.parse_known_args()
    # Checked first: the code works once, so nothing may fail after it is used.
    try:
        args.data.mkdir(parents=True, exist_ok=True)
        tempfile.TemporaryFile(dir=args.data).close()
    except OSError as exc:
        sys.stdout.write(f"Can't save the link in {args.data} ({exc.strerror}), so the code wasn't used.\n")
        return 1
    return run(rest, config=settings_for_the_linker(ROOT), store=LinkFile(args.data), clock=SystemClock(),
               claim=claim, say=print)


if __name__ == '__main__':
    sys.exit(main())
