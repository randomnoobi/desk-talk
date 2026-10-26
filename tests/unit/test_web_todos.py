"""The to-do list routes (desktalk/web/routers/chat.py): GET /api/todos, and the lists the app sends
(POST /api/chat_todos, /api/chat_todos_sync) kept on the server."""

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from desktalk.domain import AgentProfile, HabitEvent, Todo
from desktalk.web.routers import chat
from tests.fakes import FakeClock, FakeProfiles, FakeTodoList, SyncEventBus

NOW = 1_760_000_000.0
BOOK = AgentProfile(id='paper', name='Book', labels=['book'], habits=['tasks'], tracker='todo')
CUP = AgentProfile(id='cup', name='Cup', labels=['cup'], habits=['hydration'], tracker='hydration')


class Panels:
    """The chat's side: the lists kept for the AI (ChatReplies.save_widget)."""

    def __init__(self):
        self.saved: list[tuple[str, object]] = []

    def save_widget(self, pid, raw):
        self.saved.append((pid, raw))
        return True


class Rig:
    def __init__(self, todos=True):
        self.bus = SyncEventBus()
        self.todos = FakeTodoList(self.bus) if todos else None
        self.generator = Panels()
        web = FastAPI()
        web.include_router(chat.router)
        web.state.desktalk = SimpleNamespace(todos=self.todos, generator=self.generator,
                                             profiles=FakeProfiles([BOOK, CUP]), clock=FakeClock(start=NOW))
        self.client = TestClient(web)


def a_list(*items):
    return {'type': 'todo', 'items': list(items)}


def test_get_todos_before_any_save():
    assert Rig().client.get('/api/todos').json() == {'has_list': False, 'items': []}


def test_get_todos_without_a_list_kept():
    assert Rig(todos=False).client.get('/api/todos').json() == {'has_list': False, 'items': []}


def test_sync_saves_and_get_returns():
    r = Rig()
    sent = a_list({'text': 'Essay', 'done': False, 'due': '2026-10-30'}, {'text': 'Email', 'done': True})
    reply = r.client.post('/api/chat_todos_sync', json={'by_chat': {'paper': sent}})
    assert reply.json() == {'ok': True}
    assert r.generator.saved == [('paper', sent)]                # the AI still gets it
    assert r.client.get('/api/todos').json() == {'has_list': True, 'items': [
        {'text': 'Essay', 'done': False, 'due': '2026-10-30'}, {'text': 'Email', 'done': True, 'due': None}]}


def test_one_list_saves_and_ticking_off_is_logged():
    r = Rig()
    r.client.post('/api/chat_todos', json={'chat': 'paper', 'widget': a_list({'text': 'Essay'})})
    r.client.post('/api/chat_todos', json={'chat': 'paper', 'widget': a_list({'text': 'Essay', 'done': True})})
    assert r.todos is not None and r.todos.items() == [Todo('Essay', done=True)]
    assert [(e.habit, e.action, e.ts) for e in r.bus.of_type(HabitEvent)] == [('tasks', 'done', NOW)]


def test_an_old_page_list_without_due_dates_is_kept():
    r = Rig()
    r.client.post('/api/chat_todos', json={'chat': 'paper', 'widget': a_list({'text': 'Essay', 'done': False})})
    assert r.client.get('/api/todos').json()['items'] == [{'text': 'Essay', 'done': False, 'due': None}]


def test_a_bad_due_date_is_dropped():
    r = Rig()
    r.client.post('/api/chat_todos', json={'chat': 'paper', 'widget': a_list({'text': 'Essay', 'due': 'friday'},
                                                                             {'text': 'Read', 'due': 7})})
    assert r.client.get('/api/todos').json()['items'] == [{'text': 'Essay', 'done': False, 'due': None},
                                                          {'text': 'Read', 'done': False, 'due': None}]


def test_lists_for_agents_without_a_to_do_panel_or_not_lists_are_not_kept():
    r = Rig()
    r.client.post('/api/chat_todos', json={'chat': 'cup', 'widget': a_list({'text': 'Not a task'})})
    r.client.post('/api/chat_todos_sync', json={'by_chat': {'cup': a_list({'text': 'Nope'}), 'paper': 'junk'}})
    r.client.post('/api/chat_todos', json={'chat': 'paper', 'widget': {'type': 'notes', 'items': []}})
    assert r.client.get('/api/todos').json() == {'has_list': False, 'items': []}


def test_an_unknown_chat_is_refused():
    r = Rig()
    assert r.client.post('/api/chat_todos', json={'chat': 'nobody', 'widget': a_list({'text': 'x'})}).status_code \
        == 400
    assert r.todos is not None and r.todos.has_list() is False


def test_the_routes_work_without_a_list_kept():
    r = Rig(todos=False)
    assert r.client.post('/api/chat_todos', json={'chat': 'paper', 'widget': a_list({'text': 'x'})}).json() == \
        {'ok': True}
    assert r.client.post('/api/chat_todos_sync', json={'by_chat': {'paper': a_list()}}).json() == {'ok': True}
