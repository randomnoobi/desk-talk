"""Every route the two web servers have must have at least one case in cases.py.

So a route that's missed, or added later without a case, fails here instead of slipping through.
"""

from __future__ import annotations

from collections import Counter

from .cases import CASES, SNAPSHOT_KINDS, SSE_MESSAGE_SNAPSHOT
from .shapes import SNAPSHOT_DIR


def covered_routes(server, server_name: str) -> set:
    out = set()
    for c in CASES:
        if c.server == server_name:
            route = server.route_for(c.method, c.path.format(snapshot_id='abc123', story_photo_id='abc123'))
            if route is None:
                assert c.status == 404, f'{c.id}: {c.method} {c.path} matches no route but expects {c.status}'
            else:
                out.add(route)
    return out


def test_every_main_server_route_has_a_case(desks):
    server = desks('fresh').main
    missing = server.routes() - covered_routes(server, 'main')
    assert not missing, 'No case in cases.py for: ' + ', '.join(f'{m} {r}' for m, r in sorted(missing))


def test_every_phone_server_route_has_a_case(desks):
    server = desks('fresh').phone
    missing = server.routes() - covered_routes(server, 'phone')
    assert not missing, 'No case in cases.py for: ' + ', '.join(f'{m} {r}' for m, r in sorted(missing))


def test_case_ids_are_unique():
    repeated = [i for i, n in Counter(c.id for c in CASES).items() if n > 1]
    assert not repeated


def test_every_snapshot_file_belongs_to_a_case():
    wanted = {c.id for c in CASES if c.reply in SNAPSHOT_KINDS} | {SSE_MESSAGE_SNAPSHOT}
    leftover = sorted(p.stem for p in SNAPSHOT_DIR.glob('*.json') if p.stem not in wanted)
    assert not leftover, f'snapshots with no case (delete them?): {leftover}'
