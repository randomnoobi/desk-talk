"""What the AI is told about the desk, and the small text helpers around chat."""

import random
from pathlib import Path

import pytest

from desktalk.config import load_config
from desktalk.core.chat import context as cx
from desktalk.desk_state import DeskState
from desktalk.domain import AgentProfile
from tests.fakes import FakeProfiles

NOW = 1_760_000_000.0

COMPUTER = AgentProfile(id='computer', name='Computer', nickname='Monty', object='laptop', labels=['laptop'],
                        habits=['breaks'], fragile=True)
CUP = AgentProfile(id='cup', name='Cup', object='cup', labels=['cup', 'mug'], habits=['hydration'])
PAPER = AgentProfile(id='paper', name='Paper', object='paper', labels=['paper/notebook'])


@pytest.fixture(scope='module')
def chat():
    return load_config(environ={}, dotenv=Path('/nonexistent')).chat.model_copy(update={
        'history_from_app': 10, 'message_max_chars': 20, 'name_max_chars': 8, 'ollama_history_turns': 4,
        'summary_turns': 8, 'summary_chars': 70, 'recent_events_kept': 10, 'recent_events_shown': 5,
        'other_objects_shown': 2, 'insights_shown': 1, 'min_reply_words': 4})


@pytest.fixture
def profiles():
    return FakeProfiles([COMPUTER, CUP, PAPER], ignored=['person', 'chair'])


@pytest.fixture
def words(profiles):
    return cx.mention_words(profiles.all())


def today():
    return {'hydration': {'water_ml': 1200, 'goal_ml': 2400, 'sips': 9},
            'breaks': {'count': 2, 'sitting_sec': 1500},
            'phone_free': {'counted': True, 'phone_free_sec': 3600},
            'light': {'sunlight_ts': None}}


# ── names and @mentions ──────────────────────────────────────────────
def test_mention_words_cover_names_nicknames_objects_and_labels(words):
    assert words['monty'] == 'computer' and words['computer'] == 'computer' and words['laptop'] == 'computer'
    assert words['mug'] == 'cup' and words['paper/notebook'] == 'paper' and words['paper'] == 'paper'


def test_mentions(words):
    assert cx.parse_mentions('hey @monty and @cup, also @nobody', words) == ['computer', 'cup']


def test_speaker_names_include_lens(profiles):
    names = cx.speaker_names(profiles.all())
    assert names == {'computer': 'computer', 'cup': 'cup', 'paper': 'paper', 'lens': 'lens'}


def test_group_speaker_order(words):
    members, known, rng = ['computer', 'cup'], ['computer', 'cup', 'paper'], random.Random(1)
    assert cx.group_speaker_order('@paper how are you', words, members, known, rng) == ['paper']
    assert cx.group_speaker_order('@cup @monty hi', words, members, known, rng) == ['cup', 'computer']
    assert cx.group_speaker_order('cup, what do you think about the laptop?', words, members, known, rng) == ['cup']
    assert cx.group_speaker_order('hello everyone', words, members, known, rng)[0] in members
    assert cx.group_speaker_order('hello everyone', words, [], known, rng) == []


def test_named_agents_without_an_at(words):
    assert cx.detect_named('is the cup next to monty?', words) == ['computer', 'cup']
    assert cx.detect_named('is the cupboard open?', words) == []


# ── replies ──────────────────────────────────────────────────────────
def test_single_speaker_text():
    names = {'cup': 'cup', 'computer': 'computer'}
    assert cx.single_speaker_text('Cup: I am fine', CUP, names, 500) == 'I am fine'
    assert cx.single_speaker_text('Computer: help!', CUP, names, 500) == ''
    assert cx.single_speaker_text('Just a line', CUP, names, 500) == 'Just a line'
    assert cx.single_speaker_text('Cup: one\nComputer: two\nthree', CUP, names, 500) == 'one three'
    assert cx.single_speaker_text('x' * 600, CUP, names, 500) == 'x' * 500


@pytest.mark.parametrize('text, cut', [
    ('Hi', True), ('I am here for you', False), ('Could you move it to the', True), ('Move me back a bit.', False),
])
def test_cut_off_replies(chat, text, cut):
    assert cx.looks_cut_off(text, chat.min_reply_words) is cut


def test_evidence_check():
    calm = {'summary': 'NO ACTIVE DANGERS', 'dangers': []}
    assert cx.response_violates_evidence('The laptop is about to shatter!', calm)
    assert cx.response_violates_evidence('Careful, spill risk!', calm)
    assert not cx.response_violates_evidence('No spill risk today, all good.', calm)
    assert not cx.response_violates_evidence('Nice to meet you!', calm)
    assert not cx.response_violates_evidence('Careful, spill risk!', {'summary': '', 'dangers': [{'label': 'x'}]})
    assert not cx.response_violates_evidence('It might break!', calm, user_message='will it break?')


# ── chat history from the app ────────────────────────────────────────
def test_history_is_cleaned(chat):
    raw = [{'role': 'user', 'text': f'message {i}'} for i in range(12)] + [{'role': 'system', 'text': 'x'}, 'junk']
    clean = cx.sanitize_chat_history(raw, chat)
    assert clean == [{'role': 'user', 'text': f'message {i}'} for i in range(4, 12)]


def test_history_text_and_names_are_cut(chat):
    raw = [{'role': 'Assistant', 'text': '  ' + 'y' * 30, 'name': 'Computer the laptop'}, {'role': 'user', 'text': ''},
           {'role': 'user', 'text': 42}]
    assert cx.sanitize_chat_history(raw, chat) == [
        {'role': 'assistant', 'text': 'y' * 20, 'name': 'Computer'}, {'role': 'user', 'text': '42'}]


@pytest.mark.parametrize('raw', [None, 'text', {'role': 'user'}])
def test_no_history(chat, raw):
    assert cx.sanitize_chat_history(raw, chat) == []


def test_history_from_the_app_can_be_switched_off(chat):
    raw = [{'role': 'user', 'text': 'hi'}]
    assert cx.sanitize_chat_history(raw, chat.model_copy(update={'history_from_app': 0})) == []


def test_older_history_becomes_a_recap(chat):
    history = [{'role': 'user', 'text': f'message {i}'} for i in range(10)]
    recent, recap = cx.budget_history(history, 6, chat, local=False)
    assert recent == history[4:]
    assert recap == ' | '.join(f'User: "message {i}"' for i in range(4))


def test_a_local_ai_gets_fewer_turns_word_for_word(chat):
    history = [{'role': 'user', 'text': f'message {i}'} for i in range(10)]
    recent, _ = cx.budget_history(history, 6, chat, local=True)
    assert recent == history[6:]


def test_the_recap_is_short(chat):
    history = [{'role': 'assistant', 'name': 'Cup', 'text': 'z' * 100}, {'role': 'user', 'text': 'now'}]
    _, recap = cx.budget_history(history, 1, chat, local=False)
    assert recap == 'Cup: "' + 'z' * 70 + '…"'
    assert cx.budget_history(history, 6, chat, local=False) == (history, '')
    assert cx.budget_history([], 6, chat, local=False) == ([], '')


# ── the live desk ────────────────────────────────────────────────────
def busy_desk():
    desk = DeskState()
    desk.objects = {'computer': {'class_name': 'laptop', 'conf': 0.912}, 'cup': {'class_name': 'cup', 'conf': 0.8}}
    desk.object_first_seen = {'computer': NOW - 125, 'cup': NOW - 30}
    desk.seen_labels = {'laptop': {}, 'cup': {}, 'person': {}, 'scissors': {}, 'book': {}, 'pen': {}}
    desk.safety_dangers = [{'pair': ['cup', 'laptop'], 'label': 'Cup in the laptop spill zone',
                            'type': 'zone_intrusion', 'depth_px': 12}]
    desk.danger_active_since = {frozenset(['cup', 'laptop']): NOW - 65}
    desk.safety_state = 'DANGER'
    desk.rel_result = {'risk_level': 3, 'explanations': ['Liquid near electronics', 'Second insight']}
    desk.lost_last_vlm_visible = {'airpods'}
    desk.recent_events = [{'time': NOW - 10 * i, 'character': 'computer', 'near': 'cup', 'rule_label': f'rule {i}'}
                          for i in range(12, 0, -1)]
    desk.session_start = NOW - 3700
    return desk


def live(desk, profiles, chat, members=('computer', 'cup'), camera_on=True, user_message=''):
    return cx.build_live_context(desk, profiles=profiles, members=list(members), habits_today=today(), now=NOW,
                                 camera_on=camera_on, chat=chat, user_message=user_message)


def test_the_live_summary_of_a_busy_desk(profiles, chat):
    ctx = live(busy_desk(), profiles, chat)
    s = ctx['summary']
    assert 'THE CAMERA IS OFF' not in s
    assert '  - Computer (you play this as: laptop): vision label "laptop", here ~2m 5s' in s
    assert 'ALSO IN THE PICTURE (not chatting): book, pen' in s       # sorted, at most 2, no person
    assert 'ACTIVE DANGERS (risk level 3/4, state: DANGER)' in s
    assert '  - Cup in the laptop spill zone — intrusion 12px deep, active for 1m 5s' in s
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
    assert ctx['dangers'] == busy_desk().safety_dangers and ctx['safety_state'] == 'DANGER'
    assert ctx['mentioned_objects'] == []


def test_an_empty_desk_with_the_camera_off(profiles, chat):
    ctx = live(DeskState(), profiles, chat, members=(), camera_on=False)
    s = ctx['summary']
    assert s.startswith('THE CAMERA IS OFF RIGHT NOW: nothing below is live.')
    assert 'NOTHING IS IN THE CHAT YET' in s and 'NO ACTIVE DANGERS — desk is safe (risk 0/4).' in s
    assert 'RECENT EVENTS' not in s and 'Session active' not in s and ctx['detected'] == []


def test_mentioned_objects_get_their_status(profiles, chat):
    desk = busy_desk()
    desk.object_last_seen = {'paper': NOW - 90}
    ctx = live(desk, profiles, chat, user_message='@cup @paper and @monty, hello')
    status = {m['char_id']: m['status'] for m in ctx['mentioned_objects']}
    assert status['cup'] == 'On desk ~30s; vision label cup (camera can be off). DANGER: Cup in the laptop spill zone'
    assert status['paper'] == 'Not currently on desk (last seen 1m 30s ago)'
    assert status['computer'].startswith('On desk ~2m 5s')
    paperless = live(DeskState(), profiles, chat, members=(), user_message='@paper?')
    assert paperless['mentioned_objects'][0]['status'] == 'Not detected this session'


def test_when_you_say_the_camera_is_wrong_the_ai_is_told_to_believe_you(profiles, chat):
    assert 'USER IS PUSHING BACK' in live(busy_desk(), profiles, chat, user_message="that's not a cup")['summary']
    assert 'USER IS PUSHING BACK' not in live(busy_desk(), profiles, chat, user_message='hello')['summary']
