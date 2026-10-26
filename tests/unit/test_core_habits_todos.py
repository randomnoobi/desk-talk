"""The to-do list the server keeps (desktalk/core/habits/todos.py): due dates, ticking off, saving."""

import pytest

from desktalk.core.habits.todos import TodoFile, parse_day
from desktalk.domain import HabitEvent, Todo
from tests.fakes import FakeClock, InMemoryKeyValueStore, SyncEventBus

NOW = 1_760_000_000.0
DONE_WORDS = 'Nice, "{task}" is done!'


class Rig:
    def __init__(self, store=None, max_items=20, max_chars=200):
        self.store = store if store is not None else InMemoryKeyValueStore()
        self.bus = SyncEventBus()
        self.todos = TodoFile(self.store, self.bus, FakeClock(start=NOW), key='todos', max_items=max_items,
                              max_chars=max_chars, done_words=DONE_WORDS)

    def done(self):
        return self.bus.of_type(HabitEvent)


@pytest.mark.parametrize('due', ['next friday', '', '2026-02-30', '20261030', '2026-10-30T10:00', 'x' * 400])
def test_bad_due_dropped_todo_kept(due):
    kept = Rig().todos.replace([Todo('Essay', due=due)], NOW)
    assert kept == [Todo('Essay')]


def test_a_real_day_is_kept():
    assert Rig().todos.replace([Todo('Essay', due='2026-10-30')], NOW) == [Todo('Essay', due='2026-10-30')]


@pytest.mark.parametrize('text, day', [('2026-10-30', '2026-10-30'), ('2026-02-29', None), ('', None),
                                       (None, None), (20261030, None), (' 2026-10-30 ', None)])
def test_parse_day(text, day):
    found = parse_day(text)
    assert (found.isoformat() if found else None) == day


def test_old_list_without_due_loads():
    store = InMemoryKeyValueStore()
    store.save('todos', {'items': [{'text': 'Essay', 'done': False}, {'text': 'Email', 'done': True}]})
    todos = Rig(store).todos
    assert todos.has_list() is True
    assert todos.items() == [Todo('Essay'), Todo('Email', done=True)]


@pytest.mark.parametrize('saved', ['junk', ['Essay'], {'items': 'Essay'}, {'items': ['Essay', {'done': True}]}])
def test_a_broken_saved_list_loads_as_what_it_can(saved):
    store = InMemoryKeyValueStore()
    store.save('todos', saved)
    todos = Rig(store).todos
    assert todos.items() == []


def test_ticking_off_publishes_task_done():
    r = Rig()
    r.todos.replace([Todo('Essay'), Todo('Email')], NOW)
    r.todos.replace([Todo('Essay', done=True), Todo('Email')], NOW + 5)
    events = r.done()
    assert [(e.ts, e.habit, e.action, e.say, e.extra) for e in events] == [
        (NOW + 5, 'tasks', 'done', 'Nice, "Essay" is done!', {'task': 'Essay'})]
    r.todos.replace([Todo('Essay', done=True), Todo('Email')], NOW + 10)     # sent again: already done
    assert len(r.done()) == 1


def test_the_same_to_do_twice_says_done_once():
    r = Rig()
    r.todos.replace([Todo('Essay'), Todo('Essay')], NOW)
    r.todos.replace([Todo('Essay', done=True), Todo('Essay', done=True)], NOW + 5)
    assert len(r.done()) == 1


def test_list_survives_restart():
    store = InMemoryKeyValueStore()
    Rig(store).todos.replace([Todo('Essay', due='2026-10-30'), Todo('Email', done=True)], NOW)
    again = Rig(store).todos
    assert again.has_list() is True
    assert again.items() == [Todo('Essay', due='2026-10-30'), Todo('Email', done=True)]
    assert store.load('todos') == {'items': [{'text': 'Essay', 'done': False, 'due': '2026-10-30'},
                                             {'text': 'Email', 'done': True, 'due': None}]}


def test_text_is_cut_and_trimmed_and_empty_ones_dropped():
    kept = Rig(max_chars=5).todos.replace([Todo('  Essay about rivers '), Todo('   '), Todo('')], NOW)
    assert kept == [Todo('Essay')]


def test_only_so_many_are_kept():
    kept = Rig(max_items=2).todos.replace([Todo('a'), Todo(''), Todo('b'), Todo('c')], NOW)
    assert kept == [Todo('a'), Todo('b')]
