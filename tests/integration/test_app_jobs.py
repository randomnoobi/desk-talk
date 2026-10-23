"""
The background jobs of the whole app built by bootstrap, each driven by calling tick() with a FakeClock:
habit reminders, Lens, small talk, lost items, the daily recap, vision habits and the Ollama keep-alive.
Ported from test_reminders_and_lens.py, plus the jobs that file didn't cover.
"""

from __future__ import annotations

import json
import time

from desktalk.core.chat import prompts
from desktalk.core.chat.tracker_actions import tracker_prompt
from desktalk.domain import BBox, Detection, DetectorState, UploadInfo
from tests.integration.app_kit import (
    CUP_FAR,
    LAPTOP,
    PERSON,
    VISION_ANSWER,
    DeskRig,
    changed,
    desk_config,
)

KEYS = Detection('key', 0.9, BBox(50, 50, 80, 70))
OLLAMA_URL = 'http://localhost:11434/v1'


def only_breaks(config, *, warmup_min=2, sit_limit_min=3):
    """Settings where only the breaks rule is on, with a short warm-up and sitting limit (so tests run quickly)."""
    for rule in ('hydration', 'light', 'phone_free', 'food', 'tasks'):
        config = changed(config, f'protocols.{rule}', enabled=False)
    config = changed(config, 'protocols.breaks', sit_limit_min=sit_limit_min)
    return changed(config, 'reminders', warmup_min=warmup_min)


def group_lines(messages):
    return [m for m in messages if m['type'] == 'group_message']


# ── habit reminders ──────────────────────────────────────────────────
def test_sitting_too_long_gets_a_break_nudge_from_the_computer(tmp_path):
    rig = DeskRig(tmp_path, config=only_breaks(desk_config(tmp_path)))
    rig.watch(4 * 60, LAPTOP, step=1.0)
    rig.messages()
    assert rig.tick(rig.app.reminders) == rig.config.reminders.check_every_sec
    [nudge] = group_lines(rig.messages())
    assert (nudge['character'], nudge['habit'], nudge['toast']) == ('computer', 'breaks', True)
    rig.watch(60, LAPTOP, step=1.0)
    rig.tick(rig.app.reminders)
    assert group_lines(rig.messages()) == []                 # not again so soon (reminders.min_gap_sec)
    day = rig.app.event_log.days()[0]
    [logged] = rig.app.event_log.events(day, {'nudge'}, 10)
    assert (logged['habit'], logged['agent'], logged['text']) == ('breaks', 'computer', nudge['text'])


def test_no_reminders_while_warming_up_or_with_the_camera_off(tmp_path):
    rig = DeskRig(tmp_path, config=only_breaks(desk_config(tmp_path), warmup_min=5, sit_limit_min=3))
    rig.watch(4 * 60, LAPTOP, step=1.0)
    rig.messages()
    rig.tick(rig.app.reminders)
    assert group_lines(rig.messages()) == []                 # only 4 minutes of watching
    rig.watch(2 * 60, LAPTOP, step=1.0)
    rig.clock.advance(rig.config.camera.disconnect_after_sec)          # the pictures stop
    rig.tick(rig.app.reminders)
    assert group_lines(rig.messages()) == []


def test_the_ai_puts_the_nudge_in_the_computers_words(tmp_path):
    words = 'Three minutes in that chair! Walk for five, and leave your phone here.'
    rig = DeskRig(tmp_path, config=only_breaks(desk_config(tmp_path, ai=True)), ai_reply=words)
    rig.watch(4 * 60, LAPTOP, step=1.0)
    rig.messages()
    rig.tick(rig.app.reminders)
    [nudge] = group_lines(rig.messages())
    assert (nudge['character'], nudge['text']) == ('computer', words)
    system = rig.ai_clients[0].requests[-1]['messages'][0]['content']
    assert 'You are Computer' in system and 'Protocols' in system


BOTTLE = Detection('bottle', 0.9, BBox(450, 300, 490, 380))


def test_hydration_rule_nudges_in_the_running_app(tmp_path):
    config = desk_config(tmp_path)
    for rule in ('breaks', 'light', 'phone_free', 'food', 'tasks'):
        config = changed(config, f'protocols.{rule}', enabled=False)
    rig = DeskRig(tmp_path, config=config)
    rig.watch(61 * 60, LAPTOP, BOTTLE, step=1)           # you and a bottle that never moves, for an hour
    rig.tick(rig.app.rule_runner)
    rig.tick(rig.app.reminders)
    day = rig.app.event_log.days()[0]
    [nudge] = rig.app.event_log.events(day, {'nudge'}, 10)
    assert nudge['rule'] == 'rule.hydration'
    rig.watch(3 * 60, LAPTOP, step=1)                    # the bottle is picked up
    rig.tick(rig.app.rule_runner)
    [outcome] = rig.app.event_log.events(day, {'rule_outcome'}, 10)
    assert outcome['result'] == 'worked'


# ── Lens ─────────────────────────────────────────────────────────────
def test_lens_speaks_up_when_the_pi_goes_quiet(tmp_path):
    rig = DeskRig(tmp_path)
    rig.watch(3, LAPTOP)
    rig.tick(rig.app.lens)
    said = []
    for _ in range(15):                                  # no more pictures
        rig.tick(rig.app.lens, after=2)
        said += [m for m in rig.messages() if m.get('character') == 'lens']
    assert said and 'Raspberry Pi' in said[0]['text'] and 'python3 -m raspberry_pi.camera' in said[0]['text']
    day = rig.app.event_log.days()[0]
    assert [e['key'] for e in rig.app.event_log.events(day, {'camera'}, 10)] == ['lost']


def test_lens_says_why_the_pis_own_detection_is_off(tmp_path):
    rig = DeskRig(tmp_path)
    off = rig.config.lens.wait_before_saying_sec.pi_detector
    said = []
    for second in range(int(off) + 10):
        rig.detector.detections = [LAPTOP, PERSON]
        for n in range(3):
            rig.picture(UploadInfo(frame_id=f'pi-{second * 3 + n:06d}', taken_at=rig.clock.now(),
                                   detector=DetectorState.OFF, detector_reason='the detector is still starting'))
            rig.clock.advance(1 / 3)
        if second % 2 == 0:
            rig.tick(rig.app.lens)
            said += [m['text'] for m in rig.messages() if m.get('character') == 'lens']
    assert [t for t in said if 'the detector is still starting' in t] != []
    assert rig.app.roster.member_ids() == ['computer']          # this computer detected meanwhile


# ── small talk ───────────────────────────────────────────────────────
def small_talk_ai():
    """The fake AI, answering by what it's asked: a topic, each agent's line, and the director (one more
    line, then stop)."""
    directed = []

    def reply(request):
        system = request['messages'][0]['content']
        if system == prompts.TOPIC_SYSTEM:
            return 'Who has been on this desk the longest?'
        if system == prompts.DIRECTOR_SYSTEM:
            directed.append(request)
            return '{"continue": true}' if len(directed) == 1 else '{"continue": false}'
        if system.startswith('You are Computer'):
            return 'Since 7 this morning. My fans remember every minute.'
        if system.startswith('You are Cup'):
            return 'Computer, how long have you been here today?'
        return 'Hey! All good over here on the desk.'
    return reply


def test_the_agents_make_small_talk_while_someone_is_at_the_desk(tmp_path):
    config = changed(desk_config(tmp_path, ai=True), 'reminders', warmup_min=1)
    config = changed(config, 'idle_chat', min_sec=30, max_sec=30)
    rig = DeskRig(tmp_path, config=config, ai_reply=small_talk_ai())
    rig.watch(70, LAPTOP, CUP_FAR, step=1.0)
    rig.messages()
    wait = rig.tick(rig.app.idle_chat)                    # the first round only sets when the chat starts
    for _ in range(3):
        rig.watch(wait, LAPTOP, CUP_FAR, step=1.0)
        wait = rig.tick(rig.app.idle_chat)
    lines = [m for m in rig.messages() if m.get('idle')]
    assert len(lines) == 2 and {m['character'] for m in lines} == {'computer', 'cup'}
    assert lines[1]['reply_to'] == {'character': lines[0]['character'], 'text': lines[0]['text']}
    assert wait == config.idle_chat.min_sec                 # the chat is over: the next one in a while


# ── lost items ───────────────────────────────────────────────────────
def test_keys_gone_a_while_get_a_message_and_an_answer_when_asked(tmp_path):
    rig = DeskRig(tmp_path)
    rig.watch(3, LAPTOP, KEYS)
    rig.watch(1, LAPTOP)
    rig.messages()
    alert_after = rig.config.lost_items.items['keys'].alert_after_min * 60
    rig.tick(rig.app.lost_items, after=alert_after)
    [said] = [m for m in rig.messages() if m.get('alert') == 'keys']
    assert said['character'] == 'lens' and "Keys: hasn't been seen for 15 min." in said['text']
    assert rig.app.habits.today()['lost_alerts'] == 1
    assert rig.app.lost_items.is_lost_query('have you seen my keys?')
    answer = rig.app.lost_items.answer('have you seen my keys?')
    assert 'Keys' in answer['text'] and 'minute' in answer['text'] and answer['cta']['action'] == 'captures'
    assert rig.app.lost_items.sightings()['keys']['alerted'] is True


# ── the daily recap ──────────────────────────────────────────────────
def test_the_daily_recap_goes_out_once_at_the_recap_hour(tmp_path):
    rig = DeskRig(tmp_path)
    rig.watch(4, LAPTOP)
    rig.messages()
    recap_at = time.mktime((2026, 10, 21, rig.config.daily_summary.hour, 0, 30, 0, 0, -1))
    rig.tick(rig.app.daily_summary, after=recap_at - rig.clock.now())
    [recap] = [m for m in rig.messages() if m.get('recap')]
    assert recap['character'] == 'computer' and 'Computer' in recap['text']
    rig.tick(rig.app.daily_summary, after=60)
    assert [m for m in rig.messages() if m.get('recap')] == []
    assert rig.app.store.load('daily_summary') == {'sent_day': '2026-10-21'}


# ── pictures to a vision AI (Ollama on this computer only) ──────────
def picture_questions(rig):
    return [(c.base_url, r['model']) for c in rig.ai_clients for r in c.requests
            if isinstance(r['messages'][-1]['content'], list)]


def test_with_ollama_the_vision_checks_look_at_the_desk_picture(tmp_path):
    rig = DeskRig(tmp_path, ai_reply=lambda request: (VISION_ANSWER.replace('good', 'slouch')
                                                      if isinstance(request['messages'][-1]['content'], list)
                                                      else 'Hey there!'), ollama=True)
    rig.watch(rig.config.object_states.person_appear_sec + 2, LAPTOP)     # long enough for you to count as here
    rig.tick(rig.app.vision_habits)
    answer, when = rig.app.vision_habits.latest()
    assert answer['posture'] == 'slouch' and when == rig.clock.now()
    assert picture_questions(rig) == [(OLLAMA_URL, rig.config.llm.vision_model)]
    rig.tick(rig.app.lost_items)
    assert len(picture_questions(rig)) == 2                  # the lost-items scan looked too


def test_without_ollama_no_picture_goes_to_the_cloud_ai(tmp_path):
    rig = DeskRig(tmp_path, ai_reply='Hey there!')
    rig.watch(2, LAPTOP)
    rig.tick(rig.app.vision_habits)
    rig.tick(rig.app.lost_items)
    assert picture_questions(rig) == [] and rig.app.vision_habits.latest() == ({}, 0.0)


def test_the_ollama_keep_alive_asks_ollama_something_tiny(tmp_path):
    rig = DeskRig(tmp_path, config=desk_config(tmp_path, ollama=True), ai_reply='hi')
    [keepalive] = [job for job, _ in rig.app.background_jobs() if job.name == 'ollama keep-alive']
    assert keepalive.tick(rig.clock.now()) == rig.config.llm.ollama_keepalive_sec
    [request] = rig.ai_clients[-1].requests
    assert request['max_tokens'] == 1 and request['model'] == rig.config.llm.ollama_model


# ── answering you ────────────────────────────────────────────────────
def test_a_reply_that_logs_a_sip_counts_it_on_the_habits_page(tmp_path):
    rig = DeskRig(tmp_path, ai_reply='Splash, nice one! [LOG_SIP]')
    rig.watch(4, LAPTOP, CUP_FAR)
    reply = rig.app.generator.answer('cup', 'I just drank some water')
    assert reply['messages'] == [{'character': 'cup', 'text': 'Splash, nice one!'}]
    assert rig.app.habits.today()['hydration']['sips'] == 1
    assert json.loads(json.dumps(rig.app.generator.live_context()))['summary']


def test_the_water_tracker_tells_the_ai_the_real_habit_numbers(tmp_path):
    rig = DeskRig(tmp_path)
    rig.app.habits.log('hydration', 'glass')
    now = rig.clock.now()
    text = tracker_prompt('hydration', rig.app.habits.today(now), None, [], now, rig.config.chat)
    assert '240 ml of a 2400 ml goal' in text                # a glass, and the goal in config/protocols.yaml
