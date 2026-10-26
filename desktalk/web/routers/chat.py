"""
The agents and the chat:

    GET  /api/agents            who's in the chat (and who was earlier), plus Lens
    GET  /api/events            chat messages as they happen (Server-Sent Events)
    GET  /api/messages/recent   the last messages, for a page that just opened
    POST /api/activity          the app pings this while you type, so the small talk holds off (204)
    POST /api/chat              your message: a group chat or a 1-on-1 with an agent
    GET  /api/todos             the to-do list the server keeps, with due dates (the app loads it when it opens)
    POST /api/chat_todos        an agent's to-do list, as the app shows it (kept for the AI, and as the server's list)
    POST /api/chat_todos_sync   every agent's to-do list
    POST /api/widget_event      you opened (or used) an agent's panel
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import StreamingResponse

from desktalk.domain import LENS, UnknownChat
from desktalk.web.deps import App, Streams
from desktalk.web.schemas import ChatMessage, Ok, PanelEvent, PanelList, PanelLists, TodoItem, TodosReply, todos_in
from desktalk.web.streams import SSE_HEADERS, SSE_TYPE

UNKNOWN_CHAT = 'unknown chat'
MESSAGE_ID_LENGTH = 12          # as the MessageHub makes them

router = APIRouter()


@router.get('/api/agents')
def agents(app: App) -> dict[str, Any]:
    return {'agents': app.roster.snapshot(), 'lens': {'id': LENS, **app.config.prompts.lens.card.model_dump()}}


@router.get('/api/events')
def events(app: App, streams: Streams) -> StreamingResponse:
    web = app.config.web
    body = streams.chat_messages(app.hub, retry_ms=web.live_retry_ms, keepalive_sec=web.live_keepalive_sec)
    return StreamingResponse(body, media_type=SSE_TYPE, headers=SSE_HEADERS)


@router.get('/api/messages/recent')
def recent_messages(app: App) -> dict[str, Any]:
    return {'messages': app.hub.recent()}


@router.post('/api/activity', status_code=204)
def activity(app: App) -> Response:
    app.character_memory.note_user_activity(app.clock.now())
    return Response(status_code=204)


@router.post('/api/chat')
def chat(sent: ChatMessage, app: App) -> dict[str, Any]:
    app.character_memory.note_user_activity(app.clock.now())
    message = (sent.message or '').strip()
    if not message:
        raise HTTPException(400, 'empty message')
    if app.lost_items.is_lost_query(message):
        answer = app.lost_items.answer(message)
        in_one_on_one = sent.chat != 'group' and app.profiles.get(sent.chat) is not None
        return {'messages': [{'character': sent.chat if in_one_on_one else answer['speaker'], 'text': answer['text'],
                              'cta': answer['cta'], 'id': uuid.uuid4().hex[:MESSAGE_ID_LENGTH]}],
                'lost_object_lookup': True}
    try:
        return app.generator.answer(sent.chat, message, sent.recent_messages or sent.history, widget=sent.widget)
    except UnknownChat:
        raise HTTPException(400, UNKNOWN_CHAT) from None


@router.get('/api/todos')
def todos(app: App) -> TodosReply:
    kept = app.todos
    if kept is None:
        return TodosReply(has_list=False, items=[])
    return TodosReply(has_list=kept.has_list(),
                      items=[TodoItem(text=t.text, done=t.done, due=t.due) for t in kept.items()])


def _keep_todos(app: App, pid: str, widget: Any) -> bool:
    """Keep an agent's list as the server's to-do list, when it's the to-do panel's. True if it was kept."""
    profile = app.profiles.get(pid)
    items = todos_in(widget)
    if app.todos is None or profile is None or profile.tracker != 'todo' or items is None:
        return False
    app.todos.replace(items, app.clock.now())
    return True


@router.post('/api/chat_todos')
def chat_todos(sent: PanelList, app: App) -> Ok:
    if sent.chat is None or app.profiles.get(sent.chat) is None:
        raise HTTPException(400, UNKNOWN_CHAT)
    app.generator.save_widget(sent.chat, sent.widget)
    _keep_todos(app, sent.chat, sent.widget)
    return Ok()


@router.post('/api/chat_todos_sync')
def chat_todos_sync(sent: PanelLists, app: App) -> Ok:
    kept = False
    for pid, widget in sent.by_chat.items():
        if isinstance(widget, dict):
            app.generator.save_widget(pid, widget)      # an unknown agent or a bad list is skipped
            kept = kept or _keep_todos(app, pid, widget)    # the same list for each to-do agent: kept once
    return Ok()


@router.post('/api/widget_event')
def widget_event(sent: PanelEvent, app: App) -> Ok:
    if sent.chat is None or not app.generator.note_panel_event(sent.chat, sent.action):
        raise HTTPException(400, UNKNOWN_CHAT)
    return Ok()
