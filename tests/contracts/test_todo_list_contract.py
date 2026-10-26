"""What every TodoList must do (desktalk/ports.py): the to-do list the server keeps, with due dates. Ticking a
to-do off publishes a HabitEvent for 'tasks'. The fake runs it here; the real one (TodoFile) joins as one more test
class in this file, made with the `bus` and `clock` fixtures."""

from __future__ import annotations

import pytest

from desktalk.domain import HabitEvent, Todo
from desktalk.ports import TodoList
from tests.fakes import FakeClock, FakeTodoList, SyncEventBus

NOW = 1_760_000_000.0


class TodoListContract:
    """Subclass and give a `todos` fixture (nothing saved yet), made with the `bus` and `clock` fixtures."""

    @pytest.fixture
    def bus(self):
        return SyncEventBus()

    @pytest.fixture
    def clock(self):
        return FakeClock(start=NOW)

    @pytest.fixture
    def todos(self, bus, clock):
        raise NotImplementedError

    def test_it_is_a_todo_list(self, todos):
        assert isinstance(todos, TodoList)

    def test_nothing_saved_yet(self, todos):
        assert todos.items() == [] and todos.has_list() is False

    def test_a_list_is_kept_with_its_due_dates(self, todos):
        items = [Todo('Essay', due='2026-10-30'), Todo('Email', done=True), Todo('Call mum')]
        assert todos.replace(items, NOW) == items
        assert todos.items() == items and todos.has_list() is True

    def test_an_empty_list_is_still_a_list(self, todos):
        todos.replace([], NOW)
        assert todos.items() == [] and todos.has_list() is True

    def test_a_bad_due_date_is_dropped_and_the_to_do_kept(self, todos):
        kept = todos.replace([Todo('Essay', due='next friday'), Todo('Email', due=''), Todo('Read', due='2026-02-30')],
                             NOW)
        assert [(t.text, t.due) for t in kept] == [('Essay', None), ('Email', None), ('Read', None)]

    def test_ticking_one_off_says_it_is_done(self, todos, bus):
        todos.replace([Todo('Essay'), Todo('Email')], NOW)
        assert bus.of_type(HabitEvent) == []
        todos.replace([Todo('Essay', done=True), Todo('Email')], NOW + 5)
        done = bus.of_type(HabitEvent)
        assert [(e.habit, e.action, e.ts, e.extra.get('task')) for e in done] == [('tasks', 'done', NOW + 5, 'Essay')]

    def test_a_to_do_already_done_says_nothing_again(self, todos, bus):
        todos.replace([Todo('Essay', done=True)], NOW)          # new, and already done: it didn't turn done here
        todos.replace([Todo('Essay', done=True)], NOW + 5)
        assert bus.of_type(HabitEvent) == []

    def test_what_comes_back_is_a_copy(self, todos):
        todos.replace([Todo('Essay')], NOW)
        todos.items().clear()
        assert todos.items() == [Todo('Essay')]


class TestFakeTodoList(TodoListContract):
    @pytest.fixture
    def todos(self, bus, clock):
        return FakeTodoList(bus)


def test_the_fake_needs_no_bus():
    fake = FakeTodoList()
    fake.replace([Todo('Essay')], NOW)
    assert fake.replace([Todo('Essay', done=True)], NOW) == [Todo('Essay', done=True)]
