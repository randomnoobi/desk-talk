# Phase 3 notes: what the FastAPI app has to match

From the route tests in `tests/characterization/`: 54 method + URL pairs (44 on the main server, 10 on the
phone server), 133 cases. To run them against FastAPI, change only `tests/characterization/client.py` and the
desk builder in `tests/characterization/conftest.py`. The coverage guard reads routes through
`client.routes()` / `client.route_for()`.

## Must stay exactly as today

**Error replies and status codes**
- Error replies are `{"error": "..."}` with the same status codes (403 / 400 / 409 / 404), not FastAPI's
  `{"detail": ...}`.
- `/api/activity` returns 204 with an empty body. FastAPI's default is 200.
- Trailing slashes: `/api/live/` is 404 today. Turn off `redirect_slashes`, which would make it a 307.
  `/phone` and `/phone/` are both real pages.

**Keys left out vs null**
- `tip` and `reply_to` are left out when there's nothing, while `actions: []` and `tip: null` (Pi upload)
  are always sent. Pydantic reply models must match this exactly.
- 1-on-1 chat replies have no message `id`; group chat replies do.

**SSE (`/api/stream`)**
- Content type `text/event-stream; charset=utf-8`.
- Headers `Cache-Control: no-cache` and `X-Accel-Buffering: no`.
- The first event is exactly `retry: 2000`, then one-line `data: {json}` events.
- A keepalive comment every 25 s (not tested).

**Video**
- Content type `multipart/x-mixed-replace; boundary=frame`; each part has `Content-Type` and `Content-Length`.
- The stream sends nothing at all, not even headers, until the first picture exists.

**Other headers**
- CORS `Access-Control-Allow-Origin: *` on every main-server reply; none on the phone server.
- `Cache-Control: no-cache` on static files.
- `public, max-age=3600` on saved photos.
- No-cache, `Pragma` and `Expires` headers on live pictures.

**Local vs Wi-Fi requests**
- `/api/live` and `/api/phone/info` include the phone link, QR code and Pi command only when the request comes
  from 127.0.0.1.
- FastAPI's TestClient reports the client as `testclient`, so the adapter must set it to 127.0.0.1.

**Headers the Pi and phone send**
- From the Pi: `X-Desk-Key`, `X-Frame-Id`, `X-Taken-At`, `X-Pi-Camera`, `X-Pi-Photos`, `X-Pi-Free-MB`,
  `X-Pi-Last-Cleanup`, `X-Lux` (upload format 1; blank values are ignored). `desktalk_shared.wire.info_from_v1_headers`
  reads them.
- From the phone: `X-Desk-Rotation` and `X-Desk-Gravity`.

**Endless streams**
- TestClient may not allow reading only the start of an endless SSE or video reply. If it doesn't, `stream()` in
  `client.py` may need a real uvicorn server on a spare port.

## Odd behavior today (recorded by the tests as it is)

Decide on purpose in Phase 3, and re-record the snapshot (`DESKTALK_RECORD_SNAPSHOTS=1`) only for the ones we fix:

- An empty upload to `/api/pi/frame` or `/api/phone/frame` gives 500 (OpenCV crashes on an empty buffer).
  **Fix:** 400 with `{"error": "not a JPEG image"}`, like other bad pictures.
- `/api/habit_log?limit=lots` and `/api/log/events?limit=all` give 500. **Fix:** 400 with a plain error.
- The phone server's automatic `/static/<path>` route always gives 404 (the folder doesn't exist). **Fix:** drop the route.
- Pi upload replies always include `"tip": null`; phone upload replies leave `tip` out. Keep as is (the pages
  don't mind).

## Wiring notes from the Phase 2 agents

**A. Detector service**
- The camera program reads `pi.detector.*` from `/api/pi/config` (now includes `detector`).
- The service's port (`raspberry_pi/detector_service/.env`) must match `pi.detector.url`; a test checks this.
- Error replies from the service are FastAPI's `{"detail": ...}`: 400 / 413 / 503 / 500.
- The docker tests (`-m docker`) and the image build have not run: no Docker on the Mac.

**C. Events and storage**
- New setting: `storage.folder: data`, the root of `JsonFileStore`. Under it are `preferences.json`,
  `habits/<day>.json` and `learned_personalities.json`.
- `EventLog(config.resolve(config.event_log.folder), config.event_log)`.
- Scheduler jobs: `every(compact_every_min * 60, lambda: log.compact(clock.now()))`, and the same for `prune`
  (`prune_every_min`).
- `safety_rules.json`: a second `JsonFileStore(config dir)`, key `safety_rules`.
- Shutdown: `stream.wait_idle(t)`, then `stream.close(t)`. `close` is on `EventStream`, not on the port.
- `JsonFileStore.save` raises `OSError` / `TypeError` and leaves the old file as it was. Callers decide whether to
  log it and carry on.
- Day names are checked strictly (`YYYY-MM-DD`). The old routes' check let `../a-bc-de` through. Use the store's
  check in the log routes.

**E1. Cameras, detector, producer**
- New settings:
  - `detector.end2end: false`: run YOLO26's second head plus the overlap filter, like the Pi's ncnn model.
  - `detector.rect: false`: always pad pictures to a square, like ncnn.
  - `camera.pair_key_bytes: 6`: an 8-character pairing key.
- `camera.pause_after_error_sec` is now unused (the Supervisor's retry wait replaces it). Remove it.
- `UltralyticsDetector(config.resolve(config.detector.model), config.detector, end2end=..., rect=...)`, then `.warmup()`.
  A model that won't load leaves Pi detections working (no crash at startup).
- `DetectionProducer(source, detector, bus, desk, clock, lens, painter, config.camera,
  new_session_after_sec=config.protocols.breaks.break_min * 60)`.
- Webcam: `WebcamSource(clock, config.camera, config.camera.webcam_indices[0])`.
- `/api/live`:
  - `frames_received` comes from `source.pictures_received`;
  - `camera_info.pi` comes from `pi_source.pi_info()`;
  - `yolo_ms` comes from `producer.last_detect_ms or 0`.
- `/api/health` gets `pi_source.status()` (upload format, detector state and reason, detect time, model,
  how far off the Pi's clock is).
- `/api/pi/frame`:
  - reply 413 above `web.pi_upload_max_mb` before calling `push`;
  - `push() == False` means 400 (this also fixes the empty-upload 500).
- `parse_gravity` (phone route) isn't ported yet. The web layer needs it.

**F. Agents and habits**
- New setting: `agents.personality_max_chars: 400` (the longest personality the AI may write for an object).
- `Profiles` now has `for_label` and `mention_words`, so F's local `ProfileLookup` is no longer needed. Tidy it up.
- `ProfileBook(config.agents_file, store, personality_max_chars=...)`; learned personalities are under the key
  `learned_personalities`.
- Roster: `Roster(bus, profiles, desk, config.agents, config.presence, on_desk=...)`.
  - `on_desk(x, y, w, h)` comes from E2's desk outline and returns True when there's no outline.
- Habit tracker: `HabitTracker(clock, bus, store, profiles, prefs, desk, config.protocols, config.habit_tracker,
  config.presence, config.agents, phone_counts=<camera isn't the phone>)`.
  - Save it every `habit_tracker.save_check_every_sec`, and `save(force=True)` on shutdown.
- `HabitReminders(habits, desk_status, roster, profiles, generator, hub, bus, config.reminders)`, added with
  `start_after=check_every_sec`.
- `Preferences(store, schedule, quiet_hours)` from `config.protocols`; `DeskStatus(desk, source, prefs, config.reminders)`.
- `desktalk/util.py` is used by the new code. Keep it in Phase 4.
- Behavior changed on purpose:
  - once-a-day nudges use their habit's own repeat time;
  - `{object}` is filled in for agents without a personality;
  - a preferences update is all-or-nothing;
  - a failed habit save is retried.

**D1. AI and message generator**
- New setting: `chat.reply_max_chars: 500` (an AI reply is cut to this many letters).
- Bootstrap:
  - `ai = OpenAICompatibleChatModel(config.llm, config.secrets, clock)`
  - `model = ResilientChatModel(ai, clock, config.llm, time_limit_sec=ai.time_limit_sec)`
  - Ollama keep-alive: `supervisor.add(ai.keepalive_worker(), start_after=config.llm.ollama_keepalive_start_sec)`,
    only when the worker isn't None.
  - `hub = MessageHub(clock, recent_kept=config.chat.recent_kept, queue_size=config.web.live_queue_size)`
  - `generator = MessageGenerator(model, hub, roster, profiles, habits, desk, clock, status=desk_status,
    habit_log=habits, llm=config.llm, chat=config.chat, agents=config.agents,
    sunlight_default_min=config.habit_tracker.sunlight_default_min, reply_max_chars=...,
    local_model=ai.is_local)`, then `generator.subscribe_to(bus)`.
- Routes:
  - `POST /api/chat`:
    - First, keep what the route does today: set `desk.last_user_activity`, reply 400 for an empty message, and
      run the lost-items check.
    - Then `generator.answer(chat_id, message, recent_messages or history, widget=...)`; `UnknownChat` → 400
      `{'error': 'unknown chat'}`.
  - The SSE route: `q = hub.subscribe()`, `q.get(timeout=web.live_keepalive_sec)`, and `hub.unsubscribe(q)` in
    `finally`.
  - `/api/messages/recent` → `{'messages': hub.recent()}`.
  - `/api/context` → `generator.live_context()`.
  - `/api/chat_todos` and `/api/chat_todos_sync` → `generator.save_widget(pid, w)`.
  - `/api/widget_event` → `generator.note_panel_event(pid, action)`.
  - Everywhere else, `generator.broadcast(m)` becomes `hub.send(m)`; use the dict it returns.
- To fix on purpose later: the memory check that skips memories already shown never matches, so every memory is
  repeated (ported as it was).

**D2. Background chat jobs** (`desktalk/core/chat/workers/`)
- New setting: `lens.wait_before_saying_sec.pi: 10`, how long the Pi must be hot or its clock off before Lens says so.
- Vision: pass `model` to `LostItems` / `VisionHabits` only when `model.local_vision`; otherwise pass `None`, so no
  cloud quota goes on scans.
- `IdleChat(..., desk_summary=<D1's live context summary>)`, with `start_after=idle_chat.start_delay_sec`.
- Interfaces in `workers/needs.py` to connect (or move into `ports.py`):
  - `PiUploads.latest_upload() -> (UploadInfo, received_at) | None`, from E1's `PiFrameSource` (add the method);
  - `PhoneCamera`, from `PhoneFrameSource` (`recent_gravity()`, `stopped_by_user`);
  - `DeskLayout.has_outline()` / `reference_gravity()`, from E2.
- `DailySummary` remembers "sent today" under store key `daily_summary`.
- `/api/scenarios` uses `lost_items.sightings()` instead of `desk.lost_objects`, and `vision_habits.events()`
  instead of `desk.habit_events`.
- Copies of D1's code to point at D1's versions: `workers/prompts.py` and `workers/text.py`.
- Not done yet: Lens saying "Pi detection off" and why (plan's failure table). Add it with its own wait setting.

**B. Camera program** (`raspberry_pi/camera/`, run with `python3 -m raspberry_pi.camera` from the repo folder)
- Five new `pi:` settings, in `settings.yaml` and `PiSettings` (the program won't start without them):
  - `camera_warmup_sec: 1`
  - `pause_after_error_sec: 0.5`
  - `status_every_sec: 30`
  - `health_every_sec: 3`
  - `settings_check_every_sec: 60`
- `/api/pi/frame` must take format 2 (`decode_upload`) as well as format 1 (`info_from_v1_headers`).
- An `ok` picture can arrive after `busy` pictures taken later. The live view must not show an older picture over
  a newer one (compare `taken_at` / the frame number).
- `server.py` must print `python3 -m raspberry_pi.camera --server ... --key ...` instead of the `camera_node.py`
  command.
