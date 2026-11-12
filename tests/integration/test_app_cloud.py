"""The cloud sender in the whole app: nothing changes with no link, with the server off or with it failing, and
the status route never shows the token."""

from __future__ import annotations

import logging

import pytest

from desktalk.domain import CloudNotLinked, CloudUnavailable
from desktalk.ports import CloudSending
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
