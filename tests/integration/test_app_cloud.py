"""The cloud sender in the whole app: nothing changes with no link, with the server off or with it failing, and
the status route never shows the token."""

from __future__ import annotations

import logging

import pytest

from desktalk.domain import CloudHello, CloudLinkRefused, CloudNotLinked, CloudUnavailable, Saving
from desktalk.ports import CloudSending
from desktalk_shared.cloud_api import ClaimReply
from tests.fakes import RecordingCloudLink, a_server_that_is_off
from tests.integration.app_kit import CUP_FAR, LAPTOP, DeskRig
from tests.integration.web_kit import web_client

TOKEN = 'super-secret-token-xyz'
AI_LINE = 'Hey! All good over here on the desk.'


def link_it(rig: DeskRig, **changes) -> None:
    key = rig.config.cloud.link_key
    rig.app.store.save(key, {'server': 'http://cloud.test:8000', 'device_id': 'dev-1', 'token': TOKEN,
                             'place_ts': rig.clock.now(), 'start_ts': rig.clock.now(), 'recent_ids': [],
                             'stopped': False, **changes})


def make_rig(tmp_path, link: RecordingCloudLink | None, **kw) -> DeskRig:
    link = link if link is not None else RecordingCloudLink()
    return DeskRig(tmp_path, ai_reply=AI_LINE, cloud_link_factory=lambda server, token: link, **kw)


def a_normal_day(rig: DeskRig) -> dict:
    """Pictures, a logged sip, a chat message, and the sender's rounds in between; what the app then shows."""
    client = web_client(rig.app)
    rig.watch(4, LAPTOP, CUP_FAR)
    rig.tick(rig.app.cloud, after=1)
    assert client.post('/api/habits/log', json={'habit': 'hydration', 'action': 'sip'}).status_code == 200
    reply = client.post('/api/chat', json={'message': 'hello', 'agent_id': 'computer'})
    rig.tick(rig.app.cloud, after=60)
    day = rig.clock.local_day(rig.clock.now())
    return {'chat_status': reply.status_code, 'sips': rig.app.habits.today(rig.clock.now())['hydration']['sips'],
            'event_types': sorted({e['type'] for e in rig.app.event_log.events(day, None, None)})}


def test_the_sender_is_a_background_job(tmp_path):
    rig = make_rig(tmp_path, None)
    assert isinstance(rig.app.cloud, CloudSending)
    assert rig.app.cloud in [job for job, _ in rig.app.background_jobs()]
    assert rig.app.cloud.name == 'cloud uploader'


def test_nothing_is_called_until_linked(tmp_path):
    link = RecordingCloudLink()
    made: list[tuple[str, str]] = []
    rig = DeskRig(tmp_path, cloud_link_factory=lambda server, token: made.append((server, token)) or link)
    rig.watch(3, LAPTOP)
    wait = rig.tick(rig.app.cloud, after=100)
    assert wait == rig.config.cloud.idle_check_every_sec and made == [] and link.calls == []


@pytest.mark.parametrize('failure', [CloudUnavailable('the cloud server is off'), CloudNotLinked('401'),
                                     RuntimeError('a bug in the link')],
                         ids=['server off', 'server forgot us', 'a bug'])
def test_server_off_changes_nothing(tmp_path, failure):
    """Chat, habits and the event log behave as they do with no link at all, whatever the server does."""
    plain = make_rig(tmp_path / 'plain', None)
    broken = make_rig(tmp_path / 'broken', RecordingCloudLink(failure=failure))
    link_it(broken)
    assert a_normal_day_without_the_sender_failing(broken) == a_normal_day_without_the_sender_failing(plain)


def a_normal_day_without_the_sender_failing(rig: DeskRig) -> dict:
    """a_normal_day, with the sender's rounds run the way the supervisor runs them: a round that raises is caught
    and logged, and the rest of the app doesn't notice."""
    original = rig.tick

    def guarded(job, after=0.0):
        try:
            return original(job, after)
        except RuntimeError:
            return 0.0
    rig.tick = guarded
    return a_normal_day(rig)


def test_a_server_that_is_off_does_not_stop_events_reaching_the_log(tmp_path):
    rig = make_rig(tmp_path, a_server_that_is_off())
    link_it(rig)
    rig.watch(4, LAPTOP, CUP_FAR)
    rig.app.habits.log('hydration', 'sip')
    wait = rig.tick(rig.app.cloud, after=10)
    cloud = rig.config.cloud
    assert cloud.retry_start_sec * (1 - cloud.retry_jitter) <= wait <= cloud.retry_start_sec     # with its jitter
    day = rig.clock.local_day(rig.clock.now())
    assert [e['action'] for e in rig.app.event_log.events(day, {'habit'}, None)] == ['sip']
    assert rig.app.cloud.status()['waiting'] >= 1           # the sip, and what the camera saw appear


def test_events_reach_the_server_without_pictures_or_text(tmp_path):
    link = RecordingCloudLink()
    rig = make_rig(tmp_path, link)
    link_it(rig)
    rig.watch(4, LAPTOP, CUP_FAR)
    rig.clock.advance(5)
    rig.app.habits.log('hydration', 'sip')
    rig.tick(rig.app.cloud, after=5)
    sent = [e for events, _ in link.calls for e in events]
    assert {e['type'] for e in sent} >= {'habit', 'goals'}
    assert all('say' not in e and 'text' not in e for e in sent)
    [goals] = [e for e in sent if e['type'] == 'goals']
    assert goals['goals']['water_ml'] == rig.config.protocols.hydration.daily_goal_ml
    assert goals['wake'] == rig.app.preferences.get('wake_time')


def test_goals_come_from_the_habit_settings(tmp_path):
    link = RecordingCloudLink()
    rig = make_rig(tmp_path, link)
    link_it(rig)
    rig.tick(rig.app.cloud, after=5)
    [goals] = [e for events, _ in link.calls for e in events if e['type'] == 'goals']
    protocols = rig.config.protocols
    assert goals['goals'] == {
        'water_ml': protocols.hydration.daily_goal_ml, 'water_window_hours': protocols.hydration.goal_window_hours,
        'sit_limit_min': protocols.breaks.sit_limit_min, 'break_min': protocols.breaks.break_min,
        'phone_free_min': protocols.phone_free.goal_min_per_day,
        'phone_away_before_bed_min': protocols.phone_free.before_bed_min,
        'morning_light_window_min': protocols.light.morning_window_min,
        'evening_dim_before_bed_min': protocols.light.evening_dim_before_bed_min,
        'screens_off_before_bed_min': protocols.light.screens_off_before_bed_min}


# ── the status route ──
def test_status_route_with_no_link(tmp_path):
    rig = make_rig(tmp_path, None)
    reply = web_client(rig.app).get('/api/cloud/status')
    assert reply.status_code == 200
    assert reply.json() == {'linked': False, 'stopped': False, 'last_sent_ts': None, 'waiting': 0}


def test_status_route_never_shows_the_token(tmp_path, caplog):
    rig = make_rig(tmp_path, RecordingCloudLink(failure=CloudUnavailable('off')))
    link_it(rig)
    client = web_client(rig.app)
    rig.app.habits.log('hydration', 'sip')
    with caplog.at_level(logging.DEBUG):
        rig.tick(rig.app.cloud, after=10)
        for _ in range(3):
            reply = client.get('/api/cloud/status')
            assert reply.status_code == 200
            assert TOKEN not in reply.text and 'cloud.test' not in reply.text and 'dev-1' not in reply.text
            assert set(reply.json()) == {'linked', 'stopped', 'last_sent_ts', 'waiting'}
            rig.tick(rig.app.cloud, after=400)
    assert TOKEN not in caplog.text


def test_status_route_after_the_server_forgot_us(tmp_path):
    rig = make_rig(tmp_path, RecordingCloudLink(failure=CloudNotLinked('401')))
    link_it(rig)
    rig.tick(rig.app.cloud, after=10)
    body = web_client(rig.app).get('/api/cloud/status').json()
    assert body['linked'] is True and body['stopped'] is True


def test_the_settings_page_has_the_words_for_the_status_line(tmp_path):
    rig = make_rig(tmp_path, None)
    cloud = web_client(rig.app).get('/api/ui_settings').json()['cloud']
    words = rig.config.prompts.cloud
    assert cloud == {'off': words.status_off, 'on': words.status_on, 'waiting': words.status_waiting,
                     'stopped': words.status_stopped}


def test_the_developer_pages_storage_tab_never_shows_the_link_file(tmp_path):
    rig = make_rig(tmp_path, None)
    link_it(rig)
    assert rig.app.data_files is not None
    rig.app.store.save('something_else', {})
    name = f'{rig.config.cloud.link_key}.json'
    listed = [f.path for f in rig.app.data_files.list()]
    assert 'something_else.json' in listed and name not in listed          # the folder is listed, but not the link
    with pytest.raises(FileNotFoundError):
        rig.app.data_files.preview(name)
    client = web_client(rig.app)
    assert TOKEN not in client.get('/api/dev/files').text
    assert TOKEN not in client.get('/api/dev/files/preview', params={'path': name}).text


# ── the cloud website finding this Pi and linking it ──
CODE = 'ABCD-EFGH'
HOME = '192.168.86.30'              # the computer running the cloud server, on the home network
OFFER = {'server': f'http://{HOME}:8080', 'code': CODE}


class Claims:
    """Stands in for the cloud server's claim route."""

    def __init__(self):
        self.calls: list[tuple[str, str, str]] = []

    def __call__(self, server: str, code: str, name: str, *, timeout_sec: float, error_text_chars: int) -> ClaimReply:
        self.calls.append((server, code, name))
        return ClaimReply(device_id='dev-1', token=TOKEN)


class Refusing:
    """A linker that says no with one `kind`."""

    def __init__(self, kind: str):
        self.kind = kind

    def hello(self) -> CloudHello:
        return CloudHello(name='desktalk', linked=False)

    def link(self, server: str, code: str, caller: str) -> None:
        raise CloudLinkRefused(self.kind, f'no ({self.kind})')


def test_hello_route(tmp_path):
    rig = make_rig(tmp_path, None)
    reply = web_client(rig.app, from_ip=HOME).get('/api/cloud/hello')
    assert reply.status_code == 200
    assert reply.json() == {'desk_talk': 'pi', 'name': rig.app.cloud_linker.hello().name, 'linked': False}
    link_it(rig)
    assert web_client(rig.app, from_ip=HOME).get('/api/cloud/hello').json()['linked'] is True


def test_hello_never_shows_the_link(tmp_path):
    rig = make_rig(tmp_path, None)
    link_it(rig)
    reply = web_client(rig.app, from_ip=HOME).get('/api/cloud/hello')
    assert set(reply.json()) == {'desk_talk', 'name', 'linked'}
    assert TOKEN not in reply.text and 'cloud.test' not in reply.text and 'dev-1' not in reply.text


def test_link_route_links_and_speaks(tmp_path):
    claims = Claims()
    rig = make_rig(tmp_path, None, cloud_claim=claims)
    reply = web_client(rig.app, from_ip=HOME).post('/api/cloud/link', json=OFFER)
    assert reply.status_code == 200 and reply.json() == {'linked': True}
    assert [call[:2] for call in claims.calls] == [(OFFER['server'], CODE)]
    assert rig.app.store.load(rig.config.cloud.link_key)['token'] == TOKEN
    words = rig.config.prompts.cloud
    assert (words.offer_linked_title, words.offer_linked_said) in rig.desktop.shown
    assert rig.app.cloud.status()['linked'] is True


@pytest.mark.parametrize('kind, status', [('bad_request', 400), ('refused', 400), ('not_caller', 403),
                                          ('already_linked', 409), ('busy', 409), ('not_saved', 409),
                                          ('unavailable', 502)])
def test_link_route_maps_each_refusal_to_its_status(tmp_path, kind, status):
    rig = make_rig(tmp_path, None)
    rig.app.cloud_linker = Refusing(kind)
    reply = web_client(rig.app, from_ip=HOME).post('/api/cloud/link', json=OFFER)
    assert reply.status_code == status and reply.json() == {'error': f'no ({kind})'}


@pytest.mark.parametrize('body', [{}, {'server': OFFER['server']}, {'code': CODE}, {'server': 5, 'code': CODE},
                                  {'server': OFFER['server'], 'code': 1234}, ['a list']])
def test_link_route_refuses_a_body_of_the_wrong_shape(tmp_path, body):
    claims = Claims()
    rig = make_rig(tmp_path, None, cloud_claim=claims)
    reply = web_client(rig.app, from_ip=HOME).post('/api/cloud/link', json=body)
    assert reply.status_code == 400 and 'error' in reply.json() and CODE not in reply.text
    assert claims.calls == []


def test_link_route_uses_the_socket_peer_not_forwarded_for(tmp_path):
    claims = Claims()
    rig = make_rig(tmp_path, None, cloud_claim=claims)
    client = web_client(rig.app, from_ip='192.168.86.99')
    reply = client.post('/api/cloud/link', json=OFFER, headers={'X-Forwarded-For': HOME, 'X-Real-IP': HOME})
    assert reply.status_code == 403 and claims.calls == []
    assert reply.json() == {'error': rig.config.prompts.cloud.offer_not_caller}


def test_link_route_takes_a_post_with_no_origin(tmp_path):
    """The cloud server is a program, not a web page: it sends no Origin, so OwnPagesOnly lets it through. And it
    calls the Pi by its number, which KnownNamesOnly lets through."""
    claims = Claims()
    rig = make_rig(tmp_path, None, cloud_claim=claims)
    pi = f'http://192.168.86.225:{rig.config.web.port}'
    reply = web_client(rig.app, from_ip=HOME).post(f'{pi}/api/cloud/link', json=OFFER)
    assert 'origin' not in {name.lower() for name in reply.request.headers}
    assert reply.status_code == 200 and len(claims.calls) == 1


def test_link_route_refuses_while_linked(tmp_path):
    claims = Claims()
    rig = make_rig(tmp_path, None, cloud_claim=claims)
    link_it(rig)
    reply = web_client(rig.app, from_ip=HOME).post('/api/cloud/link', json=OFFER)
    assert reply.status_code == 409 and claims.calls == []
    assert rig.app.store.load(rig.config.cloud.link_key)['server'] == 'http://cloud.test:8000'


def test_link_route_with_saving_off_says_no(tmp_path):
    claims = Claims()
    rig = make_rig(tmp_path, None, cloud_claim=claims, saving=Saving.OFF)
    reply = web_client(rig.app, from_ip=HOME).post('/api/cloud/link', json=OFFER)
    assert reply.status_code == 409 and claims.calls == []
    assert reply.json() == {'error': rig.config.prompts.cloud.offer_not_saved}


@pytest.mark.parametrize('damage', [{'place_ts': 1e300}, {'recent_ids': 'oops'}, {'server': 'ftp://x'}])
def test_a_damaged_link_is_not_linked_to_the_sender_hello_or_a_link_offer(tmp_path, damage):
    """One check of the saved link for the whole app: the sender, the hello and a link offer all agree."""
    claims = Claims()
    rig = make_rig(tmp_path, None, cloud_claim=claims)
    link_it(rig, **damage)
    client = web_client(rig.app, from_ip=HOME)
    assert rig.app.cloud.status()['linked'] is False
    assert client.get('/api/cloud/hello').json()['linked'] is False
    assert client.post('/api/cloud/link', json=OFFER).status_code == 200 and len(claims.calls) == 1
    assert rig.app.cloud.status()['linked'] is True and client.get('/api/cloud/hello').json()['linked'] is True
