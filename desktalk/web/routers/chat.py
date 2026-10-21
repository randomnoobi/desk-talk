"""
The agents and the chat:

    GET  /api/agents            who's in the chat (and who was earlier), plus Lens
    GET  /api/events            chat messages as they happen (Server-Sent Events)
    GET  /api/messages/recent   the last messages, for a page that just opened
    POST /api/activity          the app pings this while you type, so the small talk holds off (204)
    POST /api/chat              your message: a group chat or a 1-on-1 with an agent
    GET  /api/context           what the chat knows about the desk right now (and who can be @mentioned)
    POST /api/chat_todos        an agent's to-do list, as the app shows it
    POST /api/chat_todos_sync   every agent's to-do list
    POST /api/widget_event      you opened (or used) an agent's panel
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import StreamingResponse

from desktalk.core.chat.message_generator import UnknownChat
from desktalk.web.deps import Desk, Streams
from desktalk.web.schemas import ChatMessage, Ok, PanelEvent, PanelList, PanelLists
from desktalk.web.streams import SSE_HEADERS, SSE_TYPE

LENS = {'id': 'lens', 'name': 'Lens', 'art': 'art/lens.svg', 'color': 'coral',
        'about': 'The camera. Speaks up when the view needs fixing.'}
UNKNOWN_CHAT = 'unknown chat'
MESSAGE_ID_LENGTH = 12          # as the MessageHub makes them

router = APIRouter()


@router.get('/api/agents')
def agents(desk: Desk) -> dict[str, Any]:
    return {'agents': desk.roster.snapshot(), 'lens': LENS}


@router.get('/api/events')
def events(desk: Desk, streams: Streams) -> StreamingResponse:
    web = desk.config.web
    body = streams.chat_messages(desk.hub, retry_ms=web.live_retry_ms, keepalive_sec=web.live_keepalive_sec)
    return StreamingResponse(body, media_type=SSE_TYPE, headers=SSE_HEADERS)


@router.get('/api/messages/recent')
def recent_messages(desk: Desk) -> dict[str, Any]:
    return {'messages': desk.hub.recent()}


@router.post('/api/activity', status_code=204)
def activity(desk: Desk) -> Response:
    desk.desk.last_user_activity = desk.clock.now()
    return Response(status_code=204)


@router.post('/api/chat')
def chat(sent: ChatMessage, desk: Desk) -> dict[str, Any]:
    desk.desk.last_user_activity = desk.clock.now()
    message = (sent.message or '').strip()
    if not message:
        raise HTTPException(400, 'empty message')
    if desk.lost_items.is_lost_query(message):
        answer = desk.lost_items.answer(message)
        in_one_on_one = sent.chat != 'group' and desk.profiles.get(sent.chat) is not None
        return {'messages': [{'character': sent.chat if in_one_on_one else answer['speaker'], 'text': answer['text'],
                              'cta': answer['cta'], 'id': uuid.uuid4().hex[:MESSAGE_ID_LENGTH]}],
                'lost_object_lookup': True}
    try:
        return desk.generator.answer(sent.chat, message, sent.recent_messages or sent.history, widget=sent.widget)
    except UnknownChat:
        raise HTTPException(400, UNKNOWN_CHAT) from None


@router.get('/api/context')
def context(desk: Desk) -> dict[str, Any]:
    ctx = desk.generator.live_context()
    members = set(desk.roster.member_ids())
    return {'detected': ctx['detected'], 'dangers': ctx['dangers'], 'safety_state': ctx['safety_state'],
            'mention_options': [{'id': a['id'], 'name': a['name'], 'icon': a['icon'], 'object': a['object'],
                                 'detected': a['id'] in members} for a in desk.roster.snapshot()]}


@router.post('/api/chat_todos')
def chat_todos(sent: PanelList, desk: Desk) -> Ok:
    if sent.chat is None or desk.profiles.get(sent.chat) is None:
        raise HTTPException(400, UNKNOWN_CHAT)
    desk.generator.save_widget(sent.chat, sent.widget)
    return Ok()


@router.post('/api/chat_todos_sync')
def chat_todos_sync(sent: PanelLists, desk: Desk) -> Ok:
    for pid, widget in sent.by_chat.items():
        if isinstance(widget, dict):
            desk.generator.save_widget(pid, widget)      # an unknown agent or a bad list is skipped
    return Ok()


@router.post('/api/widget_event')
def widget_event(sent: PanelEvent, desk: Desk) -> Ok:
    if sent.chat is None or not desk.generator.note_panel_event(sent.chat, sent.action):
        raise HTTPException(400, UNKNOWN_CHAT)
    return Ok()
