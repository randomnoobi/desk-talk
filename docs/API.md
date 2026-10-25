# API

Every web address (route) the Desk Talk servers answer, grouped by area: what it does, who calls it, and
who is allowed to. The code is the source of truth; this page is a map of it. How the parts fit together
is in [ARCHITECTURE.md](ARCHITECTURE.md).

**62 routes in all: 50 on the main server (15 of them for the developer page), 9 on the phone camera's server, 3 on the detector service.**

## Three servers

| Server | Where it runs | Port | What it's for |
|---|---|---|---|
| **Main server** | Your computer (`uv run python server.py`) | `web.port` in `config/settings.yaml`, 5000 | The app page (`web_app/`) and everything it asks for, the Pi's uploads, and the developer page (`dev_ui/`, at `/dev/`). |
| **Phone camera server** | Your computer, in a thread of the main one. Only runs when the phone is the camera. | `camera.phone_port`, 5443, **https** | The phone camera page (`phone_camera/`). Phones only allow the camera on https pages, so it has its own certificate. |
| **Detector service** | The Pi, in Docker | `DETECTOR_PORT` in `raspberry_pi/detector_service/.env`, 8100; only reachable from the Pi itself | YOLO object detection for the Pi's camera program. |

### Finding a route in the code

- **Main server and phone server:** `desktalk/web/app.py` builds both (`create_app`, `create_phone_app`).
  The routes are in `desktalk/web/routers/`, one file per area, and the top of each file lists its URLs.
  `routers/__init__.py` says which files belong to which server (`MAIN_SERVER`, `PHONE_SERVER`). What the
  routes share (the pairing key check, "is this computer", the developer page's `DevOnly`) is in
  `desktalk/web/deps.py`; what a request body looks like is in `desktalk/web/schemas.py`.
- **Detector service:** `raspberry_pi/detector_service/desktalk_detector/app.py`. The request and reply
  shapes are in `shared/desktalk_shared/detector_api.py`.
- **Every route has a test case:** `tests/characterization/cases.py`.

### What every reply of the main server has in common

- Errors are `{"error": "plain words"}`, never `{"detail": ...}`. A body that doesn't fit is a 400 (not
  422); a request bigger than `web.max_request_mb` is a 413.
- Every reply has `Access-Control-Allow-Origin: *`, so any page may ask. (The phone server's replies don't.)
- A trailing slash is not forgiven: `/api/live/` is a 404.
- Only `GET` routes exist for the page's files, so `POST` or `PUT` to a path that isn't a route gets 405.

### Who may call what

| Access | Meaning |
|---|---|
| **anyone on the network** | No check. The main server listens on every network this computer is on (`web.host`), so anyone on the same Wi-Fi can call these. |
| **pairing key** | The request must carry the key in the `X-Desk-Key` header, or it gets 403. The key is made on start and printed in the Pi command and the phone link (`desktalk/web/pairing.py`). Checked by `key_check` in `deps.py`. |
| **this computer only** | Not a block: the route answers everyone, but leaves out some parts of its reply unless the request comes from `127.0.0.1` or `::1` (`FromHere` in `deps.py`). Today only `GET /api/live` does this: the Pi command and the phone link (and its QR code) are only shown on this computer. |
| **this computer and its own page only** | A block. A request that doesn't come from `127.0.0.1` or `::1` gets 403. So does one whose `Origin` header is set to anything but the server's own address (`http://localhost:<web.port>`, `http://127.0.0.1:<web.port>`) or `npm run dev`'s (`http://localhost:<dev.vite_port>`), so another web page open in your browser can't start a test run or a query. Checked by `DevOnly` in `deps.py`, on top of the `*` cross-origin header every reply has. Only the developer page's routes use it. |

---

## Main server

### Health (1)

| Method | Path | What it does | Called by | Access |
|---|---|---|---|---|
| GET | `/api/health` | How every part is doing in one reply: background jobs, timed jobs, event consumers, camera, what the Pi said lately, detection, the AI. | The developer page's System and Storage tabs (`dev_ui/src/api.ts`). People `curl` it ([raspberry_pi/README.md](../raspberry_pi/README.md)); `tests/e2e/test_full_stack.py` | anyone on the network |

### Chat (8)

| Method | Path | What it does | Called by | Access |
|---|---|---|---|---|
| GET | `/api/agents` | Who is in the chat (and who was earlier), plus Lens's card. | `web_app/app.js`, `tests/e2e/test_page.py` | anyone on the network |
| GET | `/api/events` | Chat messages as they happen. **Server-Sent Events** (see below). | `web_app/app.js` (`EventSource`) | anyone on the network |
| GET | `/api/messages/recent` | The last messages, for a page that just opened. | `web_app/app.js` | anyone on the network |
| POST | `/api/activity` | "I'm typing", so the small talk holds off. Empty reply (204). | `web_app/app.js` (a ping while you type, and a beacon when the page closes) | anyone on the network |
| POST | `/api/chat` | Your message, to the group or to one agent. Replies with the answers. | `web_app/app.js` | anyone on the network |
| POST | `/api/chat_todos` | Save one agent's to-do list. | `web_app/app.js` | anyone on the network |
| POST | `/api/chat_todos_sync` | Save every agent's to-do list at once. | `web_app/app.js` | anyone on the network |
| POST | `/api/widget_event` | "I opened (or used) this agent's panel." | `web_app/app.js` | anyone on the network |

- `GET /api/events`: `text/event-stream`. First `retry: 2000` (`web.live_retry_ms`), then one
  `data: {json}` line per chat message, and a `: keepalive` line when nothing came for
  `web.live_keepalive_sec`. The reply never ends; the browser reconnects by itself.
- `POST /api/chat`: JSON `{"chat": "group" or an agent id, "message": "...", "recent_messages": [...],
  "widget": {...}}`. Replies `{"messages": [...]}`. 400 for an empty message or an unknown chat.
- `POST /api/chat_todos`: `{"chat": "<agent id>", "widget": {...}}`. `/api/chat_todos_sync`:
  `{"by_chat": {"<agent id>": {...}}}`. `/api/widget_event`: `{"chat": "<agent id>", "action": "view"}`.
  All three reply `{"ok": true}`.

### Live (2)

| Method | Path | What it does | Called by | Access |
|---|---|---|---|---|
| GET | `/api/live` | Everything the desk cam panel needs in one request: camera, what is on the desk, dangers, the agents, the AI. The app asks every 1.5 s. `?qr=1` adds the phone link's QR code. | `web_app/app.js`, `tests/e2e/` | anyone on the network; the Pi command, the phone link and the QR code **this computer only** |
| GET | `/api/status` | A short summary: running, camera, agents, safety state, uptime. | `web_app/app.js` (once, when the page starts) | anyone on the network |

### Video and proof photos (6)

| Method | Path | What it does | Called by | Access |
|---|---|---|---|---|
| GET | `/api/video_feed/{camera}` | Live video with the boxes drawn. **MJPEG.** | `web_app/app.js` (an `<img>`) | anyone on the network |
| GET | `/api/video_feed_raw/{camera}` | The same, without the boxes. MJPEG. | `web_app/app.js` | anyone on the network |
| GET | `/api/video_feed_raw` | The same, for the camera that is sending. MJPEG. | Nothing in the code. [benchmarks/README.md](../benchmarks/README.md) tells people to open it | anyone on the network |
| GET | `/api/latest_frame_raw/{camera}` | The newest plain picture, once (JPEG). 404 before the first. | `web_app/app.js`; mentioned in `benchmarks/README.md` | anyone on the network |
| GET | `/api/snapshot/{photo_id}` | One proof photo (JPEG). 404 if it's gone. | `web_app/app.js` | anyone on the network |
| GET | `/api/snapshots` | The proof photos kept, newest first. | `web_app/app.js` | anyone on the network |

- The video feeds are `multipart/x-mixed-replace; boundary=frame`: one JPEG part after another, sent when
  the picture changes and again every `web.video_resend_sec`. Nothing at all is sent, not even headers,
  until the first picture arrives. If the page goes away first, the reply is an empty 204.
- `{camera}` is a whole number; the first camera is `0`.

### Habits (5)

| Method | Path | What it does | Called by | Access |
|---|---|---|---|---|
| GET | `/api/habits/today` | Today's habits (water, breaks, light, phone, food) and the vision checks. | `web_app/app.js`, `tests/e2e/test_page.py` | anyone on the network |
| POST | `/api/habits/log` | A habit button: a glass of water, a break, some sunlight, a snack. | `web_app/app.js` | anyone on the network |
| GET | `/api/habit_log` | The vision habit timeline (posture, nail biting). `?category=` and `?limit=`. | `web_app/app.js` | anyone on the network |
| GET | `/api/preferences` | Wake time, bedtime, quiet hours. | `web_app/app.js` | anyone on the network |
| PUT | `/api/preferences` | Change them. Only the fields sent change. | `web_app/app.js` | anyone on the network |

- `POST /api/habits/log`: `{"habit": "hydration", "action": "glass", ...}`. Any other field (`ml`,
  `minutes`, `label` ...) goes to the tracker. 400 for an unknown habit.
- `PUT /api/preferences`: `{"wake_time": "06:30", "bed_time": ..., "quiet_enabled": ..., "quiet_start_hour":
  ..., "quiet_end_hour": ...}`. The old names (`dnd_enabled`, `dnd_start_hour`, `dnd_end_hour`) still work.
  Replies `{"ok": true, "preferences": {...}}`; 400 for a value that isn't valid.

### Event log (6)

All take `?day=2026-10-21` (today when left out, or when it isn't a date). Lists take `?limit=`: a whole
number, kept between 1 and the most `web.list_sizes` allows; anything else is a 400.

| Method | Path | What it does | Called by | Access |
|---|---|---|---|---|
| GET | `/api/log/days` | Days that have a log, newest first. | `web_app/app.js` | anyone on the network |
| GET | `/api/log/events` | Joins, leaves, alerts, habits ... newest first. `?types=alert,habit`. | `web_app/app.js` | anyone on the network |
| GET | `/api/log/presence` | What was on the desk when, as intervals. | `web_app/app.js` | anyone on the network |
| GET | `/api/log/detections` | The per-picture lines that aren't compacted yet, newest first. | `web_app/app.js` | anyone on the network |
| GET | `/api/log/status` | The log's folder, today's counts, the timed jobs, the event stream, the camera. | `web_app/app.js`; the developer page's System tab | anyone on the network |
| POST | `/api/log/compact` | Compact the log now (per-picture lines become intervals). | `web_app/app.js` (the Log page's button) | anyone on the network |

### Settings (3)

| Method | Path | What it does | Called by | Access |
|---|---|---|---|---|
| GET | `/api/rules` | The danger rules and the desk corners, plus the objects a zone can be set for. | `web_app/app.js` | anyone on the network |
| PUT | `/api/rules` | Change some of them. When the desk corners are marked, it also notes how the phone was tilted. | `web_app/app.js` | anyone on the network |
| GET | `/api/ui_settings` | The numbers and words the app page uses (refresh times, limits, Lens's card ...), so the page keeps no copies of its own. | `web_app/app.js` (on start), `tests/e2e/test_page.py` | anyone on the network |

- `PUT /api/rules`: `{"danger_zones": ..., "edge_proximity": ..., "chain_reaction": ..., "temporal": ...,
  "desk_outline": [[x, y], ...]}`. Only the parts sent change; each corner is 0 to 1 of the picture. An
  empty body is a 400. Replies `{"ok": true}`.

### Pi (2)

The Pi's camera program (`raspberry_pi/camera/`) is the only caller. Both routes need the pairing key.

| Method | Path | What it does | Called by | Access |
|---|---|---|---|---|
| GET | `/api/pi/config` | The Pi's settings (`pi:` in `config/settings.yaml`, detector included), so they live in one place. | The Pi's camera program (`raspberry_pi/camera/settings.py`), at start and every `pi.settings_check_every_sec` | pairing key |
| POST | `/api/pi/frame` | One picture from the Pi, with what its detector found. | The Pi's camera program (`raspberry_pi/camera/uploader.py`), about 3 times a second | pairing key |

- `POST /api/pi/frame`, **upload format 2**: `Content-Type: multipart/form-data` with two parts,
  `info` (JSON: format version, picture id, when it was taken, light, how the Pi is doing, the detector's
  state `ok` / `busy` / `off` and its detections) and `picture` (the JPEG). Read by
  `desktalk_shared.wire.decode_upload`. Replies `{"ok": true, "fps": 3, "tip": null or "..."}`.
- **Upload format 1** (the old `camera_node.py`): any other content type; the body is the JPEG and a few
  `X-...` headers carry the rest. Its detector counts as `off`, so this computer detects the picture.
  Kept for old Pis; nothing in this repo sends it any more except the tests.
- Errors: 400 not a JPEG or a bad `info`, 403 wrong key, 409 `camera.source` is not `pi`, 413 bigger than
  `web.pi_upload_max_mb`.

### Developer page (this computer only) (15)

The routes behind the developer page (`dev_ui/`, see [its README](../dev_ui/README.md)), in
`desktalk/web/routers/dev.py`. They are only there when `dev.enabled` is on in `config/settings.yaml`;
switched off, every one of them, and `/dev/`, is a 404. They are never on the phone server. The only caller
is the developer page, through `dev_ui/src/api.ts`; the tab that uses each one is in the "Called by" column.

| Method | Path | What it does | Called by | Access |
|---|---|---|---|---|
| GET | `/api/dev/settings` | The page's numbers, so it keeps no copies: how often to ask again, the checks' names, the System tab's limits, the smoothing settings, the most rows a query shows, the data folder. | The page, once when it opens | this computer and its own page only |
| GET | `/api/dev/states` | Each object's state now, and the changes since `?since=<time>`. | Live tab | this computer and its own page only |
| GET | `/api/dev/smoothing` | The objects that have a smoothing trace. | Live tab | this computer and its own page only |
| GET | `/api/dev/smoothing/trace` | What the smoothing made of one object's last pictures, oldest first. `?label=pen%2Fpencil` (a query, so a label with a slash works); an object never seen gives an empty list, not an error. | Live tab | this computer and its own page only |
| GET | `/api/dev/tests` | Every test the Checks tab lists (`dev.collect_markers`), slow ones marked. | Checks tab | this computer and its own page only |
| POST | `/api/dev/runs` | Start a run: every step in `dev.checks`, or some tests by id. One run at a time. | Checks tab | this computer and its own page only |
| GET | `/api/dev/runs/{run_id}` | How a run is going: its steps, each finished test, and the printout after `?from=<offset>`. | Checks tab, while a run goes | this computer and its own page only |
| POST | `/api/dev/runs/{run_id}/stop` | Stop a run. | Checks tab (**Stop**) | this computer and its own page only |
| GET | `/api/dev/db` | The SQLite databases' names. | Database tab | this computer and its own page only |
| GET | `/api/dev/db/{name}/tables` | One database's tables: columns, row count, the `CREATE` line. | Database tab | this computer and its own page only |
| POST | `/api/dev/db/{name}/query` | Run one query that only reads, on a read-only connection. | Database tab | this computer and its own page only |
| GET | `/api/dev/files` | Every file and folder in the data folder, with sizes and dates. | Storage tab | this computer and its own page only |
| GET | `/api/dev/files/preview` | The start of one file in the data folder (`dev.file_preview_kb`). `?path=` is relative to the folder. | Storage tab | this computer and its own page only |
| GET | `/dev/` | The developer page (`dev_ui/dist/index.html`, from `dev.ui_folder`). Before `npm run build` has made it, a plain line saying how to build it (200). | Your browser | anyone on the network (only the page itself: what it shows comes from the routes above) |
| GET | `/dev/{file}` | The page's other files (its script and style, the font). | Your browser | anyone on the network |

- Every `/api/dev` route turns away other computers and other web pages with 403 and `{"error": ...}`
  (the wording is in `config/prompts.yaml` under `dev:`), before anything runs.
- `GET /api/dev/settings`: `{"poll_sec", "checks": [names], "health": {...}, "trace_pictures",
  "sql_max_rows", "data_folder", "object_states": {"on_confidence", "off_confidence", "appear_sec",
  "gone_sec", "window_pictures", "vote_share"}}`.
- `GET /api/dev/states`: `{"objects": {label: {"state", "since"}}, "watched_since", "changes": [{"ts",
  "label", "change"}]}`.
- `GET /api/dev/smoothing`: `{"labels": [...]}`. `/api/dev/smoothing/trace`: `{"label", "steps": [...]}`;
  each step has `ts`, `conf`, `box_seen`, `votes_yes`, `votes_total`, `seen`, `center`, `phase`, `heading`
  and `waiting_sec`.
- `GET /api/dev/tests`: `{"tests": [{"id", "file", "markers"}]}`. The `id` is pytest's, e.g.
  `tests/unit/test_x.py::test_y`.
- `POST /api/dev/runs`: `{"check": true}` or `{"tests": [ids]}`. Replies `{"run_id"}`. 409 `{"error": "A run
  is already going."}` while one is; 400 for a test id that isn't in the list. The commands only come from
  `dev.checks` and `dev.test_command`, never from the request.
- `GET /api/dev/runs/{run_id}`: `{"run_id", "what", "state", "started", "finished", "steps": [{"name",
  "state", "exit_code"}], "outcomes": [{"id", "outcome", "duration_sec", "message"}], "output",
  "output_end"}`. `state` is `running`, `passed`, `failed`, `stopped` or `timed out` (after
  `dev.run_timeout_min`); ask again with `?from=<output_end>` for only the new printout. 404 for a run it
  doesn't know (only the newest run is kept). `POST .../stop` replies `{"ok": true}`.
- `GET /api/dev/db`: `{"databases": [...]}`. `.../tables`: `{"tables": [{"name", "columns", "rows",
  "sql"}]}`.
- `POST /api/dev/db/{name}/query`: `{"sql": "..."}`. Replies `{"columns", "rows", "truncated", "took_ms"}`,
  at most `dev.sql_max_rows` rows. 400 `{"error": ...}` for anything but one statement that only reads
  (`DELETE`, `INSERT`, `ATTACH`, two statements ...), or a query that ran longer than `dev.sql_timeout_sec`.
- `GET /api/dev/files`: `{"files": [{"path", "bytes", "modified", "is_folder"}]}`. `.../preview`:
  `{"path", "text"}`; 400 for a path outside the data folder (`../.env`, an absolute path, a symlink out) or
  a folder, never read; 404 when nothing is there.

### Pages and files (2)

| Method | Path | What it does | Called by | Access |
|---|---|---|---|---|
| GET | `/` | The app page (`web_app/index.html`). | Your browser | anyone on the network |
| GET | `/{file}` | Any other file in `web_app/` (`app.js`, `style.css`, `art/`, `fonts/` ...). 404 if it isn't there. | Your browser | anyone on the network |

This one is mounted last, so every route above (`/dev/` included) wins over it.

---

## Phone camera server (https)

Built by `create_phone_app`; the routes are in `desktalk/web/routers/phone.py`. No cross-origin header.
Only the phone page (`phone_camera/`) calls these.

### Phone (3)

| Method | Path | What it does | Called by | Access |
|---|---|---|---|---|
| GET | `/api/phone/config` | How many pictures a second to send, how big and how sharp, the highest real tilt, the size to ask the phone's camera for. | `phone_camera/camera.js` | anyone on the network |
| POST | `/api/phone/frame` | One picture from the phone. | `phone_camera/camera.js` | pairing key |
| POST | `/api/phone/stop` | "I pressed Stop on the phone." | `phone_camera/camera.js` | pairing key |

- `POST /api/phone/frame`: the body is the JPEG (`Content-Type: image/jpeg`). Optional headers:
  `X-Desk-Gravity: x,y,z` (how the phone is tilted, m/s²) and `X-Desk-Rotation: 90` (how far the phone
  turned the picture). Replies `{"ok": true, "fps": 3, "tip": "Too dark"}`; `tip` is left out when there's
  nothing to say. Errors: 400 not a JPEG, 403 wrong key, 409 `camera.source` is not `phone`, 413 too big.

### Pages and files (6)

| Method | Path | What it does | Called by | Access |
|---|---|---|---|---|
| GET | `/`, `/phone`, `/phone/` | The camera page (`phone_camera/index.html`). | The phone's browser (from the QR code) | anyone on the network |
| GET | `/phone/{file}` | The page's files (`camera.js`, `style.css` ...). | The phone's browser | anyone on the network |
| GET | `/art/{file}`, `/fonts/{file}` | The drawings and fonts it shares with the app (`web_app/art/`, `web_app/fonts/`). | The phone's browser | anyone on the network |

The 6 page routes are `/`, `/phone`, `/phone/` and the three file folders; with the 3 above that is 9.

---

## Detector service (on the Pi)

Built by `create_app` in `raspberry_pi/detector_service/desktalk_detector/app.py`. The only caller is the
Pi's camera program (`raspberry_pi/camera/detector_client.py`). There is no pairing key: Docker only
publishes the port on the Pi itself (`127.0.0.1`), so other computers can't reach it. Errors are
`{"detail": "plain words"}` here, not `{"error": ...}`.

### Detector (3)

| Method | Path | What it does | Called by | Access |
|---|---|---|---|---|
| POST | `/detect` | Finds the objects in one picture. | The Pi's camera program, for each picture | the Pi only |
| GET | `/health` | `starting`, `loading`, `ready` or `error`, which model answers, and a message. | The Pi's camera program (while the detector is off); Docker's health check | the Pi only |
| PUT | `/model` | Which model to run. It loads in the background; the old one keeps answering until the new one is ready. | The Pi's camera program, on start and when the computer's `pi.detector` settings change | the Pi only |

- `POST /detect`: the body is one JPEG. Replies `{"detections": [...], "detect_ms", "width", "height",
  "model"}`. Errors: 400 not a JPEG, 413 bigger than `DETECTOR_MAX_PICTURE_MB`, 429 still busy with another
  picture, 503 no model yet.
- `PUT /model`: JSON `{"model": "yolo26m-objv1-150.pt", "format": "ncnn", "image_size": 640, "confidence":
  0.35, "half": false}`. Replies like `/health`. 400 for settings that don't fit.
- FastAPI's own pages (`/docs`, `/redoc`, `/openapi.json`) are also switched on here; they are not counted.

---

## Routes removed in the cleanup

Nothing called these, so [the cleanup](plans/2026-10-23-codebase-cleanup-plan.md) deleted them. They now
answer like any address the server doesn't know: 404 to a `GET`, 405 to anything else.
`test_removed_routes_answer_404` in `tests/integration/test_web_routes.py` checks this.

`GET /api/context` · `GET /api/daily_stats` · `GET` and `PUT /api/habit_policy` · `GET /api/detections` ·
`GET` and `POST /api/safety` (with `safety_pipeline/`) · `GET /api/scenarios` · `GET /api/phone/info` ·
`GET /api/video_feed` (without a camera number) · `GET /api/latest_frame/{camera}` (the picture with
boxes; `latest_frame_raw` is what is left)

`GET /api/video_feed_raw` without a camera number was on the plan too, but stayed because the benchmarks
README tells people to open it.

## Adding a route

`tests/characterization/test_route_coverage.py` fails when a route on the main or phone server has no case
in `tests/characterization/cases.py`. So a new route needs three things: the route in its file in
`desktalk/web/routers/` (and a line in that file's top comment), a case in `cases.py`, and a row on this
page. Nothing checks this page, so keep the totals at the top right too.
