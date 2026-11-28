# API

Every web address (route) the Desk Talk servers answer, grouped by area: what it does, who calls it, and
who may call it. Where this page and the code disagree, the code is right.

There are 102 routes in all: 67 on the main server (24 of them for the developer page), 9 on the phone camera's server, 3 on
the detector service and 23 on the cloud server.

## Four servers

- Main server: The Pi, in the app container (a computer runs it with uv run python server.py, for development).
  Port: web.port in config/settings.yaml, 5000; the Pi opens it as APP_HOST_PORT in raspberry_pi/.env. What
  it's for: The app page (web_app/) and everything it asks for, the camera container's uploads, and (on a computer
  only) the developer page (dev_ui/, at /dev/).
- Phone camera server: A computer running server.py, in a thread of the main one. Only runs when the phone is the
  camera; not on the Pi. Port: camera.phone_port, 5443, https. What it's for: The phone camera page
  (phone_camera/). Phones only allow the camera on https pages, so it has its own certificate.
- Detector service: The Pi, in the detector container. Port: DETECTOR_PORT in raspberry_pi/.env, 8100, inside
  Docker's own network only: it is not open on the Pi, so only the other two containers reach it. What it's for: YOLO
  object detection for the Pi's camera program.
- Cloud server (optional): Wherever you run it, in Docker (cloud/). Port: WEB_PORT in cloud/.env, 8080, through
  nginx. What it's for: Keeps what a linked computer or Pi may send, shows it on its website, and writes a weekly
  report.

### Finding a route in the code

- The main server and the phone server: desktalk/web/app.py builds both (create_app, create_phone_app).
  The routes are in desktalk/web/routers/, one file per area, and the top of each file lists its URLs.
  routers/\_\_init\_\_.py says which files belong to which server (MAIN_SERVER, PHONE_SERVER). What the
  routes share (the pairing key check, "is this computer", the developer page's DevOnly) is in
  desktalk/web/deps.py; what a request body looks like is in desktalk/web/schemas.py.
- The detector service: raspberry_pi/detector_service/desktalk_detector/app.py. The request and reply
  shapes are in shared/desktalk_shared/detector_api.py.
- The cloud server: cloud/server/desktalk_cloud/routers/, one file per area, built by bootstrap.py. The shapes a Pi
  sends are in shared/desktalk_shared/cloud_api.py.
- Every route of the main and phone servers has a test case in tests/characterization/cases.py. The cloud server's
  are in tests/unit/test_cloud\_\*.py, and test_cloud_bootstrap.py checks each one needs the right login.

### What every reply of the main server has in common

- Errors are {"error": "plain words"}, never {"detail": ...}. A body that doesn't fit is a 400 (not
  422); a request bigger than web.max_request_mb is a 413.
- No other web site may read a reply or send a write. A reply carries no Access-Control-Allow-Origin unless the site is listed in web.cors_origins (empty by default; the app's own pages and npm run dev for the developer page need nothing in it), so a page from another site can't read it. A write whose Origin isn't the address it was sent to, and isn't in that list, gets 403. Programs send no Origin and pass, so the camera container's uploads and curl work.
- A request sent to a name the server doesn't go by gets 403, on the main server and the phone camera's. The Host (without the port) must be a number address (IPv4 or IPv6), localhost, a name ending in .local, the name in web.public_address, or one in web.allowed_hosts (empty by default; for a reverse proxy, and app on the Pi for the camera container). So a site that points its own name at the Pi's address by DNS ("rebinding") can't read or change anything.
- There is still no login, so anyone on the same Wi-Fi can call the routes marked "anyone on the network".
- A trailing slash is not forgiven: /api/live/ is a 404.
- Only GET routes exist for the page's files, so POST or PUT to a path that isn't a route gets 405.

### Who may call what

- anyone on the network: No check. The main server listens on every network the machine is on (web.host), so anyone
  on the same Wi-Fi can call these.
- pairing key: The request must carry the key in the X-Desk-Key header, or it gets 403. The app makes the key on its
  first start and keeps it (camera.pair_key_file; on the Pi in the pairing volume, which the camera container
  reads). On a computer the page shows it in the phone link (desktalk/web/pairing.py); it is never in a log. Checked
  by key_check in deps.py.
- this computer only: The route answers everyone, but leaves out some parts of its reply unless the request comes from
  127.0.0.1 or ::1 (FromHere in deps.py). Today only GET /api/live does this: the phone link (and its QR
  code) is only shown on the computer running server.py. On the Pi no one is "this computer" (the app is in its
  container), so it is never shown there.
- this computer and its own page only: Everyone else is turned away. A request that doesn't come from 127.0.0.1 or
  ::1 gets 403. So does one whose Host header isn't one of this computer's own addresses (localhost, 127.0.0.1
  or [::1], on web.port or dev.vite_port), so a web site that points its own name at this computer can't read
  the routes, and one whose Origin header is set to anything but those same addresses, so another web page open in
  your browser can't start a test run or a query. Checked by DevOnly in deps.py. Only the developer page's routes
  use it, and it is switched off on the Pi (dev.enabled: false).

## Main server

### Health

- GET /api/health: How every part is doing in one reply: background jobs, timed jobs, event consumers, camera, what
  the Pi said lately, detection, the AI. Called by: The developer page's System tab, for this computer's own cards
  (dev_ui/src/api.ts). People curl it ([raspberry_pi/README.md](../raspberry_pi/README.md));
  tests/e2e/test_full_stack.py; GET /api/dev/health (this server's own, or the Pi's own on a computer whose camera
  is the Pi). Access: anyone on the network
- GET /api/system/stats?since=: The system numbers of the machine the app runs on, kept for system_stats.keep_min
  minutes: {collecting, every_sec, keep_min, info, samples}, the samples newer than since. Each sample has the
  processor (busy, per core, speed, load), memory and swap, temperatures, the fan, voltages, under-voltage, disk space
  and disk speed, network and Wi-Fi, and the busiest programs. collecting is false (and samples empty) where
  system_stats.enabled is off: everywhere but the Pi. In desktalk/web/routers/system.py. Called by: GET
  /api/dev/pi_stats on a computer (the Pi tab); tests/pi/test_real_pi.py. Access: anyone on the network

### Chat

- GET /api/agents: Who is in the chat (and who was earlier), plus Lens's card. Called by: web_app/app.js,
  tests/e2e/test_page.py. Access: anyone on the network
- GET /api/events: Chat messages as they happen, as Server-Sent Events (see below). Called by: web_app/app.js
  (EventSource). Access: anyone on the network
- GET /api/messages/recent: The last messages, for a page that just opened. Called by: web_app/app.js. Access:
  anyone on the network
- POST /api/activity: "I'm typing", so the small talk holds off. Empty reply (204). Called by: web_app/app.js (a
  ping while you type, and a beacon when the page closes). Access: anyone on the network
- POST /api/chat: Your message, to the group or to one agent. Replies with the answers. Called by: web_app/app.js.
  Access: anyone on the network
- GET /api/todos: The to-do list the server keeps, with due dates. The app loads it when it opens (the server's copy
  wins). Called by: web_app/app.js. Access: anyone on the network
- POST /api/chat_todos: Save one agent's to-do list (for Book, also the server's list). Called by: web_app/app.js.
  Access: anyone on the network
- POST /api/chat_todos_sync: Save every agent's to-do list at once. Called by: web_app/app.js. Access: anyone on
  the network
- POST /api/widget_event: "I opened (or used) this agent's panel.". Called by: web_app/app.js. Access: anyone on
  the network

- GET /api/events: text/event-stream. First retry: 2000 (web.live_retry_ms), then one
  data: {json} line per chat message, and a : keepalive line when nothing came for
  web.live_keepalive_sec. The reply never ends; the browser reconnects by itself.
- POST /api/chat: JSON {"chat": "group" or an agent id, "message": "...", "recent_messages": [...],
  "widget": {...}}. Replies {"messages": [...]}. 400 for an empty message or an unknown chat.
- POST /api/chat_todos: {"chat": "\<agent id\>", "widget": {...}}. /api/chat_todos_sync:
  {"by_chat": {"\<agent id\>": {...}}}. /api/widget_event: {"chat": "\<agent id\>", "action": "view"}.
  All three reply {"ok": true}. A to-do list is {"type": "todo", "items": [{"text", "done", "due"}]};
  due is a day like "2026-10-30" or left out. For the agent with the to-do panel (Book), the list also
  becomes the server's list: a bad due date is dropped (the to-do stays), and a to-do that was open and is now
  done is logged as a habit event (tasks / done).
- GET /api/todos: {"has_list": bool, "items": [{"text", "done", "due"}]}; due is "YYYY-MM-DD" or
  null. has_list is false until a list was first saved.

### Live

- GET /api/live: Only what the desk cam panel shows, in one request (the app asks every 1.5 s): camera,
  camera_source, camera_index, agents, detections.extras (the other things on the desk, not in the chat:
  [{"yolo_class", "ignored"}]), safety.explanations (the "Heads up" sentences), camera_issues, ai: {ready,
  problem}, and phone: {url, qr_svg} with the phone camera. ?qr=1 adds the phone link's QR code. Called by:
  web_app/app.js, tests/e2e/. Access: anyone on the network; the phone link and the QR code: this computer only
- GET /api/status: A short summary: running, camera, agents, safety state, uptime. Called by: web_app/app.js
  (once, when the page starts). Access: anyone on the network

### An object's story

- GET /api/objects/{label}/story: What happened to one kind of object since the camera started, step by step, oldest
  first, each step in plain words with the picture taken for it, plus the pictures of it kept from earlier runs (so
  after a restart it can still answer, with older pictures). pen%2Fpencil/story works for a word with a slash. 404
  {"error": "The camera hasn't seen ... yet."} (object_story.never_seen in config/prompts.yaml) when there's
  nothing to tell: no step this run and no kept picture. Called by: web_app/app.js (click something under "On the
  desk"). Access: anyone on the network
- GET /api/objects/photo/{photo_id}: One of its pictures (JPEG, without the box: the page draws it). 404 when it's
  gone. Called by: web_app/app.js. Access: anyone on the network
- GET /api/objects/states: Each object's state now, the changes since ?since=\<time\>, and now (the time by this
  server's clock): the same reply as GET /api/dev/states. Only names, states and times. Called by: GET
  /api/dev/states on a computer whose camera is the Pi. Access: anyone on the network
- GET /api/objects/smoothing: The objects that have a smoothing trace: the same reply as GET /api/dev/smoothing.
  Called by: GET /api/dev/smoothing on a computer whose camera is the Pi. Access: anyone on the network
- GET /api/objects/smoothing/trace: What the smoothing made of one object's last pictures, oldest first
  (confidence numbers and times, no pictures), and the rules this server smooths with: the same reply as GET
  /api/dev/smoothing/trace. ?label=pen%2Fpencil works; an object never seen gives an empty list. Called by: GET
  /api/dev/smoothing/trace on a computer whose camera is the Pi. Access: anyone on the network

- The reply is the same as GET /api/dev/objects/{label} (see the developer page below), read by the same code
  (desktalk/web/stories.py): {"label", "timeline": [step], "photos": [photo]}. Each step has what, the
  plain words for it from object_story in config/prompts.yaml ("appeared", "joined the chat", "moved",
  "went away", "left the chat" ...), and photo, the picture taken for it or null.

### Video and proof photos

- GET /api/video_feed/{camera}: Live video with the boxes drawn, as MJPEG. Called by: web_app/app.js (an \<img\>).
  Access: anyone on the network
- GET /api/video_feed_raw/{camera}: The same, without the boxes, as MJPEG. Called by: web_app/app.js. Access:
  anyone on the network
- GET /api/video_feed_raw: The same, for the camera that is sending, as MJPEG. Called by: People, from
  [benchmarks/README.md](../benchmarks/README.md); nothing in the code. Access: anyone on the network
- GET /api/latest_frame_raw/{camera}: The newest plain picture, once (JPEG). 404 before the first. Called by:
  web_app/app.js; mentioned in benchmarks/README.md. Access: anyone on the network
- GET /api/snapshot/{photo_id}: One proof photo (JPEG). 404 if it's gone. Called by: web_app/app.js. Access:
  anyone on the network
- GET /api/snapshots: The proof photos kept, newest first. ?limit=N for only the newest N (1 to
  snapshots.max_kept; not a whole number is a 400). Called by: web_app/app.js. Access: anyone on the network

- The video feeds are multipart/x-mixed-replace; boundary=frame: one JPEG part after another, sent when
  the picture changes and again every web.video_resend_sec. Nothing at all is sent, not even headers,
  until the first picture arrives. If the page goes away first, the reply is an empty 204.
- {camera} is a whole number; the first camera is 0.

### Habits

- GET /api/habits/today: Today's habits (water, breaks, light, phone, food). Called by: web_app/app.js,
  tests/e2e/test_page.py. Access: anyone on the network
- POST /api/habits/log: A habit button: a sip, a break, some sunlight, a snack. Called by: web_app/app.js. Access:
  anyone on the network
- GET /api/preferences: Wake time, bedtime, quiet hours, the sensitivity step, and whether that step may move by
  itself (auto_sensitivity). Called by: web_app/app.js. Access: anyone on the network
- PUT /api/preferences: Change them. Only the fields sent change. Called by: web_app/app.js. Access: anyone on the
  network

- POST /api/habits/log: {"habit": "hydration", "action": "glass", ...}. Any other field (ml,
  minutes, label ...) goes to the tracker. 400 for an unknown habit.
- PUT /api/preferences: {"wake_time": "06:30", "bed_time": ..., "quiet_enabled": ..., "quiet_start_hour":
  ..., "quiet_end_hour": ..., "sensitivity": 2, "auto_sensitivity": true}. sensitivity is the step of
  agents.sensitivity_steps, 0 (Picky) to 4 (Eager); a number past either end is kept at that end, and anything
  else is a 400. auto_sensitivity is the Settings page's "Adjust by itself" switch (it starts like
  sensitivity_review.enabled).
  Replies {"ok": true, "preferences": {...}}; 400 for a value that isn't valid.

### Event log

All take ?day=2026-10-21 (today when left out, or when it isn't a date). Lists take ?limit=: a whole
number, kept between 1 and the most web.list_sizes allows; anything else is a 400.

- GET /api/log/days: {"days", "today"}: the days that have a log, newest first (at most event_log.keep_days),
  and today. Called by: web_app/app.js. Access: anyone on the network
- GET /api/log/events: Only the events that matter to you, newest first: sips, breaks, food, finished to-dos,
  sunlight (habit), reminders (nudge), warnings (alert, with a plain subtitle like "near Computer") and camera
  problems (camera). ?types=habit,nudge,alert,camera picks some of those four (others are ignored); ?limit=
  counts only these. Called by: web_app/app.js. Access: anyone on the network
- GET /api/log/status: The log's folder, today's counts, the timed jobs, the event stream, the camera. Called by:
  the developer page's System tab, for this computer's own cards. Access: anyone on the network

### Settings

- GET /api/rules: The danger rules and the desk corners, plus the objects a zone can be set for. Called by:
  web_app/app.js. Access: anyone on the network
- PUT /api/rules: Change some of them. When the desk corners are marked, it also notes how the phone was tilted.
  Called by: web_app/app.js. Access: anyone on the network
- GET /api/ui_settings: The numbers and words the app page uses (refresh times, waits, list sizes, the sensitivity
  steps, the starter to-do and counters, Lens's card ...), so the page keeps no copies of its own. Its pi part is
  {fps, delete_every_min, public_address, address}: public_address is web.public_address (desktalk.local:5000
  on the Pi, empty on a computer), and when it is set the page says the Pi starts its own camera. Otherwise (on a
  computer) the page links to the Pi's own Desk Talk at address (dev.pi_address), since the Pi's camera sends its
  pictures there. Called by: web_app/app.js (on start), tests/e2e/test_page.py. Access: anyone on the network
- GET /api/sensitivity_review: Whether the sensitivity slider may move by itself, the reviewer's newest look back,
  and the moves it made (newest first, at most sensitivity_review.changes_kept). The Settings page shows the newest
  move's words and when. Called by: web_app/app.js (when the Settings page opens). Access: anyone on the network

- PUT /api/rules: {"danger_zones": ..., "edge_proximity": ..., "chain_reaction": ..., "temporal": ...,
  "desk_outline": [[x, y], ...]}. Only the parts sent change; each corner is 0 to 1 of the picture. An
  empty body is a 400. Replies {"ok": true}.
- GET /api/sensitivity_review: {"enabled": true, "last": {...} or null, "moves": [{...}, ...]}. Each look back
  is {"ts", "from_step", "to_step", "why", "watched_hours", "blinks", "misses", "step", "says"}: why is one of
  SENSITIVITY_WHYS in desktalk/domain/agents.py, step the name of the step it left the slider at, and says
  what the page shows for a move (ui.sensitivity_moved_pickier or ui.sensitivity_moved_easier in
  config/prompts.yaml, filled in), or null when it left the slider alone.

### Pi

The camera container (raspberry_pi/camera/) is the only caller. All three need the pairing key.

- GET /api/pi/config: The Pi's settings (pi: in config/settings.yaml, detector included), so they live in one
  place. Called by: The Pi's camera program (raspberry_pi/camera/settings.py), at start and every
  pi.settings_check_every_sec. Access: pairing key
- POST /api/pi/frame: One picture from the Pi, with what its detector found. Called by: The Pi's camera program
  (raspberry_pi/camera/uploader.py), about 3 times a second. Access: pairing key
- POST /api/pi/heartbeat: How the Pi is doing (temperature, disk, the detector), on a timer of its own, so it keeps
  coming when the pictures stop. Called by: The Pi's camera program (raspberry_pi/camera/heartbeat.py), every
  pi.heartbeat_every_sec. Access: pairing key

- POST /api/pi/frame: Content-Type: multipart/form-data with two parts, info (JSON: format version,
  picture id, when it was taken, light, how the Pi is doing, the detector's state ok / busy / off and its
  detections) and picture (the JPEG). This is the only format it takes. Read by
  desktalk_shared.wire.decode_upload. Replies {"ok": true, "fps": 3, "tip": null or "..."}.
- Errors: 400 not a JPEG or a bad info, 403 wrong key, 409 camera.source is not pi, 413 bigger than
  web.pi_upload_max_mb.
- POST /api/pi/heartbeat: JSON {"health": {...}}, the same "how the Pi is doing" as in a picture's info.
  Replies {"ok": true}. 400 for a health that doesn't fit, 403 wrong key, 409 camera.source is not pi.

### Cloud link

- GET /api/cloud/status: Whether this Pi (or computer) is linked to a cloud server: {"linked", "stopped",
  "last_sent_ts", "waiting"}. stopped means the server no longer knows it, so it needs linking again. waiting is
  how many events were still to go at its last try, so it doesn't count ones written since. Called by:
  web_app/app.js (the line in Settings). Access: anyone on the network
- GET /api/cloud/hello: "Who are you": {"desk_talk": "pi", "name", "linked"}. The cloud website's search for Pis
  on the home network knocks here on each address, from the person's browser, so this route alone answers with
  Access-Control-Allow-Origin: * (the page can read it from another address). Called by: the cloud website's
  Find Pis on my network button (cloud/web/src/api.ts, findPis), only to find Pis. Access: anyone on the network
- POST /api/cloud/link: "Link to this server with this code": body {"server", "code"}, where server is an http or
  https address. Trades the code with the server, saves the link and replies {"linked": true}. An error is
  {"error": "plain words", "kind": "a kind"}, the kind saying why for the cloud server: 400 bad_request for a bad
  body or a bad address, 400 refused for a code the server refused (its reason in the words); 403 not_caller when
  server is not a private home network address equal to the address the request came from; 409 already_linked,
  busy (another offer is being worked on) or not_saved (saving is off, so the link would not last); 502
  unavailable when the server could not be reached. Called by: the cloud website's Link button, through the cloud
  server (POST /api/pis/link). Access: anyone on the network

### Developer page (this computer only)

The routes behind the developer page (dev_ui/, see [its README](../dev_ui/README.md)), in
desktalk/web/routers/dev.py. They are only there when dev.enabled is on in config/settings.yaml;
switched off, every one of them, and /dev/, is a 404. They are never on the phone server. The only caller
is the developer page, through dev_ui/src/api.ts; each entry's "Called by" says which tab uses it.

They change nothing in the app, with two exceptions: starting and stopping a test run, and the Demo tab's
pretend warnings (/api/dev/scenarios), which are fired on purpose. Once its countdown is over, a pretend
warning goes out through the same parts as a real one (the chat, the Mac pop-up, the event log, where its alert
has "demo": true), and a pretend sip counts in today's water.

- GET /api/dev/settings: The page's numbers, so it keeps no copies: how often to ask again, the checks' names, the
  System tab's limits, the smoothing settings, the most rows a query shows, the data folder. Called by: The page, once
  when it opens. Access: this computer and its own page only
- GET /api/dev/states: Each object's state now, and the changes since ?since=\<time\>.
  On a computer whose camera is the Pi (camera.source: pi, web.public_address empty), the Pi's own GET /api/objects/states,
  fetched from dev.pi_address, with "from": "pi" and "address" added; anywhere else this server's own, with
  "from": "here" and "address": "". 502 with dev.pi_objects_unreachable's words when the Pi doesn't answer
  within dev.pi_stats_timeout_sec. Called by: Live tab. Access: this computer and its own page only
- GET /api/dev/smoothing: The objects that have a smoothing trace.
  On a computer whose camera is the Pi (camera.source: pi, web.public_address empty), the Pi's own GET /api/objects/smoothing,
  fetched from dev.pi_address, with "from": "pi" and "address" added; anywhere else this server's own, with
  "from": "here" and "address": "". 502 with dev.pi_objects_unreachable's words when the Pi doesn't answer
  within dev.pi_stats_timeout_sec. Called by: Live tab. Access: this computer and its own page only
- GET /api/dev/smoothing/trace: What the smoothing made of one object's last pictures, oldest first.
  ?label=pen%2Fpencil (a query, so a label with a slash works); an object never seen gives an empty list, not an
  error. On a computer whose camera is the Pi (camera.source: pi, web.public_address empty), the Pi's own GET /api/objects/smoothing/trace,
  fetched from dev.pi_address, with "from": "pi" and "address" added; anywhere else this server's own, with
  "from": "here" and "address": "". 502 with dev.pi_objects_unreachable's words when the Pi doesn't answer
  within dev.pi_stats_timeout_sec. Called by: Live tab. Access: this computer and its
  own page only
- GET /api/dev/objects/{label}: One kind of object's whole trace: its state changes since the camera started and its
  chat joins, leaves and refusals (with why), oldest first, each with the picture taken for it, and its pictures.
  pen%2Fpencil works for a word with a slash; an object never seen gives empty lists.
  On a computer whose camera is the Pi (camera.source: pi, web.public_address empty), the Pi's own GET /api/objects/{label}/story (its 404 for an object it never saw is the empty story here),
  fetched from dev.pi_address, with "from": "pi" and "address" added; anywhere else this server's own, with
  "from": "here" and "address": "". 502 with dev.pi_objects_unreachable's words when the Pi doesn't answer
  within dev.pi_stats_timeout_sec. Called by:
  Live tab. Access: this computer and its own page only
- GET /api/dev/objects/photo/{photo_id}: One trace picture (JPEG). 404 when it's gone. On a computer whose camera
  is the Pi, fetched from the Pi's own GET /api/objects/photo/{photo_id} at dev.pi_address (404 when it's gone
  there; 502 with dev.pi_objects_unreachable's words when the Pi doesn't answer, or answers with something that
  isn't a JPEG). Called by: Live tab. Access: this computer and its own page only
- GET /api/dev/tests: Every test the Checks tab lists (dev.collect_markers), slow ones marked. Called by: Checks
  tab. Access: this computer and its own page only
- POST /api/dev/runs: Start a run: every step in dev.checks, or some tests by id. One run at a time. Called by:
  Checks tab. Access: this computer and its own page only
- GET /api/dev/runs/{run_id}: How a run is going: its steps, each finished test, and the printout after
  ?from=\<offset\>. Called by: Checks tab, while a run goes. Access: this computer and its own page only
- POST /api/dev/runs/{run_id}/stop: Stop a run. Called by: Checks tab (Stop). Access: this computer and its own page
  only
- GET /api/dev/db: The SQLite databases' names. Called by: Database tab. Access: this computer and its own page only
- GET /api/dev/db/{name}/tables: One database's tables: columns, row count, the CREATE line. Called by: Database
  tab. Access: this computer and its own page only
- POST /api/dev/db/{name}/query: Run one query that only reads, on a read-only connection. Called by: Database tab.
  Access: this computer and its own page only
- GET /api/dev/files: Every file and folder in the data folder, with sizes and dates. Called by: Storage tab.
  Access: this computer and its own page only
- GET /api/dev/files/preview: The start of one file in the data folder (dev.file_preview_kb). ?path= is relative
  to the folder. Called by: Storage tab. Access: this computer and its own page only
- GET /api/dev/scenarios: The pretend warnings (dev.scenarios.items) and the ones counting down. Called by: Demo
  tab. Access: this computer and its own page only
- POST /api/dev/scenarios: Start a pretend warning counting down; it goes out like the real one when the countdown
  is over. Called by: Demo tab (Start). Access: this computer and its own page only
- DELETE /api/dev/scenarios/{id}: Stop one counting down, so it never goes out. Called by: Demo tab (Cancel).
  Access: this computer and its own page only
- GET /api/dev/pi_stats?since=: The Pi's GET /api/system/stats, fetched from dev.pi_address (the page can't ask
  the Pi itself: web.cors_origins is empty), with "address" added. 502 with dev.pi_stats_unreachable's words
  when the Pi doesn't answer within dev.pi_stats_timeout_sec. Called by: Pi tab. Access: this computer and its own
  page only
- GET /api/dev/health: How every part is doing, from the machine that knows. On a computer whose camera is the Pi
  (camera.source: pi, web.public_address empty), the Pi's own GET /api/health, fetched from dev.pi_address,
  with "from": "pi" and "address" added: the Pi's camera sends its pictures to the Pi's Desk Talk, so this
  computer's own camera health would say it isn't connected. Anywhere else (on the Pi itself, or another camera), this
  server's own GET /api/health with "from": "here" and "address": "". 502 with dev.pi_health_unreachable's
  words when the Pi doesn't answer within dev.pi_stats_timeout_sec, or sends something that isn't its health checks.
  The rest of the System tab (this computer's own parts and its event log) reads GET /api/health and GET
  /api/log/status straight. Called by: System tab (Health checks and Pi cards), Storage tab (Pi card). Access: this
  computer and its own page only
- GET /api/dev/pi_log: The lines of one of the Pi's logs (?source= a name in dev.pi_log.sources) numbered after
  ?after=\<n\>, and how the ssh connection to the Pi is doing. Asking starts ssh pi with that log's command and
  keeps it going; it stops once the tab stops asking. Called by: Pi log tab, while it is open. Access: this computer
  and its own page only
- GET /dev: Sends the browser on to /dev/, where the page's files are found. Called by: Your browser. Access:
  anyone on the network
- GET /dev/: The developer page (dev_ui/dist/index.html, from dev.ui_folder). Before npm run build has made
  it, a plain line saying how to build it (200). Called by: Your browser. Access: anyone on the network (only the page
  itself: what it shows comes from the routes above)
- GET /dev/{file}: The page's other files (its script and style, the font). Called by: Your browser. Access: anyone
  on the network

- Every /api/dev route turns away other computers and other web pages with 403 and {"error": ...}
  (the wording is in config/prompts.yaml under dev:), before anything runs.
- GET /api/dev/settings: {"poll_sec", "checks": [names], "health": {...}, "pi_hot": {"warning_c", "serious_c"}, "trace_pictures",
  "trace_events_kept", "trace_photos_kept", "trace_step_photos_kept", "sql_max_rows", "data_folder", "object_states": {"on_confidence", "off_confidence", "appear_sec",
  "gone_sec", "window_pictures", "vote_share"}, "scenarios": {"countdown_default_sec", "countdown_max_sec",
  "waiting_max"}, "pi_log": {"sources": [{"name", "label"}], "keep_lines"}}. object_states are this server's
  smoothing numbers, with the waits the sensitivity slider has now; a smoothing trace brings its own (rules).
- GET /api/dev/states: {"objects": {label: {"state", "since"}}, "watched_since", "changes": [{"ts",
  "label", "change"}], "now", "from", "address"}. now is when it answered, by the clock of the server that
  answered (the Pi's, from the Pi): the page measures "for 5 s" and the last minute's flips from it, so a Pi clock
  that is off from the computer's doesn't throw them. It, /api/dev/smoothing, /api/dev/smoothing/trace and
  /api/dev/objects/{label} each add "from": "pi" | "here" and "address", like /api/dev/health.
- GET /api/dev/smoothing: {"labels": [...]}. /api/dev/smoothing/trace: {"label", "steps": [...],
  "rules": {"on_confidence", "off_confidence", "appear_sec", "gone_sec", "window_pictures", "vote_share"}};
  each step has ts, conf, box_seen, votes_yes, votes_total, seen, center, phase, heading
  and waiting_sec. rules are the numbers the answering server smoothed with (the same keys as
  /api/dev/settings' object_states; the Pi's own, from the Pi, whose settings and sensitivity may differ): the
  chart draws its lines, vote mark and countdown with them.
- GET /api/dev/objects/{label} (and GET /api/objects/{label}/story): {"label", "timeline": [{"ts",
  "source": "state" | "chat" | "photo", "kind", "what", "confidence", "seen_for_sec", "on_desk", "step",
  "before", "after", "photo"}], "photos": [photo]}, where a photo is {"id", "ts", "why", "confidence",
  "box": [x1, y1, x2, y2], "size": [width, height]}. State rows are the newest object_story.events_kept
  changes (kind = appeared / removed / moved, before / after the states); chat rows are joined, left
  or refused, with the sensitivity step's name. A picture's why is appeared, joined, moved, removed
  (the newest picture once it's gone, with its last box: the empty spot) or every (one every
  object_story.photo_every_sec while it's there). Each step picture goes in the photo of the step of its
  kind nearest in time (within object_story.photo_match_sec); a picture that goes with no step is a row of
  its own (source "photo", kind its why). Only the newest object_story.timed_photos_kept every
  pictures and, apart from them, the newest object_story.step_photos_kept others are kept, so a long stay
  never pushes out how it got there. Here an object never seen gives empty lists (the app page's route a 404).
- GET /api/dev/tests: {"tests": [{"id", "file", "markers"}]}. The id is pytest's, e.g.
  tests/unit/test_x.py::test_y.
- POST /api/dev/runs: {"check": true} or {"tests": [ids]}. Replies {"run_id"}. 409 {"error": "A run
  is already going."} while one is; 400 for a test id that isn't in the list. The commands only come from
  dev.checks and dev.test_command, never from the request.
- GET /api/dev/runs/{run_id}: {"run_id", "what", "state", "started", "finished", "steps": [{"name",
  "state", "exit_code"}], "outcomes": [{"id", "outcome", "duration_sec", "message"}], "output",
  "output_end"}. state is running, passed, failed, stopped or timed out (after
  dev.run_timeout_min); ask again with ?from=\<output_end\> for only the new printout. 404 for a run it
  doesn't know (only the newest run is kept). POST .../stop replies {"ok": true}.
- GET /api/dev/db: {"databases": [...]}. .../tables: {"tables": [{"name", "columns", "rows",
  "sql"}]}.
- POST /api/dev/db/{name}/query: {"sql": "..."}. Replies {"columns", "rows", "truncated", "took_ms"},
  at most dev.sql_max_rows rows. 400 {"error": ...} for anything but one statement that only reads
  (DELETE, INSERT, ATTACH, two statements ...), or a query that ran longer than dev.sql_timeout_sec.
- GET /api/dev/files: {"files": [{"path", "bytes", "modified", "is_folder"}]}. .../preview:
  {"path", "text"}; 400 for a path outside the data folder (../.env, an absolute path, a symlink out) or
  a folder, never read; 404 when nothing is there.
- GET /api/dev/scenarios: {"scenarios": [{"name", "label", "fires"}], "waiting": [{"id", "name", "label",
  "fires_at", "seconds_left"}]}, the soonest first. fires is edge_warning, danger_zone, drink_reminder,
  camera_covered, sip or lost_object.
- POST /api/dev/scenarios: {"name": "cup_at_edge", "after_sec": 10}. Replies the new one counting down,
  {"id", "name", "label", "fires_at", "seconds_left"}. 400 {"error": ...} for a name that isn't in the
  list, one that names an agent or a lost item your settings don't have, a countdown below 0 or above dev.scenarios.countdown_max_sec, or when dev.scenarios.waiting_max
  are counting down already. The background job fires it (checked every dev.scenarios.check_every_sec).
- DELETE /api/dev/scenarios/{id}: {"ok": true}; 404 when it isn't counting down (never started, cancelled,
  or gone off already).
- GET /api/dev/pi_log?source=all&after=0: {"source", "state", "problem", "retry_at", "lines": [{"number",
  "text", "kind", "at"}], "end", "dropped"}. state is connecting, connected, waiting (to try again at
  retry_at, after a lost connection) or stopped; problem is the last thing that went wrong ("" once
  connected again). A line's kind is line (the Pi printed it, its errors too: ssh runs with a terminal),
  problem (ssh's own errors) or note (this computer's own: connecting, lost, trying again); at is when
  this computer got it. Ask again with ?after=\<end\> for only the new lines; dropped is true when some lines
  after the number asked for were let go already (only the newest dev.pi_log.keep_lines are kept). Asking for
  another log stops the one being read; asking for the same one after it stopped picks up where it left off,
  keeping the lines and their numbers. 404 for a source not in dev.pi_log.sources; 400 without a source or
  with after below 0.

### Pages and files

- GET /: The app page (web_app/index.html). Called by: Your browser. Access: anyone on the network
- GET /{file}: Any other file in web_app/ (app.js, style.css, art/, fonts/ ...). 404 if it isn't there.
  Called by: Your browser. Access: anyone on the network

This route is mounted last, so every route above (/dev/ included) wins over it.

## Phone camera server (https)

Built by create_phone_app; the routes are in desktalk/web/routers/phone.py. No cross-origin header.
Only the phone page (phone_camera/) calls these.

### Phone

- GET /api/phone/config: How many pictures a second to send, how big and how sharp, the highest real tilt, the size
  to ask the phone's camera for. Called by: phone_camera/camera.js. Access: anyone on the network
- POST /api/phone/frame: One picture from the phone. Called by: phone_camera/camera.js. Access: pairing key
- POST /api/phone/stop: "I pressed Stop on the phone.". Called by: phone_camera/camera.js. Access: pairing key

- POST /api/phone/frame: the body is the JPEG (Content-Type: image/jpeg). Optional headers:
  X-Desk-Gravity: x,y,z (how the phone is tilted, m/s²) and X-Desk-Rotation: 90 (how far the phone
  turned the picture). Replies {"ok": true, "fps": 3, "tip": "Too dark"}; tip is left out when there's
  nothing to say. Errors: 400 not a JPEG, 403 wrong key, 409 camera.source is not phone, 413 too big.

### Pages and files

- GET /, /phone, /phone/: The camera page (phone_camera/index.html). Called by: The phone's browser (from the
  QR code). Access: anyone on the network
- GET /phone/{file}: The page's files (camera.js, style.css ...). Called by: The phone's browser. Access: anyone
  on the network
- GET /art/{file}, /fonts/{file}: The drawings and fonts it shares with the app (web_app/art/,
  web_app/fonts/). Called by: The phone's browser. Access: anyone on the network

That makes 6 page routes (/, /phone, /phone/ and the three file folders), so 9 routes in all with the 3 above.

## Detector service (on the Pi)

Built by create_app in raspberry_pi/detector_service/desktalk_detector/app.py. The only caller is the
camera container (raspberry_pi/camera/detector_client.py), apart from Docker's health check inside the detector itself.
There is no pairing key: Docker doesn't open the port on the Pi at all, so only the other two containers can reach it, at
http://detector:8100. A curl http://127.0.0.1:8100 on the Pi, or from another computer, gets no answer; ask from inside
the container instead (the detector's [README](../raspberry_pi/detector_service/README.md) shows how). Errors are
{"detail": "plain words"} here, not {"error": ...}.

### Detector

- POST /detect: Finds the objects in one picture. Called by: The camera container, for each picture. Access: the
  other containers only
- GET /health: starting, loading, ready or error, which model answers, and a message. Called by: The camera
  container (while the detector is off); Docker's health check. Access: the other containers only
- PUT /model: Which model to run. It loads in the background; the old one keeps answering until the new one is
  ready. Called by: The camera container, on start and when the app's pi.detector settings change. Access: the other
  containers only

- POST /detect: the body is one JPEG. Replies {"detections": [...], "detect_ms", "width", "height",
  "model"}. Errors: 400 not a JPEG, 413 bigger than DETECTOR_MAX_PICTURE_MB, 429 still busy with another
  picture, 503 no model yet.
- PUT /model: JSON {"model": "yolo26m-objv1-150.pt", "format": "ncnn", "image_size": 640, "confidence":
  0.35, "half": false}. Replies like /health. 400 for settings that don't fit.
- GET /health: {"state", "model", "message", "detections_done", "last_detect_ms", ...}. state is error when there
  is no working model, and also when a model is loaded but the last picture failed: then model still names the
  loaded model and the message says the last picture failed. It goes back to ready by itself as soon as one picture works.
- FastAPI's own pages (/docs, /redoc, /openapi.json) are also switched on here; they are not counted.

## Cloud server

The optional server in cloud/. It has its own access rules:

- open: No login.
- limited: No login, but counted per address (limits.claim_tries in limits.claim_window_sec), then 429.
- Pi token: Authorization: Bearer \<token\>, the one scripts/link_cloud.py saved.
- login: The login cookie from logging in with Google.
- Pi token or owner: That Pi's own token, or the login of the person it belongs to.
- admin: The admin login cookie.

### For a Pi

- POST /v1/devices/claim: Trade a one-time code for a device id and a token. Access: limited
- POST /v1/events: Send events. Each goes through to_cloud_event() again. Replies how many were kept, repeated and
  refused. An empty list is fine: it updates when the Pi was last seen and, with caught_up: true, when it last had
  nothing left to send. Access: Pi token
- GET /v1/devices/{device_id}/state: The Pi, when it was last seen, its goals, its last camera problem and its
  objects, and recent_hours (site.recent_hours, how far back the website lists events). Access: Pi token or owner
- GET /v1/devices/{device_id}/events: Its events, oldest first. ?since=, ?after_id=, ?limit=. Access: Pi token
  or owner
- POST /v1/chat/completions: Chat through this server's AI key, text only. Off without CHAT_API_KEY. 503 in plain
  words when chat.max_at_once chats are already running on the server, and 502 when the AI company goes past
  chat.deadline_sec or fails. Access: Pi token

### Logging in

- GET /auth/google/login: Send the person to Google. 503, in plain words, when Google isn't set up. A browser on
  another address is first sent to this same page on server.login_url's address, with nothing saved. Access: open
- GET /auth/google/callback: Where Google sends them back. Access: open
- POST /auth/logout: Log out. Access: open
- POST /auth/admin/login: The admin's username and password. 404 with no admin set up. Access: limited

### The website

All of these need the login, and each reaches only that person's own things.

- GET /api/me: Who is logged in.
- DELETE /api/me: Delete this person and everything of theirs.
- GET /api/devices: Their Pis.
- POST /api/devices/code: A one-time code and the setup command. With a device_id, it links that same Pi again.
- PATCH /api/devices/{device_id}: Rename a Pi.
- DELETE /api/devices/{device_id}: Remove a Pi: cancels its token and keeps its history.
- DELETE /api/devices/{device_id}/data: Delete a Pi and everything kept for it.
- GET /api/pis/where_to_look: Where and how the page looks for Pis on the home network; the browser does the
  knocking, never this server: {"addresses", "port", "wait_ms", "at_once", "name_max_chars", "reply_max_kb"}.
  addresses is every address of the network around PUBLIC_URL (its /24, find_pis.network_prefix) but its own,
  lowest first. The page asks each one's GET /api/cloud/hello on port (find_pis.pi_port), at_once at a time,
  waits wait_ms (find_pis.knock_timeout_sec) for each, cuts a name to name_max_chars (pairing.name_max_chars) and
  reads no reply bigger than reply_max_kb. 409, in plain words, when PUBLIC_URL gives no private network to search.
- POST /api/pis/link: Link a found Pi: body {"address", "time_zone"}. Makes a one-time code and offers it to that
  Pi. Replies {"linked": true}, also when the Pi didn't answer in time but had already used the code. 400 for an
  address that isn't one of where_to_look's or a bad time zone; 409 when that Pi is already linked, is linking
  already, or isn't saving (each in its own words); 502 when the Pi refused (its reason, cut to
  find_pis.reason_max_chars), only links to the computer asking and PUBLIC_URL isn't it, couldn't reach this
  server at PUBLIC_URL, or didn't answer. The Pi's kind decides which; without one, its status does.
- GET /api/reports: Their weekly reports, newest week first, with each Pi's name and no report text. ?device_id=
  for one Pi.
- GET /api/reports/{report_id}: One report.

### Admin, and health

- GET /api/admin/overview: Who's on the server, failed reports, and each Pi's latest finished report (without its
  text). Access: admin
- POST /api/admin/reports/{report_id}/rerun: Set a report to be written again. 409, in plain words, when its Pi has
  been removed. Access: admin
- GET /healthz: For Docker's health check. nginx doesn't pass it on, so only the containers can reach it. Access:
  open
