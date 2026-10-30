"""An object's story for the app page (desktalk/web/routers/objects.py): GET /api/objects/<label>/story is what
happened to it since the camera started (and the pictures of it kept from earlier runs), step by step, each step
with its picture when one was taken then, and GET /api/objects/photo/<id> is one of those pictures. The developer
page's trace (/api/dev/objects/<label>) is read by the same code (desktalk/web/stories.py). The router is built
here with fakes; the whole app's characterization cases live in tests/characterization/cases.py."""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from desktalk.web.routers import objects
from tests.contracts.test_trace_photos_contract import JPEG, a_photo
from tests.fakes import FakeChatJoins, FakeObjectStates, FakeTracePhotos, InMemoryStateStore
from tests.unit.test_web_dev_trace import app_config, join, record


def client_for(records=(), joins=(), photos: FakeTracePhotos | None = None, *, no_parts: bool = False,
               watched_since: float | None = 1.0) -> TestClient:
    store = InMemoryStateStore()
    store.append(list(records))
    desk = SimpleNamespace(config=app_config(), object_states=FakeObjectStates(watched_since=watched_since),
                           state_store=store, chat_joins=None if no_parts else FakeChatJoins(joins),
                           trace_photos=None if no_parts else (photos or FakeTracePhotos()))
    web = FastAPI()
    web.include_router(objects.router)
    web.state.desktalk = desk
    return TestClient(web)


def story(client: TestClient, label: str = 'cup') -> dict:
    reply = client.get(f'/api/objects/{label}/story')
    assert reply.status_code == 200, reply.text
    return reply.json()


def test_each_step_has_its_picture_next_to_it():
    photos = FakeTracePhotos()
    photos.add(a_photo('p-appeared', 100.4), JPEG)
    photos.add(a_photo('p-joined', 105.2, why='joined'), JPEG)
    photos.add(a_photo('p-moved', 120.3, why='moved'), JPEG)
    photos.add(a_photo('p-gone', 130.1, why='removed'), JPEG)
    client = client_for(records=[record(100.0, 'appeared', conf_avg=0.8), record(120.0, 'moved'),
                                 record(130.0, 'removed', dwell_sec=30.0)],
                        joins=[join('joined', 105.0)], photos=photos)
    rows = story(client)['timeline']
    assert [(row['kind'], (row['photo'] or {}).get('id')) for row in rows] == [
        ('appeared', 'p-appeared'), ('joined', 'p-joined'), ('moved', 'p-moved'), ('removed', 'p-gone')]
    assert rows[0]['photo'] == {'id': 'p-appeared', 'ts': 100.4, 'why': 'appeared', 'confidence': 0.82,
                                'box': [10.0, 20.0, 50.0, 80.0], 'size': [640, 480]}


def test_the_steps_are_in_plain_words():
    words = app_config().prompts.object_story
    client = client_for(records=[record(100.0, 'appeared'), record(120.0, 'moved'), record(130.0, 'removed')],
                        joins=[join('joined', 105.0), join('left', 131.0), join('refused', 99.0)])
    rows = story(client)['timeline']
    assert [row['what'] for row in rows] == [words.refused, words.appeared, words.joined, words.moved,
                                             words.removed, words.left]
    assert (words.appeared, words.joined, words.moved, words.removed) == (
        'appeared', 'joined the chat', 'moved', 'went away')


def test_a_picture_too_far_from_any_step_is_a_step_of_its_own():
    """A timed picture while it's there, and a step picture whose step isn't in the list (an earlier run)."""
    match_sec = app_config().object_story.photo_match_sec
    photos = FakeTracePhotos()
    photos.add(a_photo('p-every', 200.0, why='every'), JPEG)
    photos.add(a_photo('p-far', 100.0 + match_sec + 1), JPEG)
    client = client_for(records=[record(100.0, 'appeared')], photos=photos)
    rows = story(client)['timeline']
    assert [(row['source'], row['kind'], (row['photo'] or {}).get('id')) for row in rows] == [
        ('state', 'appeared', None), ('photo', 'appeared', 'p-far'), ('photo', 'every', 'p-every')]
    timed = rows[-1]
    assert timed['ts'] == 200.0 and timed['confidence'] == 0.82
    assert timed['what'] == app_config().prompts.object_story.every


def test_one_picture_goes_with_the_nearest_step_only():
    photos = FakeTracePhotos()
    photos.add(a_photo('p1', 103.0), JPEG)
    client = client_for(records=[record(100.0, 'appeared'), record(104.0, 'appeared')], photos=photos)
    rows = story(client)['timeline']
    assert [(row['ts'], (row['photo'] or {}).get('id')) for row in rows] == [(100.0, None), (104.0, 'p1')]


def test_never_seen_is_404_in_plain_words():
    reply = client_for().get('/api/objects/glasses/story')
    assert reply.status_code == 404
    # the whole server writes it as {"error": ...} (the characterization case checks that)
    assert reply.json() == {'detail': app_config().prompts.object_story.never_seen.format(object='glasses')}


def test_a_word_with_a_slash_works():
    body = story(client_for(records=[record(100.0, 'appeared', label='pen/pencil')]), 'pen%2Fpencil')
    assert body['label'] == 'pen/pencil' and len(body['timeline']) == 1


def test_an_object_called_photo_still_has_a_story():
    body = story(client_for(records=[record(100.0, 'appeared', label='photo')]), 'photo')
    assert body['label'] == 'photo'


def test_object_photo():
    photos = FakeTracePhotos()
    photos.add(a_photo('p1', 100.0), JPEG)
    reply = client_for(photos=photos).get('/api/objects/photo/p1')
    assert reply.status_code == 200
    assert reply.headers['content-type'] == 'image/jpeg'
    assert reply.content == JPEG


def test_object_photo_404():
    assert client_for().get('/api/objects/photo/gone').status_code == 404
    assert client_for(no_parts=True).get('/api/objects/photo/p1').status_code == 404


def test_no_story_parts_still_reads_the_state_changes():
    body = story(client_for(records=[record(100.0, 'appeared')], no_parts=True))
    assert [row['kind'] for row in body['timeline']] == ['appeared'] and body['photos'] == []
