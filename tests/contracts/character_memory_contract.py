"""What every CharacterMemory must do (desktalk.ports.CharacterMemory): what the characters remember."""

from __future__ import annotations

TODO = {'type': 'todo', 'items': [{'text': 'Buy milk', 'done': False}]}


class CharacterMemoryContract:
    """Subclass and give a `memory` fixture (nothing remembered yet)."""

    def test_nothing_remembered_yet(self, memory):
        assert memory.recent('cup') == []
        assert memory.widget('cup') is None and memory.widgets() == {}
        assert memory.widget_views_of('cup') == []
        assert memory.user_active_at() == 0.0
        assert memory.vision_brief() == ''

    def test_only_the_newest_things_an_agent_said_are_kept(self, memory):
        for n in range(4):
            memory.remember('cup', f'line {n}', keep=3)
        assert memory.recent('cup') == ['line 1', 'line 2', 'line 3']
        assert memory.recent('laptop') == []
        memory.remember('cup', 'one more', keep=0)
        assert memory.recent('cup') == []

    def test_the_tracker_panel_the_app_sent(self, memory):
        memory.set_widget('cup', TODO)
        assert memory.widget('cup') == TODO and memory.widgets() == {'cup': TODO}
        memory.widgets()['laptop'] = {}
        assert memory.widgets() == {'cup': TODO}

    def test_panel_views_keep_the_newest(self, memory):
        for n in range(3):
            memory.note_widget_view('cup', f'view {n}', 100.0 + n, keep=2)
        assert memory.widget_views_of('cup') == [{'ts': 101.0, 'action': 'view 1'}, {'ts': 102.0, 'action': 'view 2'}]

    def test_when_you_last_typed(self, memory):
        memory.note_user_activity(1234.5)
        assert memory.user_active_at() == 1234.5

    def test_the_last_posture_check(self, memory):
        memory.set_vision_brief('posture=bad')
        assert memory.vision_brief() == 'posture=bad'
