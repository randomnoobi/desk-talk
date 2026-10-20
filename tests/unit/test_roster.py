"""Agents joining and leaving: presence hysteresis and the roster consumer."""

from desktalk.agents.profiles import ProfileBook
from desktalk.agents.roster import PresenceTracker, Roster
from desktalk.events.stream import EventStream
from desktalk.desk_state import DeskState
from desktalk.vision import relationships


def test_presence_needs_time_to_appear_and_to_go():
    p = PresenceTracker(appear_sec=3, gone_sec=10)
    t = 0.0
    assert p.update({'cup'}, t) == ([], [])
    assert p.update({'cup'}, t + 2.9) == ([], [])
    assert p.update({'cup'}, t + 3.0) == (['cup'], [])
    assert p.update(set(), t + 12.9) == ([], [])          # missing 9.9 s: still here
    assert p.update(set(), t + 13.0) == ([], ['cup'])
    assert not p.is_present('cup')


def test_presence_flicker_restarts_the_count():
    p = PresenceTracker(appear_sec=3, gone_sec=1)
    p.update({'cup'}, 0)
    p.update({'cup'}, 1)
    p.update({'cup'}, 5)                                   # 4 s gap > gone_sec: a new sighting
    assert not p.is_present('cup')
    for t in (5.5, 6, 6.5, 7, 7.5):
        assert p.update({'cup'}, t) == ([], [])
    assert p.update({'cup'}, 8) == (['cup'], [])           # 3 s after the new sighting began


def make_roster(settings, **agent_overrides):
    s = dict(settings.agents)
    s.update(agent_overrides)
    stream = EventStream(synchronous=True)
    events = []
    stream.subscribe('test', events.append)
    desk = DeskState()
    profiles = ProfileBook(settings.agents_file, learned_path='/nonexistent/x.json')
    return Roster(profiles, stream, desk, s, settings.presence), events, desk


def detection(ts, *objs, size=(640, 480)):
    return {'type': 'detection', 'ts': ts, 'size': list(size), 'detected_obj': list(objs)}


def o(label, conf=0.9, box=(100, 100, 200, 200)):
    x1, y1, x2, y2 = box
    return {'label': label, 'conf': conf, 'bbox': [x1, y1, x2, y2], 'center': [(x1 + x2) / 2, (y1 + y2) / 2]}


def test_object_joins_and_leaves(settings):
    roster, events, desk = make_roster(settings)
    t = 1000.0
    for i in range(12):                                      # 4 seconds at 3 pictures a second
        roster.on_detection(detection(t + i / 3, o('laptop'), o('person')))
    joined = [e for e in events if e['type'] == 'agent_joined']
    assert [(e['agent'], e['name'], e['label']) for e in joined] == [('computer', 'Computer', 'laptop')]
    assert roster.member_ids() == ['computer']
    assert set(desk.objects) == {'computer'}                # person is ignored
    for i in range(150):                                     # 50 s with no laptop
        roster.on_detection(detection(t + 4 + i / 3, o('person')))
    left = [e for e in events if e['type'] == 'agent_left']
    assert [e['agent'] for e in left] == ['computer']
    assert roster.member_ids() == [] and desk.objects == {}
    snap = roster.snapshot()
    assert snap[0]['id'] == 'computer' and snap[0]['on_desk'] is False and snap[0]['left_at']


def test_several_labels_make_one_agent(settings):
    roster, events, _ = make_roster(settings)
    for i in range(12):
        roster.on_detection(detection(i / 3, o('cup', 0.9), o('bottle', 0.8, (300, 100, 350, 200))))
    assert [e['agent'] for e in events if e['type'] == 'agent_joined'] == ['cup']


def test_unknown_objects_still_join(settings):
    roster, events, _ = make_roster(settings)
    for i in range(12):
        roster.on_detection(detection(i / 3, o('stapler')))
    j = [e for e in events if e['type'] == 'agent_joined']
    assert j and j[0]['agent'] == 'stapler' and j[0]['name'] == 'Stapler'


def test_weak_detections_dont_count(settings):
    roster, events, _ = make_roster(settings)
    for i in range(15):
        roster.on_detection(detection(i / 3, o('cup', 0.5)))      # cup needs 0.55 (agents.yaml)
        roster.on_detection(detection(i / 3, o('laptop', 0.3)))   # below the general 0.45
    assert not [e for e in events if e['type'] == 'agent_joined']


def test_only_objects_on_the_desk_join(settings):
    roster, events, _ = make_roster(settings)
    old = relationships.DESK_OUTLINE
    relationships.DESK_OUTLINE = [[0, 0.5], [1, 0.5], [1, 1], [0, 1]]   # desk = bottom half of the picture
    try:
        for i in range(12):
            roster.on_detection(detection(i / 3, o('cup', box=(100, 20, 150, 100)),      # above the desk
                                          o('laptop', box=(200, 300, 400, 450))))       # on the desk
    finally:
        relationships.DESK_OUTLINE = old
    assert [e['agent'] for e in events if e['type'] == 'agent_joined'] == ['computer']


def test_max_agents(settings):
    roster, events, _ = make_roster(settings, max_agents=2)
    for i in range(12):
        roster.on_detection(detection(i / 3, o('laptop'), o('book', box=(300, 300, 400, 400)),
                                      o('scissors', box=(10, 10, 50, 50))))
    assert len([e for e in events if e['type'] == 'agent_joined']) == 2
