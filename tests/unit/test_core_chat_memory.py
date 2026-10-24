"""What the characters remember (ChatMemory) and their tracker panels (TrackerPanels), kept in CharacterMemory."""

from pathlib import Path

from desktalk.config import load_config
from desktalk.core.chat.memory import ChatMemory, TrackerPanels
from tests.fakes import FakeCharacterMemory

CHAT = load_config(environ={}, dotenv=Path('/nonexistent')).chat


def test_a_line_and_a_reply_are_remembered_cut_to_size():
    desk = FakeCharacterMemory()
    memory = ChatMemory(desk, keep=2, reply_chars=5, user_chars=3)
    memory.remember('cup', 'first reply')
    memory.remember('cup', 'second reply', 'second message')
    memory.remember('cup', 'third reply', 'third message')
    assert memory.recent('cup') == desk.recent('cup') == ['[User said "sec"] → [You replied] secon',
                                                          '[User said "thi"] → [You replied] third']


def test_nothing_said_is_not_remembered():
    memory = ChatMemory(FakeCharacterMemory(), keep=2, reply_chars=5)
    memory.remember('cup', '   ')
    assert memory.recent('cup') == []


def test_a_panel_keeps_a_good_todo_list_and_the_newest_views():
    desk = FakeCharacterMemory()
    panels = TrackerPanels(desk, CHAT.model_copy(update={'panel_views_kept': 2, 'panel_action_max_chars': 4}))
    assert panels.save('paper', {'type': 'todo', 'items': [{'text': 'Essay'}]})
    assert not panels.save('paper', 'junk')
    assert desk.widget('paper') == {'type': 'todo', 'items': [{'text': 'Essay', 'done': False}]}
    for n, action in enumerate(('view', 'view', 'closed')):
        panels.note_view('paper', action, float(n))
    assert desk.widget_views_of('paper') == [{'ts': 1.0, 'action': 'view'}, {'ts': 2.0, 'action': 'clos'}]
