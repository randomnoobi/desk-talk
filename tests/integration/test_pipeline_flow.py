"""
The whole pipeline with a scripted detector:
pictures → detection events → roster / desk monitor / habits / event log → chat messages.
"""

import queue

from conftest import Feeder, obj, read_jsonl

LAPTOP = obj('laptop', 200, 200, 400, 350)
CUP_FAR = obj('cup', 560, 380, 600, 450)
CUP_NEAR = obj('cup', 410, 250, 450, 320)       # inside the laptop's spill zone


def drain(q):
    out = []
    while True:
        try:
            out.append(q.get_nowait())
        except queue.Empty:
            return out


def test_objects_join_chat_alert_sip_and_leave(make_app):
    app = make_app()
    sse = app.generator.subscribe()
    f = Feeder(app)

    # 1. A laptop and a cup appear: both join after 3 seconds and say hello.
    f.run(4, [LAPTOP, CUP_FAR])
    msgs = drain(sse)
    roster = [m for m in msgs if m['type'] == 'roster']
    assert {(m['agent']['id'], m['action']) for m in roster} == {('computer', 'joined'), ('cup', 'joined')}
    assert {m['text'] for m in roster} == {'Computer joined the chat', 'Cup joined the chat'}
    hellos = [m for m in msgs if m.get('hello')]
    assert {m['character'] for m in hellos} == {'computer', 'cup'}
    assert app.roster.member_ids() and set(app.desk.objects) == {'computer', 'cup'}

    # 2. The cup slides next to the laptop: Computer (fragile) warns, with a proof photo.
    f.run(2, [LAPTOP, CUP_NEAR])
    alerts = [m for m in drain(sse) if m.get('alert') == 'proximity']
    assert alerts and alerts[0]['character'] == 'computer'
    assert 'Cup' in alerts[0]['text'] and alerts[0]['snapshot']
    assert app.desk.snapshots.get(alerts[0]['snapshot'])
    assert app.habits.d['danger_count'] >= 1

    # 3. The cup is picked up for 8 seconds and put back: a sip.
    f.run(8, [LAPTOP])
    f.run(3, [LAPTOP, CUP_FAR])
    sips = [m for m in drain(sse) if m.get('habit') == 'hydration' and m['type'] == 'group_message']
    assert sips and 'Sip detected' in sips[0]['text'] and sips[0]['character'] == 'cup'
    assert app.habits.d['sips'] == 1

    # 4. The cup goes away for good: "Cup left the chat".
    f.run(50, [LAPTOP])
    left = [m for m in drain(sse) if m['type'] == 'roster' and m['action'] == 'left']
    assert [m['agent']['id'] for m in left] == ['cup']
    assert app.roster.member_ids() == ['computer']

    # 5. The event log has every picture, then compaction turns them into intervals.
    day = app.event_log.days()[0]
    raw = read_jsonl(app.event_log.day_dir(day) / 'detections.jsonl')
    assert len(raw) == len(set(e['frame_id'] for e in raw)) > 150
    assert raw[0]['detected_obj'][0]['label'] in ('laptop', 'cup', 'person')
    types = [e['type'] for e in app.event_log.events(day, limit=1000)]
    assert types.count('agent_joined') == 2 and types.count('agent_left') == 1
    assert 'alert' in types and 'habit' in types
    summary = app.event_log.compact()
    assert summary['frames'] == len(raw)
    ivs = app.event_log.presence(day, include_live=False)
    cups = [i for i in ivs if i['label'] == 'cup']
    laptops = [i for i in ivs if i['label'] == 'laptop']
    assert len(laptops) == 1 and len(cups) == 2           # the cup was away for 8 s, then gone
    assert laptops[0]['interval'][1] - laptops[0]['interval'][0] > 60


def test_unknown_object_gets_an_ai_personality(make_app):
    app = make_app(ai_reply='A tiny bodybuilder who holds papers together and brags about it.',
                   secrets={'gemini_api_key': 'fake'})
    sse = app.generator.subscribe()
    f = Feeder(app)
    f.run(4, [obj('stapler', 100, 100, 160, 140)])
    p = app.profiles.get('stapler')
    assert p and p.learned and 'bodybuilder' in p.personality
    assert any(m['type'] == 'roster' and m['agent']['name'] == 'Stapler' for m in drain(sse))


def test_pets_and_clutter(make_app):
    app = make_app()
    sse = app.generator.subscribe()
    f = Feeder(app)
    f.run(4, [LAPTOP, CUP_FAR])
    f.run(1, [LAPTOP, CUP_FAR, obj('cat', 0, 300, 150, 470)])
    pets = [m for m in drain(sse) if m.get('alert') == 'pet_risk']
    assert pets and pets[0]['text'].startswith('Cat detected near the desk!') and 'Cup' in pets[0]['text']


def test_camera_off_freezes_the_roster(make_app):
    """No pictures = no events: agents don't all 'leave' just because the Pi went quiet."""
    app = make_app()
    f = Feeder(app)
    f.run(4, [LAPTOP])
    f.t += 600                     # 10 minutes with no pictures at all
    assert app.roster.member_ids() == ['computer']
