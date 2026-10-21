"""Reminders (Protocols nudges) and Lens, run through their threads' tick()."""

import queue
import time

from desktalk.chat.lens import LensThread
from desktalk.habits.reminders import HabitReminderThread, speaker_for
from tests.conftest import Feeder, obj


def drain(q):
    out = []
    while True:
        try:
            out.append(q.get_nowait())
        except queue.Empty:
            return out


def warm_app(make_app, at_hour=13, **kw):
    """An app whose camera has been watching for an hour, at a chosen time of day."""
    app = make_app(**kw)
    lt = time.localtime()
    start = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, at_hour - 1, 0, 0, 0, 0, -1))
    f = Feeder(app, start=start, step=1.0)       # one picture a second keeps these long runs quick
    return app, f


def test_sitting_too_long_gets_a_break_nudge(make_app):
    app, f = warm_app(make_app)
    f.run(35 * 60, [obj('laptop', 200, 200, 400, 350), obj('cup', 560, 380, 600, 450)])
    app.habits.log('hydration', 'glass')        # so water isn't the most urgent thing
    app.habits.log('light', 'sunlight')
    app.source.last_frame_ts = time.time()      # the camera is "on"
    sse = app.generator.subscribe()
    r = HabitReminderThread(app)
    nudge = r.tick(f.t)
    assert nudge is not None
    msgs = [m for m in drain(sse) if m['type'] == 'group_message']
    assert msgs and msgs[0]['toast']
    by_habit = {n.habit for n in app.habits.nudges(f.t)}
    assert 'breaks' in by_habit
    # nothing again right away: min gap between reminders
    assert r.tick(f.t + 60) is None
    # the nudge was logged
    day = app.event_log.days()[0]
    assert any(e['type'] == 'nudge' for e in app.event_log.events(day))


def test_no_reminders_while_warming_up_or_camera_off(make_app):
    app, f = warm_app(make_app)
    f.run(5 * 60, [obj('laptop', 200, 200, 400, 350)])
    app.source.last_frame_ts = time.time()
    assert HabitReminderThread(app).tick(f.t) is None          # only 5 minutes of watching
    f.run(30 * 60, [obj('laptop', 200, 200, 400, 350)])
    app.source.last_frame_ts = 0                                # camera off
    assert HabitReminderThread(app).tick(f.t) is None


def test_speaker_is_an_agent_for_that_habit_or_lens(make_app):
    app = make_app()
    p = app.profiles
    p.for_label('laptop')
    assert speaker_for(('breaks',), ['computer', 'cup'], p) == 'computer'
    assert speaker_for(('hydration',), ['computer'], p) == 'lens'
    assert speaker_for(('light',), [], p) == 'lens'


def test_ai_phrases_the_nudge_in_character(make_app):
    app, f = warm_app(make_app, ai_reply='Hey, 35 minutes of sitting! Walk five minutes, leave your phone.',
                      secrets={'gemini_api_key': 'fake'})
    f.run(35 * 60, [obj('laptop', 200, 200, 400, 350)])
    app.habits.log('hydration', 'glass', quiet=True)
    app.habits.log('light', 'sunlight', quiet=True)
    app.source.last_frame_ts = time.time()
    nudge = HabitReminderThread(app).tick(f.t)
    assert nudge and nudge.habit == 'breaks'
    system = app.llm.client.requests[-1]['messages'][0]['content']
    assert 'Protocols' in system and 'sitting' in system


def test_lens_reports_the_pi_going_quiet(make_app):
    app = make_app()
    f = Feeder(app)
    f.run(3, [obj('laptop', 200, 200, 400, 350)])
    app.source.last_frame_ts = time.time()
    lens = LensThread(app)
    now = time.time()
    lens.tick(now)
    app.source.last_frame_ts = 0                 # pictures stop
    said = []
    for i in range(1, 30):
        said += lens.tick(now + i)
    assert said and said[0]['character'] == 'lens' and 'Raspberry Pi' in said[0]['text']


def test_idle_chat_round_between_agents(make_app, monkeypatch):
    """Small talk: a topic, an opening question, then the director picks who answers."""
    import desktalk.chat.idle_chat as idle_chat
    monkeypatch.setattr(idle_chat.random, 'choice', lambda seq: seq[0])   # Cup opens (normally random)
    monkeypatch.setattr(idle_chat.time, 'sleep', lambda sec: None)        # no pause between lines
    replies = iter(['Who has been on this desk the longest?',                 # topic
                    'Computer, how long have you been here?',               # opening line (Cup)
                    '{"continue": true, "next": "Computer"}',                 # director
                    'Since 7 this morning. My fans remember every minute.',  # Computer
                    '{"continue": false}'])
    app = make_app(ai_reply=lambda kw: next(replies), secrets={'gemini_api_key': 'fake'})
    Feeder(app).run(4, [obj('laptop', 200, 200, 400, 350), obj('cup', 560, 380, 600, 450)])
    sse = app.generator.subscribe()
    t = idle_chat.IdleChatThread(app)
    t.run_conversation(['cup', 'computer'])
    lines = [m for m in drain(sse) if m.get('idle')]
    assert [m['character'] for m in lines] == ['cup', 'computer']
    assert lines[1]['reply_to']['character'] == 'cup'


def test_threads_start_and_stop_cleanly(make_app):
    app = make_app(synchronous=False)
    app.producer.start()
    app.scheduler.start()
    app.producer.stop()
    app.scheduler.stop()
    app.producer.join(3)
    app.scheduler.join(3)
    assert not app.producer.is_alive() and not app.scheduler.is_alive()


def test_recent_messages_for_a_page_that_opens_later(make_app):
    from desktalk.web.routes import create_web_app
    app = make_app()
    Feeder(app).run(4, [obj('laptop', 200, 200, 400, 350)])
    recent = create_web_app(app).test_client().get('/api/messages/recent').json['messages']
    assert recent[0]['type'] == 'roster' and recent[0]['text'] == 'Computer joined the chat'
