# Phase 2 briefs: one per subagent

Part of [the refactor plan](2026-10-21-refactor-plan.md). Each agent works in its own git worktree and branch,
builds its part test-first against the interfaces from Phase 1, and owns its own folders. No two agents touch
the same file.

## Rules for every agent

**Where you work**
- Only in your own worktree, on your own branch. Use absolute paths. Never cd into the main checkout or
  another worktree: other agents are working there.
- Set up with `uv sync --all-packages`, then run everything with `uv run`.

**What's frozen (you build on it, you don't change it)**
- Where each old hardcoded number lives now: [2026-10-21-settings-map.md](2026-10-21-settings-map.md).
- `desktalk/ports.py` (the interfaces), `desktalk/domain/` (plain data), `shared/desktalk_shared/`
  (Detection, the upload format, the detector API, the Ultralytics converter), `tests/fakes/`,
  `tests/contracts/*_contract.py`, `desktalk/config.py` (typed settings), `config/*.yaml`, `desktalk/runtime/`,
  `pyproject.toml`, `uv.lock`.
- If you need a change in one of these, don't make it. Work around it if you can, and put the change you
  need in your report. If you can't work around it, report BLOCKED with the change you need.
- New settings: take the value as a constructor argument (a typed section from `desktalk.config` where one
  exists) and list the setting you need (name, value, plain comment) in your report. Phase 3 adds it to
  `config/settings.yaml`.

**The old code stays as it is**
- The old modules (`desktalk/app.py`, `desktalk/web/`, `desktalk/chat/`, `desktalk/vision/`,
  `desktalk/habits/`, `desktalk/agents/`, `desktalk/events/`, `desktalk/settings.py`, `desktalk/scheduler.py`,
  `raspberry_pi/camera_node.py`) keep the app running until Phase 3 switches over. Don't edit them or their
  tests. Phase 4 deletes them.
- Read them as the spec. Your new code must do what they do, unless the plan says otherwise.
- Port their tests: every behavior an old test checks for your area gets a new test against your new code.

**How to write the code**
- Strict test-driven development with the superpowers skill:
  /Users/samwang/.claude/plugins/cache/superpowers-marketplace/superpowers/7.0.0/skills/test-driven-development/SKILL.md
  and `writing-good-tests.md` next to it. Failing test first, watch it fail for the right reason, least code to
  pass, tidy up.
- Every class asks in its constructor for only the interfaces and settings it uses. No `app` object,
  no module globals, no `time.time()` (use the `Clock`), no `print` (use `logging.getLogger(__name__)`).
  Every number comes from settings.
- Background jobs are `PeriodicWorker`s (`name`, `tick(now) -> seconds until next`). They don't start
  threads themselves; the `Supervisor` in `desktalk/runtime/` runs them. Test them by calling `tick()` with a
  `FakeClock`.
- Events are the typed classes in `desktalk.domain` (`DetectionEvent`, `AgentJoined`, `Alert`,
  `HabitEvent`, `NudgeSent`, `CameraNotice` ...). Subscribe by class. Publish with `bus.publish(Event(...))`.
- Any real version of an interface gets a test class that runs the matching suite in `tests/contracts/`.
- `DeskState` (`desktalk/desk_state.py`) is still the shared "desk right now" and is passed in directly.
  Don't edit that file. State that only your part uses (the `lost_*`, `vlm_*` fields and so on) moves into
  your own class.

**Tests**
- Test file names must be unique across `tests/`, so prefix them with your area,
  e.g. `tests/unit/test_core_roster.py`.
- `tests/` is a package: import helpers as `from tests.fakes import ...` and `from tests.conftest import ...`.
- No network, API keys, camera, Pi, Docker or real waiting in the default run. Real model: `@pytest.mark.slow`.
  Docker: `@pytest.mark.docker`.

**Plain words**
- Comments, docstrings, log messages and anything the user sees are in plain everyday words, like the
  existing README and `config/settings.yaml`.

**Done means**
- `uv run pytest -m "not slow"` passes. Show the summary line.
- `uv run ruff check <your folders and tests>` is clean. Show it.
- One or more commits on your branch, each message one short plain sentence ending with:
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`

**Report back (short)**
- Status: DONE, DONE_WITH_CONCERNS or BLOCKED.
- Commit hashes.
- The public API: classes, constructor arguments, the interfaces each one implements.
- Settings you need added.
- Changes you'd want in frozen files.
- Anything in the old code that looked wrong.

---

## A. Detector service (runs in Docker on the Pi)

**Owns:**
- `raspberry_pi/detector_service/` (package `desktalk_detector`, plus `Dockerfile` and `compose.yaml` there)
- `tests/unit/test_detector_service_*.py`, `tests/docker/`

**Spec:**
- the plan's "Detection on the Pi" section and failure table
- `shared/desktalk_shared/detector_api.py`: the HTTP API, already decided
- `shared/desktalk_shared/ultralytics_results.py`: use it to build the replies
- `benchmarks/Dockerfile` and `benchmarks/compose.yaml`: an image that already works for Ultralytics +
  ncnn on the Pi, built from `uv.lock`
- `docs/plans/2026-10-21-ncnn-check.md`

**Build:**
- FastAPI app (`create_app(...)` factory, so tests use `TestClient` with a fake YOLO):
  - `POST /detect`: raw JPEG body → `DetectReply.to_dict()`.
  - `GET /health` → `DetectorHealth`.
  - `PUT /model`: `ModelSettings` → load it, exporting `.pt` → ncnn first if the ncnn folder isn't there.
  - Always reply with `to_dict()`, never FastAPI's automatic serialising (the JSON must match
    `desktalk_shared`).
- A model manager that:
  - loads and exports in the background, and keeps answering with the last good model meanwhile;
  - retries a failed load with growing waits;
  - runs one detection at a time;
  - sets `YOLO_AUTOINSTALL=false`;
  - keeps models in a folder that becomes a Docker volume.
- `/detect` while there's no model: 503 with a plain reason.
- Bad JPEG: 400.
- Picture bigger than the limit: 413.
- Settings come from the environment and the PUT, not from desktalk's config (the service doesn't import
  `desktalk`).
- Decision 9 in the plan: export with Ultralytics as-is. Note the end-to-end-head warning in `/health`'s
  message for YOLO26 models.
- `Dockerfile` (built from the repo root so the uv workspace and `uv.lock` are there, with
  `uv sync --frozen --package desktalk-detector --no-dev`, Python 3.12 slim, `libgl1` and `libglib2.0-0`)
  and `compose.yaml`:
  - `restart: unless-stopped`;
  - a health check on `/health`;
  - a memory limit;
  - the port bound to `127.0.0.1` only;
  - a named volume for models.
  - Read the limit and port values from an env file with plain comments, not hardcoded in several places.
- An opt-in `tests/docker/` test (`@pytest.mark.docker`, skipped when Docker isn't installed): build the
  image, start it with compose, check `/health` and `/detect`.
- A short `raspberry_pi/detector_service/README.md` with steps labelled **On the Pi** / **On your Mac**.

**Contract:**
- An in-process adapter (`TestClient` → an object with `detect(picture)` that encodes a JPEG and decodes
  the `DetectReply`) runs `tests/contracts/detector_contract.py` with a fake YOLO.

**Don't:** touch the camera program (agent B).

## B. Camera program (on the Pi's own Python)

**Owns:**
- `raspberry_pi/camera/`
- `tests/unit/test_camera_program_*.py`

**Spec:**
- `raspberry_pi/camera_node.py`, its tests (`tests/unit/test_pi_node.py`, `tests/integration/test_pi_to_server.py`)
  and `raspberry_pi/README.md`
- the plan's "Detection on the Pi" section and its failure table rows for the Pi
- `shared/desktalk_shared/wire.py` (version 2 upload) and `detector_api.py`

**Rules:**
- Standard library only, plus `picamera2`, `cv2` and `yaml` from apt (imported inside the function that
  uses them), plus `desktalk_shared` imported from the repo's `shared/` folder (add it to `sys.path` from the
  program's own location).
- Must run on Python 3.11.
- Never import `desktalk`.

**Build, as small modules:**
- `cameras.py`: Picamera2 / USB / folder / video, as today.
- `photo_store.py`: save, clean up, disk limits, as today.
- `detector_client.py`: asks the container (`/detect` with a short time limit per picture). After a few
  failures in a row it stops asking for a while and checks `/health` now and then. On start, and when the
  computer's settings change, it sends `PUT /model` with the `pi.detector` settings.
- `uploader.py`: version 2 upload with the detector state. Back off with growing waits when the computer is
  away. Don't catch up on old pictures.
- `health.py`: CPU temperature, throttling and disk, from `/sys` and `/proc`; tested with a fake folder tree.
- `main.py`: the loop, flags, settings fetched from the computer's `/api/pi/config` as today, and
  `--self-test` (camera, container health, temperature, computer reachable).
- Only the newest picture goes to the detector. Pictures taken while it's busy go out as `busy`. When the
  container is down they go out as `off`, with the reason.
- All timing through an injectable clock, so tests don't wait.
- Test with a fake camera, a fake container (an in-process HTTP server, or a fake transport behind the
  client) and a fake computer.
- An example systemd unit file and a `--help` that shows the run command.

**Don't:** touch the detector service (agent A) or `camera_node.py`.

## C. Events and storage

**Owns:**
- `desktalk/core/events/`, `desktalk/adapters/storage/`
- `tests/unit/test_core_events_*.py`, `tests/unit/test_storage_*.py`

**Spec:**
- `desktalk/events/stream.py`, `desktalk/events/log.py` and their tests
- the plan's failure table rows "A consumer is slow" and "A data file is half-written or broken"

**Build:**
- `EventStream`, implementing `EventBus` with typed events:
  - one bounded queue and thread per consumer;
  - when a queue is full, drop the oldest `DetectionEvent` only (never joins, alerts, habits or nudges) and
    count it;
  - a handler error is logged and counted, and the consumer keeps going;
  - `wait_idle`, `stats`, `close`.
  - Also a `synchronous=True` mode for tests.
  - Passes `tests/contracts/event_bus_contract.py`.
- `EventLog`, implementing `EventStore`:
  - the same folder layout and file names under `data/events/` as today, so existing logs keep working;
  - compaction into intervals as today;
  - pruning old days;
  - broken lines in `.jsonl` files skipped and logged, not fatal;
  - all writes through temp file + rename where today's code rewrites a file.
  - Passes `tests/contracts/event_store_contract.py`.
- `JsonFileStore`, implementing `KeyValueStore`:
  - key `preferences` → `<root>/preferences.json`; key `habits/2026-10-21` → `<root>/habits/2026-10-21.json`.
    The same files as today, so existing data loads.
  - A broken file reads as missing and is logged.
  - Saves are atomic.
  - Passes `tests/contracts/key_value_contract.py`.
- The queue size, merge gap, days kept etc. are constructor arguments (from settings).

## D1. AI connection and the message generator

**Owns:**
- `desktalk/adapters/llm/`
- `desktalk/core/chat/`, except `desktalk/core/chat/workers/`, which belongs to D2
- `tests/unit/test_llm_*.py`, `tests/unit/test_core_chat_*.py`

**Spec:**
- `desktalk/chat/llm.py`, `message_generator.py`, `prompts.py`, `context.py`, `tracker_actions.py`
- `tests/unit/test_chat_helpers.py` and the chat parts of `tests/integration/`
- the plan's failure table row "The AI is down or out of quota"

**Build:**
- `OpenAICompatibleChatModel`, implementing `ChatModel`:
  - picks the provider from secrets as today: Gemini → OpenAI → Groq → Ollama;
  - same request options per provider;
  - a separate vision client for Ollama;
  - friendly plain-words errors;
  - raises `AIUnavailable` instead of returning None.
  - The OpenAI client factory is injected (tests use a fake, like `tests/conftest.py`'s `FakeAI`).
  - The Ollama keep-alive becomes a `PeriodicWorker`.
- `ResilientChatModel`, wrapping any `ChatModel`:
  - a time limit per call;
  - retries with growing waits (not for background calls after a rate limit);
  - background calls pause after a rate limit;
  - a circuit breaker that stops calling for a while after repeated failures;
  - `problem()` in plain words.
  - Uses `Clock`, so tests are exact. Both models pass `tests/contracts/chat_model_contract.py`.
- `MessageHub`, implementing `Notifier`: keeps the recent chat messages and fans them out to subscriber
  queues (what `/api/stream` SSE reads; Phase 3 connects the web side). Bounded queues; dead subscribers
  are dropped.
- `MessageGenerator`, implementing `ChatVoice`:
  - subscribes to `AgentJoined` / `AgentLeft` / `Alert` / `HabitEvent` and writes the chat lines;
  - answers the user's chat;
  - `phrase_nudge`.
  - Takes `ChatModel`, `Notifier`, `RosterView`, `Profiles`, `HabitsView`, `DeskState`, `Clock` and its
    settings.
  - Short built-in fallback lines when the AI is unavailable, so characters still speak.
  - `prompts.py`, `context.py` and `tracker_actions.py` ported (pure functions where possible).
- Keep what the web routes call with the same reply shapes; Phase 3 wires them. Read
  `desktalk/web/routes.py` for which calls those are.

## D2. Background chat jobs

**Owns:**
- `desktalk/core/chat/workers/`
- `tests/unit/test_core_workers_*.py`

**Spec:**
- `desktalk/chat/idle_chat.py`, `lost_items.py`, `lens.py`, `daily_summary.py`, `vision_habits.py`
- `tests/integration/test_reminders_and_lens.py`

**Build:** each one as a `PeriodicWorker` built only from interfaces:
- `IdleChat`
- `LostItems`: also subscribes to `DetectionEvent`. Keep `items`, `is_lost_query(text)` and `answer(text)`
  for the chat route.
- `Lens`: uses `CameraHealthView`, `FrameSource`'s phone motion when there is one, `Preferences`, `Notifier`;
  publishes `CameraNotice`.
- `DailySummary`
- `VisionHabits`: keep `summary()` for the routes.

They use `ChatModel` (catch `AIUnavailable` and carry on), `Notifier`, `ChatVoice`, `DeskStatus`,
`RosterView`, `Profiles`, `HabitsView`, `EventBus`, `Clock`, `DeskState` and their settings sections. Their
private state moves out of `DeskState` into the worker. The same behavior as today, tested with fakes and
`FakeClock`.

## E1. Cameras, detector and the producer

**Owns:**
- `desktalk/adapters/camera/`, `desktalk/adapters/detector/`
- `desktalk/core/vision/producer.py`
- `tests/unit/test_camera_sources_*.py`, `tests/unit/test_detector_adapter_*.py`, `tests/unit/test_core_producer*.py`

**Spec:**
- `desktalk/vision/camera_sources.py`, `detector.py`, `producer.py`, `pairing.py`
- the plan's "Detection on the Pi" section (`ok` / `busy` / `off`)

**Build:**
- Camera sources (all pass `frame_source_contract.py`):
  - `PiFrameSource`: a `FrameSource` + `PictureInbox` that takes `UploadInfo`, keeps the Pi's health
    for `/api/health`, and passes `push_source_contract.py` too.
  - `PhoneFrameSource`: also passes `push_source_contract.py`; keeps the phone's tilt and rotation.
  - `WebcamSource`.
- `UltralyticsDetector`, implementing `Detector`:
  - uses `desktalk_shared.ultralytics_results`;
  - falls back to the CPU when the GPU fails;
  - runs one detection at a time;
  - raises `DetectorUnavailable`;
  - takes the model loader injected, so a fake YOLO object tests it without the real model.
  - The `end2end` and `rect` options per decision 9.
  - Passes `detector_contract.py` with a fake YOLO, and with the real model under `@pytest.mark.slow`.
- `DetectionProducer`, a `PeriodicWorker` that reads the `FrameSource`:
  - `ok`: use the Pi's detections (`detected_on='pi'`);
  - `busy`: live view only, no event;
  - `off` or no detector state: detect here.
  - It measures light, gives each picture to `PictureCheck`, updates `DeskState` (session start, live view
    pictures, light, person / motion times, seen labels) as today, publishes `DetectionEvent`, and draws the
    live view with `LiveViewPainter`.
  - When local detection fails it logs the error and keeps the live view going.
- The pair key helper, ported.

## E2. Desk rules and Lens's checks

**Owns:**
- `desktalk/core/vision/`, except `producer.py`, which belongs to E1
- `tests/unit/test_core_vision_*.py`

**Spec:**
- `desktalk/vision/relationships.py` (994 lines), `camera_health.py`, `desk_monitor.py`
- `tests/unit/test_vision_rules.py`
- `config/safety_rules.json`

**Build:**
- `relationships.py`, split by job: geometry, the desk outline and edges, danger zones, risky pairs,
  drawing. No module globals: categories and rules are passed in (rules come from a `KeyValueStore` key, so
  saving from the Settings page goes through it).
- A `LiveViewPainter` implementation:
  - boxes plus overlays;
  - highlights agents in the chat;
  - takes `DeskState` and `Profiles`.
- `CameraHealth`, implementing `PictureCheck` + `CameraHealthView`:
  - all its numbers come from settings;
  - keeps `issues()` and `phone_tip()` for the routes.
- `DeskMonitor`:
  - subscribes to `DetectionEvent`;
  - publishes `Alert`;
  - uses `Profiles`, `HabitsView.bump`, `DeskStatus`, `DeskState` and the rules;
  - the same alerts as today.

## F. Agents and habits

**Owns:**
- `desktalk/core/agents/`, `desktalk/core/habits/`
- `tests/unit/test_core_agents_*.py`, `tests/unit/test_core_habits_*.py`

**Spec:**
- `desktalk/agents/profiles.py`, `roster.py`, `desktalk/habits/*.py`, `Preferences` in `desktalk/settings.py`,
  and the question methods on `DeskTalkApp` (`camera_connected`, `warm`, `reminders_ready`, `user_active`,
  `open_todos`)
- their tests: `test_roster`, `test_profiles`, `test_protocols`, `test_habit_tracker`, `test_settings`'s
  preferences part

**Build:**
- `ProfileBook`, implementing `Profiles`:
  - built from typed `config/agents.yaml`;
  - learned personalities through a `KeyValueStore`;
  - uses `domain.AgentProfile`.
- `Roster`, implementing `RosterView`:
  - subscribes to `DetectionEvent`;
  - publishes `AgentJoined` / `AgentLeft`;
  - keeps `snapshot()` for the routes.
- The Protocols rules as pure functions returning `domain.Nudge`, with `repeat_after_sec` always given and
  coming from `config/protocols.yaml`.
- `HabitTracker`, implementing `HabitsView`:
  - habit days through a `KeyValueStore`, key `habits/<day>`;
  - subscribes to `DetectionEvent`;
  - publishes `HabitEvent`;
  - keeps `log()` and `day_times()` for the routes.
- `HabitReminders`, a `PeriodicWorker`:
  - uses `ChatVoice`, `Notifier`, `DeskStatus`, `RosterView`, `Profiles` and `HabitsView`;
  - publishes `NudgeSent`.
- `Preferences`, implementing `Preferences`: through a `KeyValueStore`, with protocols.yaml defaults.
- `DeskStatus`, implementing `DeskStatus`: from `DeskState`, the `FrameSource`, `Preferences` and settings.
- The sun times, ported.
