"""What the AI is told about the desk right now: read from the desk scene and the safety board."""

from pathlib import Path

import pytest

from desktalk.config import load_config
from desktalk.core.chat.live_context import build_live_context
from desktalk.domain import AgentProfile, Danger, SeenObject
from tests.fakes import FakeDeskScene, FakeProfiles, FakeSafetyBoard

NOW = 1_760_000_000.0
KEEP = 50

COMPUTER = AgentProfile(id='computer', name='Computer', nickname='Monty', object='laptop', labels=['laptop'],
                        habits=['breaks'], fragile=True)
CUP = AgentProfile(id='cup', name='Cup', object='cup', labels=['cup', 'mug'], habits=['hydration'])
PAPER = AgentProfile(id='paper', name='Paper', object='paper', labels=['paper/notebook'])
WORDS = load_config(environ={}, dotenv=Path('/nonexistent')).prompts.live_context
SPILL = Danger(pair=('cup', 'laptop'), label='Cup in the laptop spill zone', kind='zone_intrusion', depth_px=12.5)


@pytest.fixture(scope='module')
def chat():
    return load_config(environ={}, dotenv=Path('/nonexistent')).chat.model_copy(update={
        'recent_events_kept': 10, 'recent_events_shown': 5, 'other_objects_shown': 2, 'insights_shown': 1})


@pytest.fixture
def profiles():
    return FakeProfiles([COMPUTER, CUP, PAPER], ignored=['person', 'chair'])


def box(label, conf=0.9):
    return SeenObject(label=label, box=(10, 10, 50, 50), center=(30, 30), confidence=conf)


def today():
    return {'hydration': {'water_ml': 1200, 'goal_ml': 2400, 'sips': 9},
            'breaks': {'count': 2, 'sitting_sec': 1500},
            'phone_free': {'counted': True, 'phone_free_sec': 3600},
            'light': {'sunlight_ts': None}}


def busy_desk():
    scene, safety = FakeDeskScene(), FakeSafetyBoard()
    scene.note_picture({label: box(label) for label in ('laptop', 'cup', 'person', 'scissors', 'book', 'pen')},
                       NOW - 3700, person=True, moved=False, new_session=True)
    scene.set_members({'paper': box('paper')}, NOW - 90)
    scene.set_members({'computer': box('laptop', 0.912)}, NOW - 125)
    scene.set_members({'computer': box('laptop', 0.912), 'cup': box('cup', 0.8)}, NOW - 30)
    scene.set_scan_labels({'airpods'})
    safety.report('DANGER', [SPILL], {'risk_level': 3, 'explanations': ['Liquid near electronics', 'Second insight']},
                  NOW - 65)
    for i in range(12, 0, -1):
        safety.note_alert({'time': NOW - 10 * i, 'character': 'computer', 'near': 'cup', 'rule_label': f'rule {i}'},
                          KEEP)
    return scene, safety


def live(desk, profiles, chat, members=('computer', 'cup'), camera_on=True, user_message='', words=WORDS):
    scene, safety = desk
    return build_live_context(scene, safety, profiles=profiles, members=list(members), habits_today=today(),
                              now=NOW, camera_on=camera_on, chat=chat, words=words, user_message=user_message)


def empty_desk():
    return FakeDeskScene(), FakeSafetyBoard()


def test_the_live_summary_of_a_busy_desk(profiles, chat):
    ctx = live(busy_desk(), profiles, chat)
    s = ctx['summary']
    assert 'THE CAMERA IS OFF' not in s
    assert '  - Computer (you play this as: laptop): vision label "laptop", here ~2m 5s' in s
    assert 'ALSO IN THE PICTURE (not chatting): book, pen' in s       # sorted, at most 2, no person
    assert 'ACTIVE DANGERS (risk level 3/4, state: DANGER)' in s
    assert '  - Cup in the laptop spill zone — intrusion 12.5px deep, active for 1m 5s' in s
    assert '  - Liquid near electronics' in s and 'Second insight' not in s
    assert 'LAST VISION PASS (Ollama lost-item scan) mentioned: airpods' in s
    assert 'RECENT EVENTS (last 10):' in s
    assert s.count(' near Cup (rule ') == 5 and '50s ago: Computer near Cup (rule 5)' in s
    assert 'HABITS TODAY: water ~1.2 of 2.4 L (9 sips); 2 breaks; sitting now 25 min; ' \
           'phone-free desk time 60 min' in s
    assert 'Session active for 1h 1m.' in s
    assert ctx['detected'] == [
        {'char_id': 'computer', 'name': 'Computer', 'object': 'laptop', 'confidence': 0.91, 'on_desk_for': '2m 5s'},
        {'char_id': 'cup', 'name': 'Cup', 'object': 'cup', 'confidence': 0.8, 'on_desk_for': '30s'}]
    assert ctx['dangers'] == [SPILL.to_dict()] and ctx['safety_state'] == 'DANGER'
    assert ctx['mentioned_objects'] == []


def test_an_empty_desk_with_the_camera_off(profiles, chat):
    ctx = live(empty_desk(), profiles, chat, members=(), camera_on=False)
    s = ctx['summary']
    assert s.startswith('THE CAMERA IS OFF RIGHT NOW: nothing below is live.')
    assert 'NOTHING IS IN THE CHAT YET' in s and 'NO ACTIVE DANGERS — desk is safe (risk 0/4).' in s
    assert 'RECENT EVENTS' not in s and 'Session active' not in s and ctx['detected'] == []


def test_mentioned_objects_get_their_status(profiles, chat):
    ctx = live(busy_desk(), profiles, chat, user_message='@cup @paper and @monty, hello')
    status = {m['char_id']: m['status'] for m in ctx['mentioned_objects']}
    assert status['cup'] == 'On desk ~30s; vision label cup (camera can be off). DANGER: Cup in the laptop spill zone'
    assert status['paper'] == 'Not currently on desk (last seen 1m 30s ago)'
    assert status['computer'].startswith('On desk ~2m 5s')
    paperless = live(empty_desk(), profiles, chat, members=(), user_message='@paper?')
    assert paperless['mentioned_objects'][0]['status'] == 'Not detected this session'


def test_things_on_the_desk_but_not_in_the_chat_are_seen_even_when_the_newest_picture_missed_them(profiles, chat):
    scene, safety = empty_desk()
    scene.note_picture({'laptop': box('laptop')}, NOW, person=False, moved=False, new_session=True)
    scene.set_members({'computer': box('laptop')}, NOW)
    scene.set_on_desk({'computer': box('laptop'), 'paper': box('notebook'), 'stapler': box('stapler')}, NOW)
    ctx = live((scene, safety), profiles, chat, members=('computer',), user_message='@paper?')
    assert 'ALSO IN THE PICTURE (not chatting): notebook, stapler' in ctx['summary']
    assert ctx['mentioned_objects'][0]['status'].startswith('On desk') and 'notebook' in ctx['mentioned_objects'][0]['status']


def test_when_you_say_the_camera_is_wrong_the_ai_is_told_to_believe_you(profiles, chat):
    assert 'USER IS PUSHING BACK' in live(busy_desk(), profiles, chat, user_message="that's not a cup")['summary']
    assert 'USER IS PUSHING BACK' not in live(busy_desk(), profiles, chat, user_message='hello')['summary']


def test_the_headings_and_lines_come_from_prompts_yaml(profiles, chat):
    words = WORDS.model_copy(update={'members': 'ON THE DESK:', 'member': '* {name} ({label})',
                                     'habits': 'WATER {water_l:.2f} L', 'pushback_words': ('nope',),
                                     'pushback': 'BELIEVE THEM.'})
    s = live(busy_desk(), profiles, chat, user_message='nope', words=words)['summary']
    assert 'ON THE DESK:\n* Computer (laptop)\n* Cup (cup)' in s
    assert 'WATER 1.20 L' in s and s.endswith('BELIEVE THEM.')
