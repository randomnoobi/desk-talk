"""Chat helpers: mentions, who answers, action tags, lost-item words."""

from types import SimpleNamespace

import pytest

from desktalk.agents.profiles import ProfileBook
from desktalk.chat import tracker_actions
from desktalk.chat.context import (budget_history, group_speaker_order, parse_mentions, response_violates_evidence,
                                   sanitize_chat_history, single_speaker_text)


@pytest.fixture
def words(settings, tmp_path):
    return ProfileBook(settings.agents_file, learned_path=tmp_path / 'l.json').mention_words()


def test_mentions(words):
    assert parse_mentions('hey @monty and @cup, also @nobody', words) == ['computer', 'cup']


def test_group_speaker_order(words):
    members = ['computer', 'cup']
    known = ['computer', 'cup', 'paper']
    assert group_speaker_order('@paper how are you', words, members, known) == ['paper']
    assert group_speaker_order('cup, what do you think about the laptop?', words, members, known) == ['cup']
    assert group_speaker_order('hello everyone', words, members, known)[0] in members
    assert group_speaker_order('hello everyone', words, [], known) == []


def test_single_speaker_text():
    a = SimpleNamespace(id='cup', name='Cup')
    names = {'cup': 'cup', 'computer': 'computer'}
    assert single_speaker_text('Cup: I am fine', a, names) == 'I am fine'
    assert single_speaker_text('Computer: help!', a, names) == ''
    assert single_speaker_text('Just a line', a, names) == 'Just a line'


def test_evidence_check():
    calm = {'summary': 'NO ACTIVE DANGERS', 'dangers': []}
    assert response_violates_evidence('The laptop is about to shatter!', calm)
    assert response_violates_evidence('Careful, spill risk!', calm)
    assert not response_violates_evidence('No spill risk today, all good.', calm)
    assert not response_violates_evidence('Nice to meet you!', calm)


def test_history_helpers():
    raw = [{'role': 'user', 'text': f'message {i}'} for i in range(12)] + [{'role': 'system', 'text': 'x'}, 'junk']
    clean = sanitize_chat_history(raw)
    assert len(clean) <= 10 and all(t['role'] == 'user' for t in clean)
    recent, recap = budget_history(clean, verbatim_turns=6)
    assert len(recent) == 6 and 'User: "message' in recap


@pytest.mark.parametrize('tracker,text,actions', [
    ('todo', 'Sure! [ADD_TODO:Buy milk] [DONE_TODO:Essay]', [{'action': 'add_todo', 'text': 'Buy milk'},
                                                            {'action': 'done_todo', 'text': 'Essay'}]),
    ('hydration', 'Splash! [LOG_SIP]', [{'action': 'log_sip'}]),
    ('hydration', 'A whole glass! [LOG_GLASS]', [{'action': 'log_glass'}]),
    ('breaks', 'Nice walk [LOG_BREAK]', [{'action': 'log_break'}]),
    ('food', 'Yum [LOG_SNACK_HEALTHY:almonds]', [{'action': 'log_snack', 'label': 'almonds', 'healthy': True}]),
    ('light', 'Sunshine! [LOG_SUNLIGHT:15]', [{'action': 'log_sunlight', 'minutes': 15}]),
])
def test_action_tags(tracker, text, actions):
    clean, got = tracker_actions.parse_actions(text, tracker)
    assert got == actions and '[' not in clean


def test_other_agents_tags_are_hidden_not_applied():
    clean, got = tracker_actions.parse_actions('Splash! That counts. [LOG_SIP]', 'breaks')   # Computer copying Cup
    assert got == [] and clean == 'Splash! That counts.'
    assert tracker_actions.strip_action_tags('Done [ADD_TODO:Essay] and [LOG_SUNLIGHT:10] ok [not a tag]') == \
        'Done and ok [not a tag]'


def test_broadcast_hides_leftover_tags(make_app):
    app = make_app()
    msg = app.generator.broadcast({'type': 'group_message', 'character': 'computer', 'text': 'Nice [LOG_BREAK]'})
    assert msg['text'] == 'Nice'


def test_tracker_prompt_uses_habit_numbers(settings, tmp_path):
    from conftest import HabitRig
    r = HabitRig(settings, tmp_path)
    r.tracker.log('hydration', 'glass')
    today = r.tracker.today(r.t)
    assert '240 ml of a 2400 ml goal' in tracker_actions.tracker_prompt('hydration', today, None)
    assert "TO-DO LIST" in tracker_actions.tracker_prompt('todo', today, {'items': [{'text': 'Essay', 'done': False}]})
    assert tracker_actions.normalize_todo({'type': 'todo', 'items': [{'text': ' x '}, {'text': ''}]}) == \
        {'type': 'todo', 'items': [{'text': 'x', 'done': False}]}


def test_lost_item_words(make_app):
    app = make_app()
    li = app.lost_items
    assert li.items_in_text('have you seen my keys?') == ['keys']
    assert li.is_lost_query('where did i put my airpods')
    assert not li.is_lost_query('where is the sun')
    assert li.items_in_labels({'earphone'}) == {'airpods'}
    assert li.items_in_labels({'car keys'}) == {'keys'}
    assert li.items_in_labels({'keyboard'}) == set()
