"""
Every request the characterization tests send, and the status code each one gets today.

Each Case is one request to one of the two web servers, on one of the ready-made desks in conftest.py:

    fresh    nothing seen yet, no pictures; the AI works
    busy     the Pi has watched a laptop, a cup, keys, a book and a remote for 6 s with someone at the desk;
             one Pi upload arrived
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

from tests.conftest import blank_frame

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

# What the old raspberry_pi/camera_node.py sent with each picture (upload format 1, still accepted).
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

TODO_LIST = {'type': 'todo', 'items': [{'text': 'Send the report', 'done': False},
                                       {'text': 'Water the plant', 'done': True}]}
DATED_TODOS = {'type': 'todo', 'items': [{'text': 'Essay', 'done': False, 'due': '2026-10-30'},
                                         {'text': 'Call mum', 'done': False, 'due': 'next friday'}]}  # a bad date
OUTLINE = [[0, 0.5], [1, 0.5], [1, 1], [0, 1]]
# The developer page (/api/dev/..., only from this computer): the test its fake runner knows, and the pages that
# may ask (the server's own and the Vite dev server's, settings → web.port and dev.vite_port as in git).
DEV_TEST_ID = 'tests/unit/test_example.py::test_one'
OTHER_PAGE = {'Origin': 'https://example.com'}
VITE_PAGE = {'Origin': 'http://localhost:5173'}
# a web site whose name was pointed at 127.0.0.1: its page reaches the server from this computer, with no Origin
OTHER_ADDRESS = {'Host': 'evil.example:5000'}

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
    path: str                             # {snapshot_id} is filled in with a real photo id, {story_photo_id}
                                          # with the id of a picture in the cup's story
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


DEV_RUN = Step('POST', '/api/dev/runs', {'check': True})     # the developer page starts "Run everything"

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
    # An empty upload is a bad picture like any other (the Flask server crashed inside OpenCV: 500).
    Case('pi-frame-no-picture', 'POST', '/api/pi/frame', 400, desk='fresh', data=b'', headers=PI_HEADERS),
    Case('pi-frame-not-a-jpeg', 'POST', '/api/pi/frame', 400, desk='fresh', data=b'this is not a picture',
         headers=PI_HEADERS),
    Case('pi-frame-camera-is-phone', 'POST', '/api/pi/frame', 409, desk='phone', data=PICTURE, headers=PI_HEADERS),
    Case('live-after-pi-frame', 'GET', '/api/live', 200, desk='fresh',
         before=(Step('POST', '/api/pi/frame', data=PICTURE, headers=PI_HEADERS),),
         values={'camera': True, 'camera_source': 'pi'}),
    # What the Pi last said about itself is on the Log status now (the developer page reads it).
    Case('log-status-after-pi-frame', 'GET', '/api/log/status', 200, desk='fresh',
         before=(Step('POST', '/api/pi/frame', data=PICTURE, headers=PI_HEADERS),),
         values={'camera.frames_received': 1, 'camera.pi.last_frame_id': 'pi-000042',
                 'camera.pi.photos_stored': 42, 'camera.pi.disk_free_mb': 1234}),

    # ── Video and photos ──
    Case('video-feed-camera-0', 'GET', '/api/video_feed/0', 200, reply=MJPEG),
    Case('video-feed-raw', 'GET', '/api/video_feed_raw', 200, reply=MJPEG),
    Case('video-feed-raw-camera-0', 'GET', '/api/video_feed_raw/0', 200, reply=MJPEG),
    Case('video-feed-waits-for-first-picture', 'GET', '/api/video_feed/0', 200, desk='fresh', reply=MJPEG_WAITS),
    Case('latest-frame-raw', 'GET', '/api/latest_frame_raw/0', 200, reply=FILE, starts_with=JPEG_START),
    Case('latest-frame-raw-no-picture-yet', 'GET', '/api/latest_frame_raw/0', 404, desk='fresh', reply=STATUS),
    Case('snapshot', 'GET', '/api/snapshot/{snapshot_id}', 200, reply=FILE, starts_with=JPEG_START),
    Case('snapshot-unknown-id', 'GET', '/api/snapshot/nope', 404, reply=STATUS),
    Case('snapshots', 'GET', '/api/snapshots', 200),
    Case('snapshots-none-yet', 'GET', '/api/snapshots', 200, desk='fresh'),
    Case('snapshots-newest-one', 'GET', '/api/snapshots?limit=1', 200),

    # ── An object's story (the app page's story panel; any device on the Wi-Fi may ask) ──
    Case('object-story', 'GET', '/api/objects/cup/story', 200, values={'label': 'cup'}),
    Case('object-story-from-lan', 'GET', '/api/objects/cup/story', 200, from_ip=LAN_DEVICE),
    Case('object-story-never-seen', 'GET', '/api/objects/pen%2Fpencil/story', 404,
         values={'error': "The camera hasn't seen pen/pencil since it started."}),
    Case('object-photo', 'GET', '/api/objects/photo/{story_photo_id}', 200, reply=FILE, starts_with=JPEG_START),
    Case('object-photo-gone', 'GET', '/api/objects/photo/nope', 404, reply=STATUS),

    # ── Live status ──
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
    Case('health', 'GET', '/api/health', 200),
    Case('health-phone-camera', 'GET', '/api/health', 200, desk='phone', values={'pi': None}),

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
    Case('todos', 'GET', '/api/todos', 200, desk='fresh', values={'has_list': False, 'items': []}),
    Case('todos-after-sync', 'GET', '/api/todos', 200,
         before=(Step('POST', '/api/chat_todos_sync', {'by_chat': {'paper': DATED_TODOS}}),),
         values={'has_list': True, 'items': [{'text': 'Essay', 'done': False, 'due': '2026-10-30'},
                                             {'text': 'Call mum', 'done': False, 'due': None}]}),
    Case('widget-event', 'POST', '/api/widget_event', 200, json={'chat': 'cup', 'action': 'view'}),
    Case('widget-event-unknown-chat', 'POST', '/api/widget_event', 400, json={'chat': 'nobody', 'action': 'view'}),

    # ── Habits ──
    Case('habits-today', 'GET', '/api/habits/today', 200),
    Case('habits-today-nothing-seen', 'GET', '/api/habits/today', 200, desk='fresh'),
    Case('habits-log-glass', 'POST', '/api/habits/log', 200, json={'habit': 'hydration', 'action': 'glass'},
         values={'hydration.water_ml': 240}),
    Case('habits-log-food', 'POST', '/api/habits/log', 200,
         json={'habit': 'food', 'action': 'food', 'label': 'apple', 'healthy': True}),
    Case('habits-log-unknown-habit', 'POST', '/api/habits/log', 400, json={'habit': 'nope', 'action': 'x'}),
    Case('preferences', 'GET', '/api/preferences', 200),
    Case('preferences-put', 'PUT', '/api/preferences', 200,
         json={'wake_time': '06:30', 'bed_time': '23:00', 'quiet_enabled': True}),
    Case('preferences-put-bad-time', 'PUT', '/api/preferences', 400, json={'wake_time': 'noon'}),
    Case('preferences-put-sensitivity', 'PUT', '/api/preferences', 200, json={'sensitivity': 0},
         values={'preferences.sensitivity': 0}),
    Case('preferences-put-bad-sensitivity', 'PUT', '/api/preferences', 400, json={'sensitivity': 'abc'}),
    Case('preferences-put-old-names', 'PUT', '/api/preferences', 200, json={'dnd_enabled': True, 'dnd_start_hour': 21},
         values={'preferences.quiet_enabled': True, 'preferences.quiet_start_hour': 21}),
    Case('preferences-after-put', 'GET', '/api/preferences', 200,
         before=(Step('PUT', '/api/preferences', {'wake_time': '06:30'}),), values={'wake_time': '06:30'}),

    # ── Event log ──
    Case('log-days', 'GET', '/api/log/days', 200, values={'today': '2026-10-21'}),
    Case('log-days-nothing-seen', 'GET', '/api/log/days', 200, desk='fresh'),
    Case('log-events', 'GET', '/api/log/events', 200),
    Case('log-events-alerts-only', 'GET', '/api/log/events?types=alert&limit=2', 200),
    Case('log-events-other-day', 'GET', '/api/log/events?day=2026-10-20', 200, values={'day': '2026-10-20'}),
    Case('log-events-bad-day', 'GET', '/api/log/events?day=yesterday', 200, values={'day': '2026-10-21'}),
    Case('log-events-bad-limit', 'GET', '/api/log/events?limit=all', 400),     # the Flask server crashed: 500
    Case('log-status', 'GET', '/api/log/status', 200),
    Case('log-status-nothing-seen', 'GET', '/api/log/status', 200, desk='fresh'),

    # ── Settings ──
    Case('rules', 'GET', '/api/rules', 200),
    Case('rules-put-outline', 'PUT', '/api/rules', 200, json={'desk_outline': OUTLINE}),
    Case('rules-put-outline-phone-camera', 'PUT', '/api/rules', 200, desk='phone', json={'desk_outline': OUTLINE}),
    Case('rules-after-put-outline', 'GET', '/api/rules', 200,
         before=(Step('PUT', '/api/rules', {'desk_outline': OUTLINE}),), values={'desk_outline': OUTLINE}),
    Case('rules-put-too-few-corners', 'PUT', '/api/rules', 400, json={'desk_outline': [[0, 0], [1, 0]]}),
    Case('rules-put-bad-outline', 'PUT', '/api/rules', 400, json={'desk_outline': 'the whole desk'}),
    Case('rules-put-empty', 'PUT', '/api/rules', 400, json={}),
    Case('ui-settings', 'GET', '/api/ui_settings', 200, values={'web.refresh_ms.live': 1500, 'lens_card.id': 'lens'}),

    # ── The developer page (dev_ui/ at /dev/, and /api/dev/...) ──
    Case('dev-page-not-built', 'GET', '/dev/', 200, desk='fresh', reply=FILE, starts_with=HTML_START),
    Case('dev-page-not-built-file', 'GET', '/dev/assets/index.js', 404, desk='fresh', reply=STATUS),
    Case('dev-page-no-slash', 'GET', '/dev', 307, desk='fresh', reply=STATUS),
    Case('dev-settings', 'GET', '/api/dev/settings', 200, desk='fresh',
         values={'checks': ['Fast tests', 'Lint', 'Types', 'Dead code', 'Page tests'], 'web_port': 5000,
                 'flicker_flips_per_min': 4, 'output_keep_kb': 2000, 'flips_window_min': 10,
                 'table_counts_every_sec': 30}),
    Case('dev-settings-other-computer', 'GET', '/api/dev/settings', 403, desk='fresh', from_ip=LAN_DEVICE),
    Case('dev-settings-other-web-page', 'GET', '/api/dev/settings', 403, desk='fresh', headers=OTHER_PAGE),
    Case('dev-settings-other-address', 'GET', '/api/dev/settings', 403, desk='fresh', headers=OTHER_ADDRESS),
    Case('dev-states', 'GET', '/api/dev/states?since=0', 200),
    Case('dev-states-nothing-seen', 'GET', '/api/dev/states', 200, desk='fresh'),
    Case('dev-states-bad-since', 'GET', '/api/dev/states?since=yesterday', 400),
    Case('dev-smoothing', 'GET', '/api/dev/smoothing', 200),
    Case('dev-smoothing-trace', 'GET', '/api/dev/smoothing/trace?label=cup', 200, values={'label': 'cup'}),
    Case('dev-smoothing-trace-never-seen', 'GET', '/api/dev/smoothing/trace?label=pen%2Fpencil', 200,
         values={'label': 'pen/pencil', 'steps': []}),
    Case('dev-smoothing-trace-no-label', 'GET', '/api/dev/smoothing/trace', 400),
    Case('dev-object-trace', 'GET', '/api/dev/objects/cup', 200, values={'label': 'cup'}),
    Case('dev-object-trace-never-seen', 'GET', '/api/dev/objects/pen%2Fpencil', 200,
         values={'label': 'pen/pencil', 'timeline': [], 'photos': []}),
    Case('dev-object-photo-gone', 'GET', '/api/dev/objects/photo/nope', 404, reply=STATUS),
    Case('dev-tests', 'GET', '/api/dev/tests', 200, desk='fresh'),
    Case('dev-run-check', 'POST', '/api/dev/runs', 200, desk='fresh', json={'check': True},
         values={'run_id': 'run-1'}),
    Case('dev-run-check-from-vite', 'POST', '/api/dev/runs', 200, desk='fresh', json={'check': True},
         headers=VITE_PAGE),
    Case('dev-run-check-other-web-page', 'POST', '/api/dev/runs', 403, desk='fresh', json={'check': True},
         headers=OTHER_PAGE),
    Case('dev-run-check-other-computer', 'POST', '/api/dev/runs', 403, desk='fresh', json={'check': True},
         from_ip=LAN_DEVICE),
    Case('dev-run-tests', 'POST', '/api/dev/runs', 200, desk='fresh', json={'tests': [DEV_TEST_ID]},
         before=(Step('GET', '/api/dev/tests'),)),
    Case('dev-run-unknown-test', 'POST', '/api/dev/runs', 400, desk='fresh', json={'tests': ['tests/x.py::nope']},
         before=(Step('GET', '/api/dev/tests'),)),
    Case('dev-run-nothing', 'POST', '/api/dev/runs', 400, desk='fresh', json={}),
    Case('dev-run-already-going', 'POST', '/api/dev/runs', 409, desk='fresh', json={'check': True},
         before=(DEV_RUN,), values={'error': 'A run is already going.'}),
    Case('dev-run-status', 'GET', '/api/dev/runs/run-1?from=0', 200, desk='fresh', before=(DEV_RUN,),
         values={'state': 'passed'}),
    Case('dev-run-status-unknown', 'GET', '/api/dev/runs/run-9', 404, desk='fresh'),
    Case('dev-run-status-bad-from', 'GET', '/api/dev/runs/run-1?from=-1', 400, desk='fresh', before=(DEV_RUN,)),
    Case('dev-run-stop', 'POST', '/api/dev/runs/run-1/stop', 200, desk='fresh', before=(DEV_RUN,)),
    Case('dev-db', 'GET', '/api/dev/db', 200, desk='fresh', values={'databases': ['object_states']}),
    Case('dev-db-tables', 'GET', '/api/dev/db/object_states/tables', 200),
    Case('dev-db-tables-unknown', 'GET', '/api/dev/db/nope/tables', 404),
    Case('dev-db-query', 'POST', '/api/dev/db/object_states/query', 200,
         json={'sql': 'SELECT object, type FROM events ORDER BY ts LIMIT 3'}),
    Case('dev-db-query-writes', 'POST', '/api/dev/db/object_states/query', 400, json={'sql': 'DELETE FROM events'}),
    Case('dev-db-query-mistake', 'POST', '/api/dev/db/object_states/query', 400, json={'sql': 'SELEC 1'}),
    Case('dev-db-query-no-sql', 'POST', '/api/dev/db/object_states/query', 400, json={}),
    Case('dev-db-query-unknown', 'POST', '/api/dev/db/nope/query', 404, json={'sql': 'SELECT 1'}),
    Case('dev-files', 'GET', '/api/dev/files', 200),
    Case('dev-files-preview-database', 'GET', '/api/dev/files/preview?path=object_states.sqlite', 200),
    Case('dev-files-preview-outside', 'GET', '/api/dev/files/preview?path=../.env', 400),
    Case('dev-files-preview-missing', 'GET', '/api/dev/files/preview?path=nothing-here.txt', 404),
    Case('dev-files-preview-other-address', 'GET', '/api/dev/files/preview?path=preferences.json', 403,
         headers=OTHER_ADDRESS),

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
    # The Flask server had an automatic /static/ route here, for a folder that doesn't exist; it's gone.
    Case('phone-static-folder', 'GET', '/static/style.css', 404, desk='phone', server='phone', reply=STATUS),
    Case('phone-config', 'GET', '/api/phone/config', 200, desk='phone', server='phone'),
    Case('phone-frame', 'POST', '/api/phone/frame', 200, desk='phone', server='phone', data=PICTURE,
         headers=PHONE_HEADERS),
    Case('phone-frame-no-tilt', 'POST', '/api/phone/frame', 200, desk='phone', server='phone', data=PICTURE,
         headers={'Content-Type': 'image/jpeg', **KEY}),
    Case('phone-frame-wrong-key', 'POST', '/api/phone/frame', 403, desk='phone', server='phone', data=PICTURE,
         headers={**PHONE_HEADERS, **WRONG_KEY}),
    Case('phone-frame-no-picture', 'POST', '/api/phone/frame', 400, desk='phone', server='phone', data=b'',
         headers=PHONE_HEADERS),     # a bad picture, like the Pi's (the Flask server crashed: 500)
    Case('phone-frame-not-a-jpeg', 'POST', '/api/phone/frame', 400, desk='phone', server='phone',
         data=b'this is not a picture', headers=PHONE_HEADERS),
    Case('phone-stop', 'POST', '/api/phone/stop', 200, desk='phone', server='phone', headers=KEY),
    Case('phone-stop-wrong-key', 'POST', '/api/phone/stop', 403, desk='phone', server='phone', headers=WRONG_KEY),
]


def cases_of(*kinds: str) -> list[Case]:
    return [c for c in CASES if c.reply in kinds]


def case(case_id: str) -> Case:
    return next(c for c in CASES if c.id == case_id)
