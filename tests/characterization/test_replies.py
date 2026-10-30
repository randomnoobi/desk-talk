"""What every web route answers today: status code, content type, a few headers and the JSON shape.

The cases are listed in cases.py; the saved shapes are in snapshots/. See shapes.py for how to record them again.
"""

from __future__ import annotations

import pytest

from .cases import EMPTY, FILE, JSON, STATUS, Case, cases_of
from .client import Reply
from .shapes import check_snapshot, shape_of

# Headers the browser, the Pi or the phone can notice. Others (Date, Content-Length, ETag...) aren't checked.
CHECKED_HEADERS = ('access-control-allow-origin', 'cache-control', 'pragma', 'expires', 'x-accel-buffering')


def send(desk, case: Case) -> Reply:
    """Send the case's `before` requests, then the request itself, to the case's server."""
    for step in case.before:
        r = desk.server(step.server).request(step.method, step.path, json=step.json, data=step.data,
                                             headers=step.headers)
        assert 200 <= r.status < 300, f'{step.method} {step.path} (sent first) failed with {r.status}'
    path = case.path
    if '{snapshot_id}' in path:
        items = desk.main.get('/api/snapshots').json()['items']
        assert items, 'this desk has no photos'
        path = path.format(snapshot_id=items[0]['id'])
    if '{story_photo_id}' in path:
        photos = desk.main.get('/api/objects/cup/story').json()['photos']
        assert photos, 'the cup has no pictures on this desk'
        path = path.format(story_photo_id=photos[0]['id'])
    return desk.server(case.server).request(case.method, path, json=case.json, data=case.data,
                                            headers=case.headers, from_ip=case.from_ip)


def observed(case: Case, reply: Reply) -> dict:
    """What gets compared with the saved snapshot."""
    out = {'request': f'{case.method} {case.path} ({case.server} server, {case.desk} desk)',
           'content_type': reply.content_type,
           'headers': {h: reply.headers[h] for h in CHECKED_HEADERS if h in reply.headers}}
    if case.reply == JSON:
        out['json'] = shape_of(reply.json())
    return out


def value_at(data, dotted: str):
    for part in dotted.split('.'):
        assert isinstance(data, dict) and part in data, f'no "{dotted}" in the reply'
        data = data[part]
    return data


def check_status(case: Case, reply: Reply) -> None:
    assert reply.status == case.status, (f'{case.method} {case.path}: expected status {case.status}, '
                                         f'got {reply.status}: {reply.body[:300]!r}')


@pytest.mark.parametrize('case', cases_of(JSON), ids=lambda c: c.id)
def test_json_reply(case, desks):
    reply = send(desks(case.desk), case)
    check_status(case, reply)
    check_snapshot(case.id, observed(case, reply), case.vary)
    data = reply.json()
    for dotted, want in case.values.items():
        assert value_at(data, dotted) == want, f'{dotted}: expected {want!r}, got {value_at(data, dotted)!r}'


@pytest.mark.parametrize('case', cases_of(FILE), ids=lambda c: c.id)
def test_file_reply(case, desks):
    reply = send(desks(case.desk), case)
    check_status(case, reply)
    check_snapshot(case.id, observed(case, reply))
    assert reply.body, 'the file is empty'
    assert reply.body.startswith(case.starts_with), f'the file starts with {reply.body[:20]!r}'


@pytest.mark.parametrize('case', cases_of(STATUS, EMPTY), ids=lambda c: c.id)
def test_status_reply(case, desks):
    reply = send(desks(case.desk), case)
    check_status(case, reply)
    if case.reply == EMPTY:
        assert reply.body == b''
