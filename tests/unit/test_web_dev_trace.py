"""The developer page's object trace (desktalk/web/routers/dev.py): GET /api/dev/objects/<label> mixes the object
state changes and the chat's joins and refusals into one timeline, with the pictures kept of it, and
GET /api/dev/objects/photo/<id> is one of those pictures. The router is built here with fakes; the whole app's
characterization case lives in tests/characterization/cases.py."""

from __future__ import annotations

import functools
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from desktalk.config import AppConfig, load_config
from desktalk.domain import ChatJoin, Phase, StateRecord
from desktalk.web.deps import dev_only
from desktalk.web.routers import dev
from tests.contracts.test_trace_photos_contract import JPEG, a_photo
from tests.fakes import FakeChatJoins, FakeObjectStates, FakeTracePhotos, InMemoryStateStore

ROOT = Path(__file__).resolve().parents[2]


@functools.cache
def app_config() -> AppConfig:
    """The real config files, without anyone's settings.local.yaml or .env."""
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        (root / 'config').mkdir()
        for name in ('settings.yaml', 'agents.yaml', 'protocols.yaml', 'prompts.yaml'):
            shutil.copy(ROOT / 'config' / name, root / 'config' / name)
        return load_config(root=root, environ={}, dotenv=root / '.env')


def record(ts: float, change: str, label: str = 'cup', **more: Any) -> StateRecord:
    before, after = {'appeared': (Phase.ABSENT, Phase.PRESENT), 'removed': (Phase.PRESENT, Phase.ABSENT),
                     'moved': (Phase.PRESENT, Phase.PRESENT)}[change]
    return StateRecord(ts=ts, timezone='+00:00', device_id='pi', type=change, label=label, prev_state=before,
                       new_state=after, **more)


def join(kind: str, ts: float, label: str = 'cup', **more: Any) -> ChatJoin:
    values: dict[str, Any] = {'agent': 'cup', 'label': label, 'kind': kind, 'ts': ts, 'confidence': 0.81,
                              'seen_for_sec': 3.2, 'on_desk': True, 'step': 'Normal', **more}
    return ChatJoin(**values)


def client_for(records=(), joins=(), photos: FakeTracePhotos | None = None, *, no_parts: bool = False,
               watched_since: float | None = 1.0) -> TestClient:
    """`watched_since`: when the camera's current run of pictures began (None = no pictures since the app started)."""
    store = InMemoryStateStore()
    store.append(list(records))
    desk = SimpleNamespace(config=app_config(), object_states=FakeObjectStates(watched_since=watched_since),
                           state_store=store, chat_joins=None if no_parts else FakeChatJoins(joins),
                           trace_photos=None if no_parts else (photos or FakeTracePhotos()))
    web = FastAPI()
    web.include_router(dev.router)
    web.dependency_overrides[dev_only] = lambda: None
    web.state.desktalk = desk
    return TestClient(web)


def test_object_trace_mixes_states_and_joins():
    photos = FakeTracePhotos()
    photos.add(a_photo('p1', 100.5), JPEG)
    client = client_for(
        records=[record(100.0, 'appeared', conf_avg=0.8, dwell_sec=40.0), record(101.0, 'appeared', label='pen')],
        joins=[join('joined', 105.0), join('refused', 103.0, on_desk=None, step='Picky'),
               join('joined', 104.0, label='laptop')],
        photos=photos)
    reply = client.get('/api/dev/objects/cup')
    assert reply.status_code == 200
    body = reply.json()
    assert body['label'] == 'cup'
    assert [(row['ts'], row['source'], row['kind']) for row in body['timeline']] == [
        (100.0, 'state', 'appeared'), (103.0, 'chat', 'refused'), (105.0, 'chat', 'joined')]
    appeared, refused, joined = body['timeline']
    assert appeared == {'ts': 100.0, 'source': 'state', 'kind': 'appeared', 'confidence': 0.8,
                        'seen_for_sec': None, 'on_desk': None, 'step': None, 'before': 'ABSENT',
                        'after': 'PRESENT'}
    assert refused['on_desk'] is None and refused['step'] == 'Picky'
    assert joined == {'ts': 105.0, 'source': 'chat', 'kind': 'joined', 'confidence': 0.81, 'seen_for_sec': 3.2,
                      'on_desk': True, 'step': 'Normal', 'before': None, 'after': None}
    assert body['photos'] == [{'id': 'p1', 'ts': 100.5, 'why': 'appeared', 'confidence': 0.82,
                               'box': [10.0, 20.0, 50.0, 80.0], 'size': [640, 480]}]


def test_a_removed_row_says_how_long_it_was_seen():
    client = client_for(records=[record(100.0, 'appeared', dwell_sec=600.0), record(130.0, 'removed', dwell_sec=30.0)])
    rows = client.get('/api/dev/objects/cup').json()['timeline']
    assert [row['seen_for_sec'] for row in rows] == [None, 30.0]


def test_only_the_newest_state_changes_since_the_camera_started():
    kept = app_config().dev.trace_events_kept
    records = [record(1.0, 'appeared')] + [record(100.0 + n, 'moved') for n in range(kept + 5)]
    client = client_for(records=records, watched_since=50.0)
    rows = client.get('/api/dev/objects/cup').json()['timeline']
    assert len(rows) == kept
    assert rows[0]['ts'] == 105.0 and rows[-1]['ts'] == 100.0 + kept + 4


def test_before_the_first_picture_there_are_no_state_changes_from_old_runs():
    """No pictures yet since the app started: the changes in the store are from earlier runs, so none is shown."""
    store_rows = [record(100.0, 'appeared'), record(130.0, 'removed')]
    rows = client_for(records=store_rows, joins=[join('joined', 105.0)], watched_since=None).get(
        '/api/dev/objects/cup').json()['timeline']
    assert [(row['source'], row['kind']) for row in rows] == [('chat', 'joined')]


def test_a_word_with_a_slash_works():
    client = client_for(records=[record(100.0, 'appeared', label='pen/pencil')])
    body = client.get('/api/dev/objects/pen%2Fpencil').json()
    assert body['label'] == 'pen/pencil' and len(body['timeline']) == 1


def test_never_seen_or_no_parts_is_an_empty_trace():
    assert client_for().get('/api/dev/objects/glasses').json() == {'label': 'glasses', 'timeline': [],
                                                                    'photos': []}
    body = client_for(records=[record(100.0, 'appeared')], no_parts=True).get('/api/dev/objects/cup').json()
    assert [row['kind'] for row in body['timeline']] == ['appeared'] and body['photos'] == []


def test_object_photo():
    photos = FakeTracePhotos()
    photos.add(a_photo('p1', 100.0), JPEG)
    reply = client_for(photos=photos).get('/api/dev/objects/photo/p1')
    assert reply.status_code == 200
    assert reply.headers['content-type'] == 'image/jpeg'
    assert reply.content == JPEG


def test_object_photo_404():
    assert client_for().get('/api/dev/objects/photo/gone').status_code == 404
    assert client_for(no_parts=True).get('/api/dev/objects/photo/p1').status_code == 404


def test_the_settings_say_how_many_are_kept():
    body = client_for().get('/api/dev/settings').json()
    assert body['trace_events_kept'] == app_config().dev.trace_events_kept
    assert body['trace_photos_kept'] == app_config().dev.trace_photos_kept
