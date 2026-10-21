"""
Every request the characterization tests send, and the status code each one gets today.

Each Case is one request to one of the two web servers, on one of the ready-made desks in conftest.py:

    fresh    nothing seen yet, no pictures; the AI works
    busy     the Pi has watched a laptop, a cup, keys, a book and a remote for 6 s with someone at the desk;
             one Pi upload arrived; the vision AI noticed nail biting
    no_ai    like busy, but there is no AI key
    ai_down  like busy, but every AI call fails
    phone    the phone is the camera (camera.source: phone) and has sent one picture

What gets checked depends on `reply`:

    JSON      status + content type + a few headers + the JSON shape (saved in snapshots/<id>.json)
    FILE      status + content type + a few headers (saved) + the first bytes of the body
    EMPTY     status + an empty body
    STATUS    status only (error pages nobody reads)
    SSE, MJPEG, MJPEG_WAITS   live streams, see test_streams.py

`before` requests are sent first (they must succeed); `values` pins a few exact values that matter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import cv2
from conftest import blank_frame

from .client import THIS_COMPUTER

JSON, FILE, EMPTY, STATUS = 'json', 'file', 'empty', 'status'
SSE, MJPEG, MJPEG_WAITS = 'sse', 'mjpeg', 'mjpeg-waits'
SNAPSHOT_KINDS = {JSON, FILE, SSE, MJPEG, MJPEG_WAITS}     # the kinds that keep a file in snapshots/
SSE_MESSAGE_SNAPSHOT = 'events-chat-message'               # the shape of one chat message sent over SSE

PAIR_KEY = 'testkey'                  # the pairing key make_app() gives the app
LAN_DEVICE = '192.168.1.50'           # another device on the Wi-Fi (not this computer)
PHONE_URL = f'https://192.168.1.20:5443/phone?key={PAIR_KEY}'
JPEG_START = b'\xff\xd8\xff'
HTML_START = b'<!DOCTYPE html>'


def _jpeg() -> bytes:
    ok, buf = cv2.imencode('.jpg', blank_frame())
    assert ok
    return buf.tobytes()


PICTURE = _jpeg()                     # a real 640x480 JPEG

# What raspberry_pi/camera_node.py sends with each picture (Uploader._post).
PI_HEADERS = {'Content-Type': 'image/jpeg', 'X-Desk-Key': PAIR_KEY, 'X-Frame-Id': 'pi-000042',
              'X-Taken-At': '1792569600.250', 'X-Pi-Camera': 'picamera2', 'X-Pi-Photos': '42',
              'X-Pi-Free-MB': '1234', 'X-Pi-Last-Cleanup': '1792569000', 'X-Lux': '312.5'}
# A USB camera on the Pi: no light level, and the Pi sends blank counts when it doesn't know them.
PI_USB_HEADERS = {**{k: v for k, v in PI_HEADERS.items() if k != 'X-Lux'},
                  'X-Pi-Camera': 'usb', 'X-Pi-Photos': '', 'X-Pi-Free-MB': ''}
# What phone_camera/camera.js sends with each picture.
PHONE_HEADERS = {'Content-Type': 'image/jpeg', 'X-Desk-Key': PAIR_KEY, 'X-Desk-Rotation': '90',
                 'X-Desk-Gravity': '0.12,-9.71,0.85'}
KEY = {'X-Desk-Key': PAIR_KEY}
WRONG_KEY = {'X-Desk-Key': 'not-the-key'}

# What safety_pipeline/messaging.py posts to /api/safety.
DANGER_ALERT = {'type': 'danger_alert', 'message': 'Safety Alert: the cup is close to the laptop.',
                'timestamp': 1792569600.0,
                'pairs': [{'objects': ['cup', 'laptop'], 'distance_px': 42.5, 'threshold_px': 80}]}
SAFE_STATUS = {'type': 'safe_status', 'message': 'All monitored object distances are within safe range.',
               'timestamp': 1792569600.0}

TODO_LIST = {'type': 'todo', 'items': [{'text': 'Send the report', 'done': False},
                                       {'text': 'Water the plant', 'done': True}]}
OUTLINE = [[0, 0.5], [1, 0.5], [1, 1], [0, 1]]
HISTORY = [{'role': 'user', 'text': 'morning!'},
           {'role': 'assistant', 'name': 'Cup', 'text': 'Morning! Drink me sometime?'}]


@dataclass(frozen=True)
class Step:
    """A request sent before the one being checked (it must succeed)."""
    method: str
    path: str
    json: Any = None
    data: bytes | None = None
    headers: dict = field(default_factory=dict)
    server: str = 'main'


@dataclass(frozen=True)
class Case:
    id: str                               # also the snapshot file name
    method: str
    path: str                             # {snapshot_id} is filled in with a real photo id
    status: int
    desk: str = 'busy'
    server: str = 'main'                  # main (web_app/ and the Pi) or phone (the phone camera page)
    reply: str = JSON
    json: Any = None
    data: bytes | None = None
    headers: dict = field(default_factory=dict)
    from_ip: str = THIS_COMPUTER
    before: tuple[Step, ...] = ()
    vary: tuple[str, ...] = ()            # JSON paths that may also be null / [] (see shapes.differences)
    values: dict = field(default_factory=dict)   # dotted JSON path → exact value
    starts_with: bytes = b''              # FILE replies: the body starts with these bytes


CASES = [
    # ── The browser app's page and files (web_app/) ──
    Case('index', 'GET', '/', 200, desk='fresh', reply=FILE, starts_with=HTML_START),
    Case('static-script', 'GET', '/app.js', 200, desk='fresh', reply=FILE),
    Case('static-style', 'GET', '/style.css', 200, desk='fresh', reply=FILE),
    Case('static-art', 'GET', '/art/desk.svg', 200, desk='fresh', reply=FILE, starts_with=b'<svg'),
    Case('static-font', 'GET', '/fonts/bricolage-grotesque.woff2', 200, desk='fresh', reply=FILE,
         starts_with=b'wOF2'),
    Case('static-missing-file', 'GET', '/no-such-file.js', 404, desk='fresh', reply=STATUS),
    Case('trailing-slash', 'GET', '/api/live/', 404, desk='fresh', reply=STATUS),

    # ── The Raspberry Pi ──
    Case('pi-config', 'GET', '/api/pi/config', 200, desk='fresh', headers=KEY),
    Case('pi-config-wrong-key', 'GET', '/api/pi/config', 403, desk='fresh', headers=WRONG_KEY),
    Case('pi-config-no-key', 'GET', '/api/pi/config', 403, desk='fresh'),
    Case('pi-frame', 'POST', '/api/pi/frame', 200, desk='fresh', data=PICTURE, headers=PI_HEADERS),
    Case('pi-frame-usb-camera', 'POST', '/api/pi/frame', 200, desk='fresh', data=PICTURE, headers=PI_USB_HEADERS),
    Case('pi-frame-wrong-key', 'POST', '/api/pi/frame', 403, desk='fresh', data=PICTURE,
         headers={**PI_HEADERS, **WRONG_KEY}),
    # An empty upload crashes inside OpenCV today (500 with an HTML page), where 400 was probably meant.
    Case('pi-frame-no-picture', 'POST', '/api/pi/frame', 500, desk='fresh', reply=STATUS, data=b'',
         headers=PI_HEADERS),
    Case('pi-frame-not-a-jpeg', 'POST', '/api/pi/frame', 400, desk='fresh', data=b'this is not a picture',
         headers=PI_HEADERS),
    Case('pi-frame-camera-is-phone', 'POST', '/api/pi/frame', 409, desk='phone', data=PICTURE, headers=PI_HEADERS),
    Case('live-after-pi-frame', 'GET', '/api/live', 200, desk='fresh',
         before=(Step('POST', '/api/pi/frame', data=PICTURE, headers=PI_HEADERS),),
         values={'camera': True, 'camera_info.frames_received': 1, 'camera_info.pi.last_frame_id': 'pi-000042',
                 'camera_info.pi.photos_stored': 42, 'camera_info.pi.disk_free_mb': 1234}),
    Case('phone-info-pi-camera', 'GET', '/api/phone/info', 200),
    Case('phone-info-phone-camera', 'GET', '/api/phone/info', 200, desk='phone', values={'url': PHONE_URL}),
    Case('phone-info-phone-camera-from-lan', 'GET', '/api/phone/info', 200, desk='phone', from_ip=LAN_DEVICE,
         values={'url': '', 'qr_svg': None}),

    # ── Video and photos ──
    Case('video-feed', 'GET', '/api/video_feed', 200, reply=MJPEG),
    Case('video-feed-camera-0', 'GET', '/api/video_feed/0', 200, reply=MJPEG),
    Case('video-feed-raw', 'GET', '/api/video_feed_raw', 200, reply=MJPEG),
    Case('video-feed-raw-camera-0', 'GET', '/api/video_feed_raw/0', 200, reply=MJPEG),
    Case('video-feed-waits-for-first-picture', 'GET', '/api/video_feed', 200, desk='fresh', reply=MJPEG_WAITS),
    Case('latest-frame', 'GET', '/api/latest_frame/0', 200, reply=FILE, starts_with=JPEG_START),
    Case('latest-frame-no-picture-yet', 'GET', '/api/latest_frame/0', 404, desk='fresh', reply=STATUS),
    Case('latest-frame-raw', 'GET', '/api/latest_frame_raw/0', 200, reply=FILE, starts_with=JPEG_START),
    Case('latest-frame-raw-no-picture-yet', 'GET', '/api/latest_frame_raw/0', 404, desk='fresh', reply=STATUS),
    Case('snapshot', 'GET', '/api/snapshot/{snapshot_id}', 200, reply=FILE, starts_with=JPEG_START),
    Case('snapshot-unknown-id', 'GET', '/api/snapshot/nope', 404, reply=STATUS),
    Case('snapshots', 'GET', '/api/snapshots', 200),
    Case('snapshots-none-yet', 'GET', '/api/snapshots', 200, desk='fresh'),

    # ── Live status ──
    Case('detections', 'GET', '/api/detections', 200),
    Case('detections-nothing-seen', 'GET', '/api/detections', 200, desk='fresh'),
    Case('safety', 'GET', '/api/safety', 200),
    Case('safety-nothing-seen', 'GET', '/api/safety', 200, desk='fresh'),
    Case('safety-post-danger-alert', 'POST', '/api/safety', 200, json=DANGER_ALERT),
    Case('safety-post-safe-status', 'POST', '/api/safety', 200, json=SAFE_STATUS),
    Case('safety-after-danger-alert', 'GET', '/api/safety', 200, before=(Step('POST', '/api/safety', DANGER_ALERT),),
         values={'state': 'DANGEROUS'}),
    Case('live', 'GET', '/api/live', 200),
    Case('live-from-lan', 'GET', '/api/live', 200, from_ip=LAN_DEVICE),
    Case('live-nothing-seen', 'GET', '/api/live', 200, desk='fresh'),
    Case('live-no-ai', 'GET', '/api/live', 200, desk='no_ai', values={'ai.ready': False}),
    Case('live-ai-down', 'GET', '/api/live', 200, desk='ai_down', values={'ai.ready': True}),
    Case('live-phone-camera', 'GET', '/api/live?qr=1', 200, desk='phone', values={'phone.url': PHONE_URL}),
    Case('live-phone-camera-no-qr', 'GET', '/api/live', 200, desk='phone', values={'phone.qr_svg': None}),
    Case('live-phone-camera-from-lan', 'GET', '/api/live?qr=1', 200, desk='phone', from_ip=LAN_DEVICE,
         values={'phone.url': '', 'phone.qr_svg': None}),
    Case('live-after-phone-stop', 'GET', '/api/live', 200, desk='phone',
         before=(Step('POST', '/api/phone/stop', headers=KEY, server='phone'),), values={'camera': False}),
    Case('status', 'GET', '/api/status', 200),
    Case('status-nothing-seen', 'GET', '/api/status', 200, desk='fresh'),

    # ── Agents and chat ──
    Case('agents', 'GET', '/api/agents', 200),
    Case('agents-nobody-yet', 'GET', '/api/agents', 200, desk='fresh'),
    Case('events', 'GET', '/api/events', 200, reply=SSE),
    Case('messages-recent', 'GET', '/api/messages/recent', 200),
    Case('messages-recent-none-yet', 'GET', '/api/messages/recent', 200, desk='fresh'),
    Case('activity', 'POST', '/api/activity', 204, reply=EMPTY),
    Case('activity-beacon', 'POST', '/api/activity', 204, reply=EMPTY, data=b'',
         headers={'Content-Type': 'text/plain;charset=UTF-8'}),
    Case('chat-group-two-mentions', 'POST', '/api/chat', 200,
         json={'chat': 'group', 'message': '@Computer @Cup how is everyone?', 'recent_messages': HISTORY}),
    Case('chat-group-nobody-on-desk', 'POST', '/api/chat', 200, desk='fresh',
         json={'chat': 'group', 'message': 'hello?'}),
    Case('chat-group-no-ai', 'POST', '/api/chat', 200, desk='no_ai',
         json={'chat': 'group', 'message': '@Computer hello?'}),
    Case('chat-lost-item', 'POST', '/api/chat', 200, json={'chat': 'group', 'message': 'have you seen my keys?'}),
    Case('chat-one-on-one', 'POST', '/api/chat', 200,
         json={'chat': 'computer', 'message': 'how am I doing?', 'recent_messages': HISTORY}),
    Case('chat-one-on-one-logs-water', 'POST', '/api/chat', 200,
         json={'chat': 'cup', 'message': 'I just drank some water'}, values={'habits_changed': True}),
    Case('chat-one-on-one-adds-todo', 'POST', '/api/chat', 200,
         json={'chat': 'paper', 'message': 'please add buy milk to my list', 'widget': TODO_LIST},
         values={'actions': [{'action': 'add_todo', 'text': 'Buy milk'}], 'habits_changed': False}),
    Case('chat-one-on-one-no-ai', 'POST', '/api/chat', 200, desk='no_ai', json={'chat': 'cup', 'message': 'hi'}),
    Case('chat-one-on-one-ai-down', 'POST', '/api/chat', 200, desk='ai_down', json={'chat': 'cup', 'message': 'hi'}),
    Case('chat-empty-message', 'POST', '/api/chat', 400, json={'chat': 'group', 'message': '   '}),
    Case('chat-unknown-chat', 'POST', '/api/chat', 400, json={'chat': 'nobody', 'message': 'hi'}),
    Case('chat-todos', 'POST', '/api/chat_todos', 200, json={'chat': 'paper', 'widget': TODO_LIST}),
    Case('chat-todos-unknown-chat', 'POST', '/api/chat_todos', 400, json={'chat': 'nobody', 'widget': TODO_LIST}),
    Case('chat-todos-sync', 'POST', '/api/chat_todos_sync', 200, json={'by_chat': {'paper': TODO_LIST}}),
    Case('chat-todos-sync-bad-payload', 'POST', '/api/chat_todos_sync', 400, json={'by_chat': ['paper']}),
    Case('widget-event', 'POST', '/api/widget_event', 200, json={'chat': 'cup', 'action': 'view'}),
    Case('widget-event-unknown-chat', 'POST', '/api/widget_event', 400, json={'chat': 'nobody', 'action': 'view'}),
    Case('context', 'GET', '/api/context', 200),
    Case('context-nothing-seen', 'GET', '/api/context', 200, desk='fresh'),

    # ── Habits ──
    Case('habits-today', 'GET', '/api/habits/today', 200),
    Case('habits-today-nothing-seen', 'GET', '/api/habits/today', 200, desk='fresh'),
    Case('habits-log-glass', 'POST', '/api/habits/log', 200, json={'habit': 'hydration', 'action': 'glass'},
         values={'hydration.water_ml': 240}),
    Case('habits-log-food', 'POST', '/api/habits/log', 200,
         json={'habit': 'food', 'action': 'food', 'label': 'apple', 'healthy': True}),
    Case('habits-log-unknown-habit', 'POST', '/api/habits/log', 400, json={'habit': 'nope', 'action': 'x'}),
    Case('habit-log', 'GET', '/api/habit_log', 200),
    Case('habit-log-one-category', 'GET', '/api/habit_log?category=posture_bad&limit=5', 200),
    Case('habit-log-bad-limit', 'GET', '/api/habit_log?limit=lots', 500, reply=STATUS),
    Case('daily-stats', 'GET', '/api/daily_stats', 200),
    Case('preferences', 'GET', '/api/preferences', 200),
    Case('preferences-old-url', 'GET', '/api/habit_policy', 200),
    Case('preferences-put', 'PUT', '/api/preferences', 200,
         json={'wake_time': '06:30', 'bed_time': '23:00', 'quiet_enabled': True}),
    Case('preferences-put-bad-time', 'PUT', '/api/preferences', 400, json={'wake_time': 'noon'}),
    Case('preferences-put-old-names', 'PUT', '/api/habit_policy', 200, json={'dnd_enabled': True, 'dnd_start_hour': 21},
         values={'preferences.quiet_enabled': True, 'preferences.quiet_start_hour': 21}),
    Case('preferences-after-put', 'GET', '/api/preferences', 200,
         before=(Step('PUT', '/api/preferences', {'wake_time': '06:30'}),), values={'wake_time': '06:30'}),

    # ── Event log ──
    Case('log-days', 'GET', '/api/log/days', 200, values={'today': '2026-10-21'}),
    Case('log-days-nothing-seen', 'GET', '/api/log/days', 200, desk='fresh'),
    Case('log-events', 'GET', '/api/log/events', 200),
    Case('log-events-joins-only', 'GET', '/api/log/events?types=agent_joined&limit=2', 200),
    Case('log-events-other-day', 'GET', '/api/log/events?day=2026-10-20', 200, values={'day': '2026-10-20'}),
    Case('log-events-bad-day', 'GET', '/api/log/events?day=yesterday', 200, values={'day': '2026-10-21'}),
    Case('log-events-bad-limit', 'GET', '/api/log/events?limit=all', 500, reply=STATUS),
    Case('log-presence', 'GET', '/api/log/presence', 200),
    Case('log-presence-nothing-seen', 'GET', '/api/log/presence', 200, desk='fresh'),
    Case('log-detections', 'GET', '/api/log/detections?limit=1', 200),
    Case('log-detections-nothing-seen', 'GET', '/api/log/detections', 200, desk='fresh'),
    Case('log-status', 'GET', '/api/log/status', 200),
    Case('log-status-nothing-seen', 'GET', '/api/log/status', 200, desk='fresh'),
    Case('log-compact', 'POST', '/api/log/compact', 200),
    Case('log-status-after-compact', 'GET', '/api/log/status', 200, before=(Step('POST', '/api/log/compact'),),
         values={'today.pictures_waiting': 0}),

    # ── Settings ──
    Case('rules', 'GET', '/api/rules', 200),
    Case('rules-put-outline', 'PUT', '/api/rules', 200, json={'desk_outline': OUTLINE}),
    Case('rules-put-outline-phone-camera', 'PUT', '/api/rules', 200, desk='phone', json={'desk_outline': OUTLINE}),
    Case('rules-after-put-outline', 'GET', '/api/rules', 200,
         before=(Step('PUT', '/api/rules', {'desk_outline': OUTLINE}),), values={'desk_outline': OUTLINE}),
    Case('rules-put-too-few-corners', 'PUT', '/api/rules', 400, json={'desk_outline': [[0, 0], [1, 0]]}),
    Case('rules-put-bad-outline', 'PUT', '/api/rules', 400, json={'desk_outline': 'the whole desk'}),
    Case('rules-put-empty', 'PUT', '/api/rules', 400, json={}),
    Case('scenarios', 'GET', '/api/scenarios', 200),
    Case('scenarios-nothing-seen', 'GET', '/api/scenarios', 200, desk='fresh'),

    # ── The phone camera's own server (phone_camera/) ──
    Case('phone-page', 'GET', '/', 200, desk='phone', server='phone', reply=FILE, starts_with=HTML_START),
    Case('phone-page-phone', 'GET', '/phone', 200, desk='phone', server='phone', reply=FILE, starts_with=HTML_START),
    Case('phone-page-phone-slash', 'GET', '/phone/', 200, desk='phone', server='phone', reply=FILE,
         starts_with=HTML_START),
    Case('phone-script', 'GET', '/phone/camera.js', 200, desk='phone', server='phone', reply=FILE),
    Case('phone-style', 'GET', '/phone/style.css', 200, desk='phone', server='phone', reply=FILE),
    Case('phone-missing-file', 'GET', '/phone/nope.js', 404, desk='phone', server='phone', reply=STATUS),
    Case('phone-art', 'GET', '/art/desk.svg', 200, desk='phone', server='phone', reply=FILE, starts_with=b'<svg'),
    Case('phone-font', 'GET', '/fonts/bricolage-grotesque.woff2', 200, desk='phone', server='phone', reply=FILE,
         starts_with=b'wOF2'),
    Case('phone-static-folder', 'GET', '/static/style.css', 404, desk='phone', server='phone', reply=STATUS),
    Case('phone-config', 'GET', '/api/phone/config', 200, desk='phone', server='phone'),
    Case('phone-frame', 'POST', '/api/phone/frame', 200, desk='phone', server='phone', data=PICTURE,
         headers=PHONE_HEADERS),
    Case('phone-frame-no-tilt', 'POST', '/api/phone/frame', 200, desk='phone', server='phone', data=PICTURE,
         headers={'Content-Type': 'image/jpeg', **KEY}),
    Case('phone-frame-wrong-key', 'POST', '/api/phone/frame', 403, desk='phone', server='phone', data=PICTURE,
         headers={**PHONE_HEADERS, **WRONG_KEY}),
    Case('phone-frame-no-picture', 'POST', '/api/phone/frame', 500, desk='phone', server='phone', reply=STATUS,
         data=b'', headers=PHONE_HEADERS),     # crashes inside OpenCV today, like the Pi's
    Case('phone-frame-not-a-jpeg', 'POST', '/api/phone/frame', 400, desk='phone', server='phone',
         data=b'this is not a picture', headers=PHONE_HEADERS),
    Case('phone-stop', 'POST', '/api/phone/stop', 200, desk='phone', server='phone', headers=KEY),
    Case('phone-stop-wrong-key', 'POST', '/api/phone/stop', 403, desk='phone', server='phone', headers=WRONG_KEY),
]


def cases_of(*kinds: str) -> list[Case]:
    return [c for c in CASES if c.reply in kinds]


def case(case_id: str) -> Case:
    return next(c for c in CASES if c.id == case_id)
