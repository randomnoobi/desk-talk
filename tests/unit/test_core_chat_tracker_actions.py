"""Trackers: what the AI is told about a tracker panel, and the action tags it can answer with."""

from pathlib import Path

import pytest

from desktalk.config import load_config
from desktalk.core.chat import tracker_actions as ta

NOW = 1_760_000_000.0


@pytest.fixture(scope='module')
def chat():
    return load_config(environ={}, dotenv=Path('/nonexistent')).chat.model_copy(update={
        'todo_max_items': 3, 'todo_max_chars': 10, 'panel_views_window_sec': 3600, 'panel_views_min': 2})


def today(**changes):
    """What HabitsView.today() returns, with made-up numbers."""
    d = {
        'hydration': {'sips': 3, 'water_ml': 240, 'goal_ml': 2400, 'pace_ml': 900, 'last_sip_ts': None},
        'breaks': {'count': 2, 'last_break_ts': None, 'sitting_sec': 1260, 'sit_limit_min': 30},
        'food': {'entries': []},
        'phone_free': {'counted': True, 'phone_free_sec': 3000, 'streak_sec': 600, 'goal_min': 120,
                       'phone_on_desk': False},
        'light': {'sunlight_ts': None, 'lux': None, 'brightness': 128.0},
    }
    d.update(changes)
    return d


# ── action tags ──────────────────────────────────────────────────────
@pytest.mark.parametrize('tracker, text, actions', [
    ('todo', 'Sure! [ADD_TODO:Buy milk] [DONE_TODO:Essay]', [{'action': 'add_todo', 'text': 'Buy milk'},
                                                            {'action': 'done_todo', 'text': 'Essay'}]),
    ('todo', 'Gone. [DEL_TODO:Old task]', [{'action': 'del_todo', 'text': 'Old task'}]),
    ('hydration', 'Splash! [LOG_SIP]', [{'action': 'log_sip'}]),
    ('hydration', 'A whole glass! [LOG_GLASS]', [{'action': 'log_glass'}]),
    ('breaks', 'Nice walk [LOG_BREAK]', [{'action': 'log_break'}]),
    ('food', 'Yum [LOG_SNACK_HEALTHY:almonds]', [{'action': 'log_snack', 'label': 'almonds', 'healthy': True}]),
    ('food', 'Oops [LOG_SNACK_JUNK:chips]', [{'action': 'log_snack', 'label': 'chips', 'healthy': False}]),
    ('light', 'Sunshine! [LOG_SUNLIGHT:15]', [{'action': 'log_sunlight', 'minutes': 15}]),
])
def test_action_tags_are_pulled_out(tracker, text, actions):
    clean, got = ta.parse_actions(text, tracker, sunlight_default_min=10)
    assert got == actions and '[' not in clean


def test_sunlight_without_minutes_uses_the_default():
    _, got = ta.parse_actions('Outside! [LOG_SUNLIGHT]', 'light', sunlight_default_min=12)
    assert got == [{'action': 'log_sunlight', 'minutes': 12}]


def test_other_agents_tags_are_hidden_not_applied():
    clean, got = ta.parse_actions('Splash! That counts. [LOG_SIP]', 'breaks', sunlight_default_min=10)
    assert got == [] and clean == 'Splash! That counts.'


def test_leftover_tags_are_removed_but_other_brackets_stay():
    assert ta.strip_action_tags('Done [ADD_TODO:Essay] and [LOG_SUNLIGHT:10] ok [not a tag]') == \
        'Done and ok [not a tag]'


def test_each_tracker_has_its_own_action_instructions():
    assert '[LOG_SIP]' in ta.action_instructions('hydration')
    assert '[ADD_TODO:' in ta.action_instructions('todo')
    assert ta.action_instructions(None) == ''


# ── doing the actions ────────────────────────────────────────────────
class HabitLog:
    def __init__(self):
        self.logged = []

    def log(self, habit, action, **details):
        self.logged.append((habit, action, details))


def test_habit_actions_go_to_the_habit_tracker_and_the_rest_comes_back():
    log = HabitLog()
    left = ta.apply_habit_actions(log, [
        {'action': 'log_sip'}, {'action': 'log_glass'}, {'action': 'log_break'},
        {'action': 'log_snack', 'label': 'almonds', 'healthy': True},
        {'action': 'log_sunlight', 'minutes': 15}, {'action': 'add_todo', 'text': 'Essay'}])
    assert left == [{'action': 'add_todo', 'text': 'Essay'}]
    assert log.logged == [
        ('hydration', 'sip', {'how': 'chat', 'quiet': True}),
        ('hydration', 'glass', {'how': 'chat', 'quiet': True}),
        ('breaks', 'break', {'quiet': True}),
        ('food', 'food', {'label': 'almonds', 'healthy': True, 'quiet': True}),
        ('light', 'sunlight', {'minutes': 15, 'quiet': True}),
    ]


# ── the to-do list from the app ──────────────────────────────────────
def test_the_todo_list_is_cleaned_and_cut_to_size(chat):
    raw = {'type': 'todo', 'items': [{'text': ' x '}, {'text': ''}, {'text': 'a very long task', 'done': 1},
                                     'junk', {'text': 'three'}, {'text': 'four'}]}
    assert ta.normalize_todo(raw, chat) == {'type': 'todo', 'items': [
        {'text': 'x', 'done': False}, {'text': 'a very lon', 'done': True}]}


@pytest.mark.parametrize('raw', [None, 'todo', {'type': 'notes', 'items': []}])
def test_anything_but_a_todo_list_is_ignored(chat, raw):
    assert ta.normalize_todo(raw, chat) is None


# ── what the AI is told ──────────────────────────────────────────────
def test_the_water_tracker_uses_the_habit_numbers(chat):
    text = ta.tracker_prompt('hydration', today(), None, [], NOW, chat)
    assert '240 ml of a 2400 ml goal (3 sips)' in text and 'pace for now 900 ml' in text


def test_the_breaks_tracker_says_how_long_you_have_sat(chat):
    text = ta.tracker_prompt('breaks', today(), None, [], NOW, chat)
    assert '2 break(s) today' in text and 'Sitting for 21 min' in text and 'every 30 minutes' in text


def test_the_todo_tracker_lists_open_and_done_items(chat):
    todo = {'items': [{'text': 'Essay', 'done': False}, {'text': 'Email', 'done': True}]}
    text = ta.tracker_prompt('todo', today(), todo, [], NOW, chat)
    assert 'TO-DO LIST' in text and '- [ ] Essay' in text and '- [x] Email' in text


def test_food_phone_and_light_trackers(chat):
    food = ta.tracker_prompt('food', today(food={'entries': [{'label': 'apple', 'healthy': True},
                                                             {'label': 'chips', 'healthy': False}]}),
                             None, [], NOW, chat)
    assert 'Healthy: apple' in food and 'Treats: chips' in food
    assert 'Empty today' in ta.tracker_prompt('food', today(), None, [], NOW, chat)
    assert '50 min of desk time' in ta.tracker_prompt('phone_free', today(), None, [], NOW, chat)
    not_counted = today(phone_free={'counted': False})
    assert 'not counted' in ta.tracker_prompt('phone_free', not_counted, None, [], NOW, chat)
    assert 'brightness 128/255' in ta.tracker_prompt('light', today(), None, [], NOW, chat)


def test_no_tracker_no_block(chat):
    assert ta.tracker_prompt(None, today(), None, [], NOW, chat) == ''


def test_opening_the_panel_often_is_mentioned(chat):
    views = [{'ts': NOW - 100, 'action': 'view'}, {'ts': NOW - 50, 'action': 'view'}]
    assert 'opened this panel 2 time(s)' in ta.tracker_prompt('hydration', today(), None, views, NOW, chat)


def test_old_or_few_panel_views_are_not_mentioned(chat):
    old = [{'ts': NOW - 4000, 'action': 'view'}, {'ts': NOW - 50, 'action': 'view'}]
    assert 'BEHAVIOR' not in ta.tracker_prompt('hydration', today(), None, old, NOW, chat)
    assert 'BEHAVIOR' not in ta.tracker_prompt('hydration', today(), None, old[1:], NOW, chat)
