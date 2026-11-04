"""
The event log, live and photo routes send only what the app page shows (desktalk/web/routers/log.py, live.py,
video.py, settings.py), on the whole app built by bootstrap with a FakeClock.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from desktalk.domain import (
    AgentJoined,
    Alert,
    BBox,
    CameraNotice,
    Compaction,
    Detection,
    DetectionEvent,
    HabitEvent,
    Light,
    NudgeSent,
    ObjectStateChanged,
    RuleOutcome,
)
from tests.integration.app_kit import CUP_FAR, LAPTOP, DeskRig, changed, desk_config
from tests.integration.web_kit import web_client

SHOWN = ('habit', 'nudge', 'alert', 'camera')


def sip(ts: float, action: str = 'sip', habit: str = 'hydration') -> HabitEvent:
    return HabitEvent(ts=ts, habit=habit, action=action, say='Nice sip!')


def noise(ts: float) -> list:
    """Events the page never shows."""
    return [
        AgentJoined(ts=ts, agent='cup', name='Cup', label='cup', conf=0.9),
        DetectionEvent(ts=ts, frame_id='f1', source='pi', size=(640, 480), detections=(), person=True,
                       light=Light(brightness=100)),
        ObjectStateChanged(ts=ts, change='appeared', label='cup', prev_state='UNKNOWN', new_state='PRESENT',
                           device_id='pi'),
        RuleOutcome(ts=ts, rule='hydration.sip', habit='hydration', result='worked', after_sec=60),
        Compaction(ts=ts, frames=3, intervals=1),
    ]


def log_events(rig: DeskRig, query: str = '') -> list[dict]:
    reply = web_client(rig.app).get(f'/api/log/events{query}')
    assert reply.status_code == 200, reply.text
    return reply.json()['events']


# ── which events ─────────────────────────────────────────────────────
def test_log_events_only_shown_types(tmp_path):
    rig = DeskRig(tmp_path)
    now = rig.clock.now()
    for event in noise(now):
        rig.app.event_log.write(event)
    rig.app.event_log.write(sip(now + 1))
    assert [(e['type'], e['habit'], e['action']) for e in log_events(rig)] == [('habit', 'hydration', 'sip')]


def test_log_events_has_the_four_shown_types_newest_first(tmp_path):
    rig = DeskRig(tmp_path)
    now = rig.clock.now()
    log = rig.app.event_log
    log.write(sip(now))
    log.write(NudgeSent(ts=now + 1, habit='hydration', rule='hydration.sip', agent='cup', evidence='', text='Drink!'))
    log.write(Alert(ts=now + 2, kind='scenario', scenario='lost_object', agent='lens', text='Keys are lost'))
    log.write(CameraNotice(ts=now + 3, key='covered', kind='covered', text='The camera is covered'))
    assert [e['type'] for e in log_events(rig)] == ['camera', 'alert', 'nudge', 'habit']


def test_log_events_ignores_other_types_asked(tmp_path):
    rig = DeskRig(tmp_path)
    now = rig.clock.now()
    for event in noise(now):
        rig.app.event_log.write(event)
    rig.app.event_log.write(sip(now + 1))
    rig.app.event_log.write(Alert(ts=now + 2, kind='scenario', scenario='lost_object', text='Keys are lost'))
    asked = log_events(rig, '?types=agent_joined,detection,habit')
    assert [e['type'] for e in asked] == ['habit']                   # the habit one; the others are ignored
    nothing_valid = log_events(rig, '?types=agent_joined,detection')
    assert {e['type'] for e in nothing_valid} == {'habit', 'alert'}  # all it asked for was ignored: the usual four
    assert [e['type'] for e in log_events(rig, '?types=alert')] == ['alert']


def test_log_events_leave_out_what_the_camera_reads_from_the_light(tmp_path):
    rig = DeskRig(tmp_path)
    now = rig.clock.now()
    log = rig.app.event_log
    log.write(sip(now, habit='light', action='sunlight'))
    log.write(sip(now + 1, habit='light', action='reading'))         # a reading, not something you did
    log.write(sip(now + 2, habit='hydration', action=''))
    log.write(sip(now + 3, habit='posture', action='slouch'))
    log.write(sip(now + 4, habit='tasks', action='done'))
    log.write(sip(now + 5, habit='phone_free', action='streak'))
    got = [(e['habit'], e['action']) for e in log_events(rig)]
    assert got == [('phone_free', 'streak'), ('tasks', 'done'), ('light', 'sunlight')]


def test_log_events_limit_counts_only_events_that_matter(tmp_path):
    rig = DeskRig(tmp_path)
    now = rig.clock.now()
    for i in range(5):
        rig.app.event_log.write(sip(now + i))
    for i in range(20):
        for event in noise(now + 10 + i):
            rig.app.event_log.write(event)
    assert len(log_events(rig, '?limit=3')) == 3
    assert len(log_events(rig)) == 5


def test_log_events_never_count_the_pictures_waiting(tmp_path, monkeypatch):
    """counts() reads the day's picture lines too: the Log page needs only the events."""
    rig = DeskRig(tmp_path)
    rig.app.event_log.write(sip(rig.clock.now()))

    def no_counting(day):
        raise AssertionError(f'the Log page counted the whole day {day}')
    monkeypatch.setattr(rig.app.event_log, 'counts', no_counting)
    assert [e['type'] for e in log_events(rig)] == ['habit']


def test_alert_subtitle_plain(tmp_path):
    rig = DeskRig(tmp_path)
    now = rig.clock.now()
    log = rig.app.event_log
    log.write(Alert(ts=now, kind='proximity', agent='cup', extra={'near': 'computer', 'rule_label': 'too close'}))
    log.write(Alert(ts=now + 1, kind='proximity', agent='cup', extra={'near': '', 'rule_label': 'too close'}))
    log.write(Alert(ts=now + 2, kind='proximity', agent='cup', extra={'near': 'nobody-knows'}))
    log.write(Alert(ts=now + 3, kind='scenario', scenario='lost_object', feature='airpods', agent='lens',
                    text='Your AirPods are lost'))
    by_time = {e['ts']: e for e in log_events(rig)}
    assert by_time[now]['subtitle'] == 'near Computer'
    assert by_time[now + 1]['subtitle'] == ''                        # nothing after "near ": nothing at all
    assert by_time[now + 2]['subtitle'] == ''                        # a name nobody has
    assert by_time[now + 3]['subtitle'] == ''
    assert all('feature' not in e for e in by_time.values())         # no raw words
    assert all('subtitle' not in e for e in log_events(rig, '?types=habit,nudge,camera'))


# ── days ─────────────────────────────────────────────────────────────
def test_log_days_has_today_and_keep_days(tmp_path):
    config = changed(desk_config(tmp_path), 'event_log', keep_days=5)
    rig = DeskRig(tmp_path, config=config)
    today = date.fromisoformat(rig.clock.local_day(rig.clock.now()))
    for back in range(9):
        day = today - timedelta(days=back)
        rig.app.event_log.write(sip(rig.clock.now() - back * 86400))
        assert rig.clock.local_day(rig.clock.now() - back * 86400) == day.isoformat()
    reply = web_client(rig.app).get('/api/log/days').json()
    assert reply['today'] == today.isoformat()
    assert reply['days'] == [(today - timedelta(days=back)).isoformat() for back in range(5)]


# ── routes that went ─────────────────────────────────────────────────
@pytest.mark.parametrize('method, path', [
    ('GET', '/api/log/presence'), ('GET', '/api/log/detections'), ('POST', '/api/log/compact'),
    ('GET', '/api/habit_log'),
])
def test_removed_routes_are_404(tmp_path, method, path):
    rig = DeskRig(tmp_path)
    rig.watch(1, LAPTOP)
    reply = web_client(rig.app).request(method, path)
    assert reply.status_code in (404, 405), (method, path)
    assert web_client(rig.app).get('/api/log/status').status_code == 200       # the developer page still uses it


# ── live ─────────────────────────────────────────────────────────────
def test_live_reply_has_only_page_fields(tmp_path):
    rig = DeskRig(tmp_path)
    rig.watch(4, LAPTOP, CUP_FAR)
    live = web_client(rig.app).get('/api/live?qr=1').json()
    assert set(live) == {'camera', 'camera_source', 'camera_index', 'agents', 'detections', 'safety',
                         'camera_issues', 'ai', 'pi_command'}
    assert set(live['detections']) == {'extras'}
    assert set(live['safety']) == {'explanations'}
    assert set(live['ai']) == {'ready', 'problem'}
    for extra in live['detections']['extras']:
        assert set(extra) == {'yolo_class', 'ignored'}


def test_live_lists_what_is_on_the_desk_but_not_in_the_chat(tmp_path):
    """The page's "on the desk" list: the chat's members (from `agents`) plus everything else the warnings see."""
    rig = DeskRig(tmp_path)
    rig.watch(4, LAPTOP, CUP_FAR, Detection('scissors', 0.9, BBox(30, 400, 80, 450)))
    rig.watch(0.5, LAPTOP, CUP_FAR)                  # the scissors are missed in the newest pictures
    live = web_client(rig.app).get('/api/live').json()
    assert live['detections']['extras'] == [{'yolo_class': 'scissors', 'ignored': False}]
    assert {a['id'] for a in live['agents'] if a['on_desk']} == {'computer', 'cup'}


def test_live_on_a_phone_camera_has_the_phone_link_and_no_other_extras(tmp_path):
    config = changed(desk_config(tmp_path), 'camera', source='phone')
    rig = DeskRig(tmp_path, config=config)
    live = web_client(rig.app).get('/api/live?qr=1').json()
    assert set(live['phone']) == {'url', 'qr_svg'}
    assert 'pi_command' not in live
    assert web_client(rig.app).get('/api/live').json()['phone']['qr_svg'] is None


# ── photos ───────────────────────────────────────────────────────────
def test_snapshots_limit(tmp_path):
    rig = DeskRig(tmp_path)
    rig.watch(1, LAPTOP)
    ids = []
    for i in range(4):
        rig.clock.advance(1)
        ids.append(rig.app.photos.save(f'Photo {i}'))
    client = web_client(rig.app)
    every = client.get('/api/snapshots').json()['items']
    assert [p['id'] for p in every] == ids[::-1]                     # newest first, all of them
    assert [p['id'] for p in client.get('/api/snapshots?limit=2').json()['items']] == ids[::-1][:2]
    assert len(client.get('/api/snapshots?limit=100').json()['items']) == 4
    assert client.get('/api/snapshots?limit=x').status_code == 400
    assert len(client.get('/api/snapshots?limit=-3').json()['items']) == 1


# ── ui_settings ──────────────────────────────────────────────────────
def test_ui_settings_has_sensitivity_steps(tmp_path):
    rig = DeskRig(tmp_path)
    c = rig.config
    ui = web_client(rig.app).get('/api/ui_settings').json()
    assert [s['name'] for s in ui['sensitivity_steps']] == ['Picky', 'Careful', 'Normal', 'Easy', 'Eager']
    picky = ui['sensitivity_steps'][0]
    assert {k: picky[k] for k in ('name', 'min_confidence', 'join_after_sec')} == {
        'name': 'Picky', 'min_confidence': 0.65, 'join_after_sec': 10}
    assert ui['sensitivity_default'] == c.agents.sensitivity_default == 2      # the page keeps no default of its own
    assert ui['list_sizes'] == {
        'on_desk_shown': 8, 'camera_issues_shown': 2, 'risk_lines_shown': 6, 'side_photos_shown': 6,
        'quote_chars': 90, 'toast_chars': 140, 'mention_max_chars': 12, 'counter_label_chars': 60}
    assert (ui['web']['camera_note_after_sec'], ui['web']['saved_note_sec'], ui['web']['retry_start_sec']) == (5, 4, 3)
    assert ui['starter_todo'] == c.prompts.ui.starter_todo
    assert ui['starter_counters'] == list(c.prompts.ui.starter_counters)


def test_each_sensitivity_step_says_what_it_does_with_the_numbers_it_uses(tmp_path):
    """The Settings page shows the words from prompts.yaml (ui.sensitivity_step), filled in for each step: the
    join bar and wait, the flicker waits (object_states times the step's steady), the leave wait, and what a new
    thing needs (agents.new_things)."""
    rig = DeskRig(tmp_path)
    c = rig.config
    ui = web_client(rig.app).get('/api/ui_settings').json()
    assert ui['sensitivity_intro'] == c.prompts.ui.sensitivity_intro
    picky, normal = ui['sensitivity_steps'][0]['says'], ui['sensitivity_steps'][2]['says']
    assert picky == c.prompts.ui.sensitivity_step.format(
        name='Picky', sure=65, join_sec='10', appear_sec='6', gone_sec='6', move_percent=40, leave_min='4',
        new_sure=80, new_join_sec='12')
    assert normal == c.prompts.ui.sensitivity_step.format(
        name='Normal', sure=45, join_sec='3', appear_sec='3', gone_sec='3', move_percent=20, leave_min='2',
        new_sure=60, new_join_sec='5')


def test_ui_settings_leaves_out_what_the_page_no_longer_reads(tmp_path):
    ui = web_client(DeskRig(tmp_path).app).get('/api/ui_settings').json()
    assert 'habit_log_shown' not in ui['web']
    assert 'compact_every_min' not in ui['pi']
    assert not {'sunlight_default_min', 'glass_ml', 'goal_window_hours'} & set(ui['habits'])
