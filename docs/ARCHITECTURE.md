# Architecture

How a picture on the Raspberry Pi turns into "Cup joined the chat", what each part does, how the parts
talk to each other, and what happens when one of them fails.

> **Not tried on a real Pi yet.** The camera program and the detector container are tested on the Mac
> (with fakes, and the detector service in-process), but they haven't run on a Raspberry Pi so far, and
> the detector's Docker image hasn't been built yet. Everything below about the Pi is how it's built to
> work.

## From the Pi to your computer

Two programs run on the Pi. The **camera program** runs on the Pi's own Python 3 (it needs the camera,
which is fiddly inside Docker). The **detector service** runs YOLO inside a Docker container, so PyTorch
and the rest can't clash with the Pi's own packages. They talk over HTTP on the Pi itself (`127.0.0.1`
only), so the camera keeps going when the container stops, crashes or is being updated.

```
 Raspberry Pi                                           This computer (uv run python server.py)
 ──────────────────────────────────────────────────     ──────────────────────────────────────────────────
 camera program (python3 -m raspberry_pi.camera)
   camera → picture, 3 a second, kept on the Pi
     │
     ├──▶ detector container (Docker, 127.0.0.1:8100)
     │      POST /detect → what's in it
     │      (one picture at a time; the rest go as busy)
     │
     └──▶ one upload per picture, over Wi-Fi ─────────▶ POST /api/pi/frame  (desktalk/web/routers/pi.py)
            the picture + what was found                    pairing key, size limit, format check
            + ok / busy / off + how the Pi is doing       │
                                                          ▼
                                                        PiFrameSource  (keeps only the newest picture)
```

The pictures still go to your computer, because the live view, proof photos, Lens (brightness, blur,
glare), the developer page's trace pictures and the AI picture check (lost items) need them. Only YOLO moved to the Pi.
Your computer keeps YOLO too, for the phone and the webcam, and as the backup for the Pi.

## On your computer

```
 PiFrameSource · PhoneFrameSource · WebcamSource       (only the newest picture is kept)
      │
      ▼
 DetectionProducer   a background job, about 3 times a second:
      │                ok   → use the Pi's detections
      │                busy → live view only, no event
      │                off, or not the Pi → UltralyticsDetector on this computer
      │              then one name per box (agents.same_thing_overlap)
      │ one DetectionEvent per analysed picture
      ▼
 EventStream   (each consumer has its own queue and thread)
      ├─▶ EventLog          data/events/<day>/, compacted every 10 minutes
      ├─▶ ObjectStateTracker  present / absent per object, saved in SQLite → ObjectStateChanged
      ├─▶ Roster            agents join and leave       → AgentJoined, AgentLeft
      │                     (and the warnings' own list of everything on the desk)
      ├─▶ HabitTracker      water, breaks, light, phone → HabitEvent
      ├─▶ DeskMonitor       danger zones, edges, pets   → Alert
      ├─▶ LostItems         where your keys were        → Alert
      ├─▶ TracePhotoTaker   pictures for the developer page's trace (only when dev.enabled)
      └─▶ MessageGenerator  joins, alerts and habits    → chat messages
            │
            ▼
          DesktopAlerts ──── warnings, reminders, camera-seen sips ────▶ Mac notifications (osascript)
            │
            ▼
          MessageHub ──── SSE: GET /api/events ────▶ every open app page (web_app/)
            ▲
            └── background jobs that speak by themselves: HabitReminders (with the RuleRunner's nudges) and
                Lens through DesktopAlerts; IdleChat and DailySummary straight to the hub
```

**One thing, one name.** The detector sometimes names one thing twice on nearly the same box (a laptop that is
also "book"). Before the producer builds the `DetectionEvent`, two different names whose boxes are at least
`agents.same_thing_overlap` the same box (shared area over the area both cover) count as one thing, and only the
surer name is kept (`core/vision/one_name.py`). So everyone who listens, and Lens and the live view, see the same
list. Words on the ignore list take no part. Each name left out is logged once per stretch, at debug level.

**Who joins the chat.** Only the agents in `agents.can_join` (Computer, Cup, Phone, Snack, Book) join, once
the camera has been sure enough of one for long enough. Both numbers are the sensitivity step you pick in the
app (`agents.sensitivity_steps`, saved as `sensitivity` in `data/preferences.json`; an agent's own
`min_confidence` counts only when it is stricter). The habits count a box by the same how-sure number, so a cup
too weak for the chat counts no sips either. Everything else the camera sees is still on the
**on-desk list** (`DeskScene.on_desk()`, with its own steady numbers, `agents.on_desk_after_sec` and
`on_desk_min_confidence`), which the warnings, the live view's drawings and the app's "on the desk" list read.
A warning about something that nobody in the chat can say comes from Lens ("Heads up: scissors next to the
cell phone"). The roster also keeps the newest `dev.trace_events_kept` joins, leaves and refusals for each
kind of object, with why (`ChatJoins`, for the developer page).

**Sips.** A sip counts when a drink is lifted (its box rises more than `hydration.sip_lift_share` of the
picture), or goes missing from the pictures, and then rests again where it was (within `sip_back_share`),
between `sip_gone_min_sec` and `sip_gone_max_sec` after it was last seen resting. The second way is for a camera
that looks down with the bottle at the picture's edge: drinking takes it straight out of the picture. A hand
over the cup (a box named in `sip_hand_labels` over its resting spot in `sip_hand_share` or more of the pictures
it was missing), a cup taken away, or a covered camera (`sip_cover_share` of the desk vanishing for
`sip_cover_pictures` pictures in a row, or no pictures for longer than `sip_stall_sec` while it was up or
missing) is no sip (`SipWatch` in `core/habits/habit_reader.py`).

**To-dos.** `TodoFile` (`core/habits/todos.py`, saved as `data/todos.json`) keeps the to-do list the app and the
chat send, with due dates. The reminders read it through the desk status: the day before, on the day, each day
it's late, and a check-in for the ones with no date. Ticking one off is a `habit` event (`tasks` / `done`). The
AI is told today's date with the list, so "due Friday" becomes a real day.

The events the consumers make (`AgentJoined`, `HabitEvent`, `Alert` ...) go back on the same event
stream, so the event log keeps them and the message generator turns them into chat messages. A slow AI
reply only holds up the message generator's own queue, never the camera or the other consumers.

## The parts

| Folder / file | Job |
|---|---|
| `server.py` | Loads the settings, builds the app, prints the Pi command (or the phone link), runs the web servers with uvicorn. |
| `desktalk/config/` | Reads `config/*.yaml` and `.env` into typed, read-only settings, one file per config file: `app.py` (`settings.yaml`, and `AppConfig`, everything together), `habits.py` (`protocols.yaml`), `agents.py` (`agents.yaml`), `prompts.py` (`prompts.yaml`), `loader.py` (reading the files, and the plain-words errors), `base.py` (the building blocks). A bad value stops the start (see [Settings](#settings)). |
| `desktalk/bootstrap.py` | **Builds every part and connects them.** The only place that knows the real classes. `build(config)` returns a `DeskTalk` with every part as a named field, each typed with an interface; `start()` and `stop()` run and stop the background jobs. |
| `desktalk/ports.py` | The interfaces between the parts (below). |
| `desktalk/domain/` | Plain data, no files or network: `Detection`, `Frame`, the event classes, `AgentProfile`, `Nudge`, the desk right now (`SeenObject`, `SafetyNow`, `Photo`), the danger rules (`DeskRules`), names for pictures and shapes (`Picture`, `Box`, `Size`), day names, and the errors `DetectorUnavailable` / `AIUnavailable` with `plain_error`. |
| `desktalk/core/desk/` | **The desk right now**, in five parts, each with its own lock inside its methods: `LiveView` (the newest live view pictures and the light), `Scene` (what the camera sees on the desk), `DangerBoard` (the dangers right now and the latest alerts), `Memories` (what the characters said, the tracker panels, when you last typed), `Album` (the proof photos). `WholeDesk` passes calls on to the five, for the parts that still ask for several desk interfaces in one object. |
| `desktalk/core/vision/` | `producer.py` (pictures → detection events), `one_name.py` (one name per box), the desk rules (`rules.py`, `danger_zones.py`, `desk_edges.py`, `risky_pairs.py`, `findings.py`, `geometry.py`, `analyzer.py`), `desk_monitor.py` (safety alerts), Lens's checks on the picture (`camera_health.py`, the front, with `camera_measure.py` for the numbers and `camera_problems.py` for when a problem starts and ends), `proof_photos.py`. No OpenCV here: pictures go through `PictureTools`. |
| `desktalk/core/events/` | `EventStream`: publish / subscribe inside the program, a bounded queue and a thread per consumer. |
| `desktalk/core/agents/` | `ProfileBook` (who each object becomes, from `config/agents.yaml`), `Roster` (who's in the chat, and the on-desk list), `PresenceTracker`, `Slider` (`sensitivity.py`: the sensitivity step in use and how sure a box must be, for the roster and the habits). |
| `desktalk/core/object_states/` | `ObjectStateTracker`: detection events → a steady present / absent state for each kind of object (smoothing, then a small state machine), `object_state` events, and rows in the SQLite store. The person's state is the on/off switch for reminders and small talk. `TracePhotoTaker` (`trace_photos.py`, only when `dev.enabled`): a picture of an object with its box when it appears, when it joins the chat, and every `dev.trace_photo_every_sec` while it's there. |
| `desktalk/core/habits/` | `habit_rules.py` (the book's rules as plain functions), `HabitTracker` (`tracker.py`, the front: the habit buttons and the reminders due), `habit_days.py` (`HabitDay`, one day's numbers, and `HabitDays`, which loads, saves and starts a new day), `habit_reader.py` (reading habits from the detection events), `HabitReminders`, `PreferencesFile` (wake time, bedtime, quiet hours, sensitivity), `TodoFile` (`todos.py`, the to-do list with due dates), `DeskStatusService`, sun times. The rule engine: `rule_engine.py` checks the rules in `config/protocols.yaml` → `rules` against the object states and the habits logged lately (`HabitEvent`), and `RuleRunner` (`rule_runner.py`) runs it once every `rule_engine.check_every_sec`, keeps the nudges that are due for the reminders, and checks whether each nudge worked (`rule_outcome`). |
| `desktalk/core/chat/` | `MessageGenerator` (the front: events → messages, answering you), built from `alert_voice.py`, `nudge_voice.py`, `chat_answers.py` and `memory.py` (the only part that writes what the characters remember). `MessageHub` (messages → open app pages), `DesktopAlerts` (`desktop_alerts.py`: a `Notifier` around the hub that also pops warnings, reminders and camera-seen sips up as Mac notifications; the `notify` mark it reads never reaches the pages), `prompts.py` (fills in the blanks of the prompts in `config/prompts.yaml`), `names.py` (@mentions and who answers), `history.py`, `live_context.py` (what the AI is told about the desk), `replies.py`, `tracker_actions.py`. `workers/`: `IdleChat`, `LostItems` (with `lost_items_answers.py` and `lost_item_words.py`), `Lens`, `DailySummary`. |
| `desktalk/adapters/camera/` | `PiFrameSource`, `PhoneFrameSource`, `WebcamSource`, and the pairing key. |
| `desktalk/adapters/detector/` | `UltralyticsDetector`: YOLO on this computer. |
| `desktalk/adapters/llm/` | `OpenAICompatibleChatModel` (Gemini / OpenAI / Groq / Ollama) and `ResilientChatModel`, which wraps it with time limits, retries and a breaker. |
| `desktalk/adapters/notify/` | `MacNotifications`: the Mac's own pop-ups through `osascript`, from a small queue (`notifications.queue_size`) on one thread, each with a time limit, failures only logged; `NoNotifications` on other systems or when switched off (`make_notifications`). |
| `desktalk/adapters/picture/` | What's done to pictures, with OpenCV: `OpenCvPictureTools` (decode, encode, shrink, crop, brightness, sharpness ...) and `DeskPainter` (the boxes and desk drawings on the live view, with every color, width and size from settings → `drawing`). |
| `desktalk/adapters/storage/` | `EventLog` (the files in `data/events/`), `JsonFileStore` (small JSON files, saved all-or-nothing), `SqliteStateStore` (the object states' history, `object_states.database`) and `TracePhotoFiles` (`trace_photo_files.py`: the developer page's trace pictures in `dev.trace_photos_folder`, the newest `dev.trace_photos_kept` per kind of object). |
| `desktalk/adapters/dev/` | For the developer page, only built when `dev.enabled` is on: `PytestRunner` (`test_runner.py`: runs the checks in `dev.checks`, or some tests with `dev.test_command`, in a child process, one run at a time, stopped after `dev.run_timeout_min`), with `pytest_report.py`, a pytest plugin that writes one line per finished test so the page sees each one as it finishes; `SqliteBrowser` (`sqlite_browser.py`: the SQLite databases opened read-only, one statement that only reads, at most `dev.sql_max_rows` rows, stopped after `dev.sql_timeout_sec`); `DataFolder` (`data_files.py`: the files in `data/` and the start of one, never anything outside it). |
| `desktalk/runtime/` | `Supervisor` (runs the background jobs and starts them again after a crash), `Scheduler`, `SystemClock` (the shared one), logging. |
| `desktalk/web/` | FastAPI. `app.py` makes the two servers (the main one, and the phone page's own HTTPS one), `routers/` has the routes by area (`pi`, `phone`, `live`, `video`, `chat`, `habits`, `log`, `settings`, `health`, `dev`), `schemas.py` the request and reply shapes, `streams.py` SSE and the video, `serve.py` runs uvicorn. The routes only see the interfaces, plain data and settings on `DeskTalk`. |
| `desktalk/web/routers/dev.py` | The developer page's routes, `/api/dev/...` (the list is in [API.md](API.md#developer-page-this-computer-only-15)). Every one only answers this computer, and only its own page (`DevOnly` in `deps.py`: the `FromHere` check plus a `Host` and an `Origin` check). It's only added to the main server when `dev.enabled` is on, never to the phone's. `app.py` serves the built page (`dev.ui_folder`) at `/dev/`, or a line saying how to build it. |
| `shared/desktalk_shared/` | Used on the Pi and on this computer, standard library only: `detection.py` (`Detection`, `BBox`), `wire.py` (the upload format), `detector_api.py` (the detector service's requests and replies), `ultralytics_results.py` (YOLO results → `Detection`s, the same on both sides), `backoff.py` (`Backoff`: the wait after each failure in a row, doubling up to a most), `clock.py` (`SystemClock`, the one real clock), `workers.py` (`WorkerThread`: a job in a thread of its own that starts again after a crash). |
| `raspberry_pi/camera/` | The camera program: `cli.py` (the command line and loading the settings), `main.py` (the loop: take, save, ask, send), `workers.py` (the threads, and `build_program`, which connects the parts), `cameras.py`, `photo_store.py`, `cleaner.py`, `detector_client.py`, `uploader.py`, `health.py`, `watchdog.py`, `self_test.py`, `settings.py`. [README](../raspberry_pi/camera/README.md). |
| `raspberry_pi/detector_service/` | The detector service: `Dockerfile`, `compose.yaml`, `.env`, and `desktalk_detector/` (FastAPI: `POST /detect`, `GET /health`, `PUT /model`; converts `.pt` models to ncnn and keeps the last good one). [README](../raspberry_pi/detector_service/README.md). |
| `benchmarks/` | `deskbench`: times YOLO models on the Mac and on the Pi (in its own Docker image), to pick the Pi's model. [README](../benchmarks/README.md). |
| `web_app/`, `phone_camera/` | The app's page, and the page a phone opens to be the camera. They keep no copies of the settings: the app page asks `GET /api/ui_settings` when it opens (refresh times, limits, the palette, Lens's card, how long a chat reply may take) and shows its "can't reach Desk Talk" note until it gets them; the phone page asks `GET /api/phone/config` (picture size and quality). |
| `dev_ui/` | The developer page at `http://localhost:5000/dev/`: Checks, Live, System, Database and Storage tabs. Vite + React + TypeScript, with its own tests (Vitest) and `package-lock.json`; `npm run build` puts it in `dev_ui/dist/`. It keeps no numbers of its own: it asks `GET /api/dev/settings` once when it opens. The app page stays plain JavaScript with no build step. [README](../dev_ui/README.md). |

There are three uv packages in one workspace with one `uv.lock`: `desktalk` (this folder), `desktalk-shared`
(`shared/`) and `desktalk-detector` (`raspberry_pi/detector_service/`). The camera program isn't installed
with uv: it runs on the Pi's own `python3` and finds `desktalk_shared` in the cloned repo's `shared/` folder.

## Interfaces

Every part is written against small interfaces (`typing.Protocol`s in `desktalk/ports.py`), never against
another part's class. A part asks in its constructor for only the interfaces it uses and its own section
of the settings, for example `Roster(bus, profiles, scene, config.agents, config.presence, preferences, on_desk=..., joins_kept=...)`.
`desktalk/bootstrap.py` passes in the real versions; tests pass in the fakes from `tests/fakes/`.

| Interface | What it promises | Real version | Fake for tests |
|---|---|---|---|
| `Clock` | `now()`, `monotonic()`, `sleep()`, `local_hour(now)`, `local_day(now)` | `SystemClock` (`shared/desktalk_shared/clock.py`) | `FakeClock` (you move time by hand) |
| `PeriodicWorker` | `name`, `tick(now)` → seconds until the next round | the producer, `Scheduler`, `RuleRunner`, `HabitReminders`, `Lens`, `IdleChat`, `LostItems`, `DailySummary`, `OllamaKeepAlive` | tests call `tick()` themselves |
| `Detector` | picture → list of `Detection`, or `DetectorUnavailable` with a reason | `UltralyticsDetector` | `FakeDetector` |
| `FrameSource` | the newest picture, `kind`, `connected`, `fps` | `PiFrameSource`, `PhoneFrameSource`, `WebcamSource` | `FakeFrameSource` |
| `PictureInbox` | `push(jpeg, info)` for a camera that sends us pictures | `PiFrameSource`, `PhoneFrameSource` | `FakeFrameSource` |
| `PiUploads`, `PiStatus` | what came with the Pi's newest picture, how far its clock is off; `pi_info()` and `status()` for the pages | `PiFrameSource` | `FakePi` |
| `PhoneCamera`, `PhoneControls` | the phone's recent tilt, whether you stopped it; `set_motion()` and `mark_stopped()` for the phone routes | `PhoneFrameSource` | `FakePhone` |
| `EventBus` | publish, subscribe by event class, `wait_idle()`, `stats()` | `EventStream` | `SyncEventBus` |
| `EventStore` | write, read a day, compact, prune | `EventLog` | `InMemoryEventStore` |
| `KeyValueStore` | load / save small JSON documents | `JsonFileStore` | `InMemoryKeyValueStore` |
| `ChatModel` | messages (or a picture) → reply, or `AIUnavailable` with a reason | `OpenAICompatibleChatModel`, wrapped in `ResilientChatModel` | `FakeChatModel` |
| `Notifier` | send a chat message to every open app page | `MessageHub`, and `DesktopAlerts` around it | `RecordingNotifier` |
| `DesktopNotifications` | `show(title, text)`: a pop-up of the computer's own, never waiting or raising | `MacNotifications`, `NoNotifications` | `FakeDesktopNotifications` |
| `ChatVoice` | a character's words for a habit reminder | `MessageGenerator` | `FakeChatVoice` |
| `RosterView` | who's in the chat | `Roster` | `StaticRoster` |
| `Profiles` | who each detected object becomes | `ProfileBook` | `FakeProfiles` |
| `HabitsView`, `HabitLog`, `HabitsLifecycle` | today's habits and the reminders ready to send; logging a habit by hand; saving the day (only `bootstrap` calls it) | `HabitTracker` | `FakeHabits` |
| `Preferences` | wake time, bedtime, quiet hours, the sensitivity step | `PreferencesFile` (`core/habits/preferences.py`) | `FakePreferences` |
| `TodoList` | the to-do list with due dates: `items()`, `replace()` (a bad date becomes none; ticking one off is a `HabitEvent`), `has_list()` | `TodoFile` (`core/habits/todos.py`) | `FakeTodoList` |
| `RuleNudges` | the rule engine's nudges ready to send (`nudges(now)`), and which one went out (`note_fired()`) | `RuleRunner` (`core/habits/rule_runner.py`) | `FakeRuleNudges` |
| `DeskStatus` | camera on? someone at the desk? (the person's object state) reminders allowed? | `DeskStatusService` (`core/habits/desk_status.py`) | `FakeDeskStatus` |
| `ObjectStatesView` | each object's state now (`snapshot()`), what changed lately (`changes(since)`) | `ObjectStateTracker` | `FakeObjectStates` |
| `ObjectStateStore` | the object states' history: add rows, newest state per object, the changes | `SqliteStateStore` | `InMemoryStateStore` |
| `CameraHealthView`, `PictureCheck` | Lens's checks on the picture | `CameraHealth` | `FakeCameraHealth`, `FakePictureCheck` |
| `LiveViewPainter` | boxes and desk drawings on the live view | `DeskPainter` | `FakePainter` |
| `PictureTools` | decode and encode JPEGs, shrink, crop, grey, brightness, glare, sharpness, how much two pictures differ | `OpenCvPictureTools` | `FakePictureTools` |
| `PhotoTaker` | save a proof photo of the live view, cropped around some boxes | `ProofPhotos` | `FakePhotoTaker` |
| `DeskLayout`, `DeskRulesView` | the desk outline and the phone's tilt when it was marked; the danger rules in use, saving changes from the Settings page | `SafetyRules` | `FakeDeskRules` |

**The desk right now** is five interfaces, one per part in `desktalk/core/desk/`. Each method does its own
locking, so nobody outside ever holds a lock:

| Interface | What it promises | Real version | Fake for tests |
|---|---|---|---|
| `LivePictures` | the newest plain and boxed picture per camera, the camera numbers, the picture size, the light | `LiveView` | `FakeLivePictures` |
| `DeskScene` | who's in the chat (`members()`) and everything on the desk (`on_desk()`), as `SeenObject`s; everything seen, first and last seen, the person, motion, the vision scan's labels | `Scene` | `FakeDeskScene` |
| `SafetyBoard` | the dangers right now (`SafetyNow`), since when a pair has been a danger, the latest alerts | `DangerBoard` | `FakeSafetyBoard` |
| `CharacterMemory` | what each character said lately, the tracker panels, when you last typed | `Memories` | `FakeCharacterMemory` |
| `PhotoAlbum` | add a proof photo, get one, the newest ones | `Album` | `FakePhotoAlbum` |

**The developer page** has six interfaces of its own. Only the `/api/dev` routes use them, and `DeskTalk`'s
`test_runner`, `databases`, `data_files` and `trace_photos` are None when `dev.enabled` is off:

| Interface | What it promises | Real version | Fake for tests |
|---|---|---|---|
| `TestRunner` | the list of tests (`collect()`), start the checks or some tests (`RunAlreadyGoing` while a run is going), a run's status and new printout, stop it | `PytestRunner` (`adapters/dev/test_runner.py`) | `FakeTestRunner` |
| `DatabaseBrowser` | the databases, their tables (with row counts and the `CREATE` line), one read-only query (`QueryRefused` for anything else, or one that takes too long) | `SqliteBrowser` (`adapters/dev/sqlite_browser.py`) | `FakeDatabaseBrowser` |
| `DataFiles` | every file and folder in `data/`, the start of one file (`ValueError` for a path outside it) | `DataFolder` (`adapters/dev/data_files.py`) | `FakeDataFiles` |
| `ChatJoins` | each kind of object's newest joins, leaves and refusals, with how sure, how long it was seen, inside the desk or not, and the sensitivity step (`joins(label)`, oldest first) | `Roster` | `FakeChatJoins` |
| `TracePhotos` | each kind of object's trace pictures (`photos(label)`, oldest first) and one picture's JPEG (`jpeg(id)`) | `TracePhotoFiles` | `FakeTracePhotos` |
| `SmoothingTraceView` | what the smoothing made of each object's last `object_states.trace_pictures` pictures (confidence, the votes, seen or not, the state, the wait), oldest first; `[]` for an object never seen | `ObjectStateTracker` | `FakeSmoothingTrace` |

A part asks only for the desk interfaces it uses (the roster gets `scene`, idle chat gets `character_memory`).
The producer, proof photos, the desk monitor, the desk status, lost items, the message generator and the
lost items still ask for several at once; `bootstrap.py` gives them a `WholeDesk`, which keeps nothing
itself and passes each call on to the part that owns it.

The last section of `ports.py` is what the web layer asks of the running app, so a route only sees what it
may call: `ChatFeed` (`MessageHub`), `ChatReplies` (`MessageGenerator`), `RosterList` (`Roster`),
`CameraTips` (`CameraHealth`), `DetectionStats` (the producer), `LostItemsView`,
`HabitTracking` (`HabitTracker`), `ScheduledJobs` (`Scheduler`) and `Jobs` (`Supervisor`). `DeskTalk`
in `bootstrap.py` types its fields with these (and with `ObjectStates`, in `bootstrap.py` itself: the
`ObjectStatesView` plus `save_snapshot()`, which only the scheduler and `stop()` call).

**Contract tests** keep the fakes honest: each interface that talks to the outside world, and each desk
interface, has one test suite in `tests/contracts/`, and the fake and every real version run the same
suite. The fakes run them in `tests/contracts/`; the real versions run them there too
(`test_desk_parts_contracts.py`, `test_real_parts_pass_new_contracts.py`, `test_opencv_picture_tools.py`)
or next to their own tests in `tests/unit/` (for example `TestEventLog` runs `EventStoreContract`). The
detector service, asked over its HTTP API, passes the same `Detector` suite as `UltralyticsDetector`.

`tests/unit/test_layers.py` checks the layers: `desktalk/core/` and `desktalk/domain/` import no OpenCV,
numpy, web or AI libraries (and nothing from `adapters/`, `web/`, `runtime/` or `bootstrap`), and
`desktalk/web/` imports only interfaces, plain data, settings and the app.

The Pi's two programs are built the same way, with their own small pieces: the camera program's
`workers.py` (`build_program`) puts together a camera, a `PhotoStore`, a `DetectorClient`, an `Uploader`,
a `HealthReader`, a `Watchdog` and a `Cleaner`, and `cli.py` reads the flags and the settings and starts it;
the detector service's `desktalk_detector/bootstrap.py` puts together its model folder, model manager and
web API.

## The upload: format 2

One `POST /api/pi/frame` per picture, with the pairing key in the `X-Desk-Key` header, as
`multipart/form-data` with two parts:

```
info      JSON:
          {"version": 2, "frame_id": "pi-000123", "taken_at": 1792569600.25, "lux": 312.5,
           "detector": "ok", "detector_reason": "",
           "detections": [{"label": "cup", "conf": 0.83, "bbox": [x1, y1, x2, y2], "center": [cx, cy]}],
           "detect_ms": 640, "model": "yolo26m-objv1-150.pt",
           "health": {"camera": "picamera2", "photos_stored": 42, "disk_free_mb": 1234,
                      "last_cleanup_ts": 1792569000, "cpu_temp_c": 61.2, "throttled": "0x0"}}
picture   the JPEG
```

Both sides use the same code for it: `shared/desktalk_shared/wire.py` (`encode_upload` on the Pi,
`decode_upload` here). An upload in another version gets a 400 that says which side to update and how.
The older header-only upload (format 1: the JPEG as the whole body, `X-Frame-Id`, `X-Lux` ... headers) is
still accepted for now.

The camera program gets its settings the same way it always did: `GET /api/pi/config` (with the pairing
key) returns the `pi:` section of `config/settings.yaml`, `pi.detector` included.

## Detector states: ok, busy, off

Every upload says what this computer should do with the picture:

| State | On the Pi | On this computer |
|---|---|---|
| `ok` | The container found these things. | Use the Pi's detections (boxes outside the picture are dropped). The event says `detected_on: pi`. |
| `busy` | The container was still working on an earlier picture. Only one picture is at the container at a time. | Show it in the live view, with the newest boxes. No event. |
| `off` | The container couldn't answer: not running, still loading or converting its model, too slow (`pi.detector.wait_per_picture_sec`), a different API version, or `--no-detector`. The reason goes along in `detector_reason`. | Run YOLO on it here (`UltralyticsDetector`). The event says `detected_on: computer`. |

So detection on the Pi can be slower than the camera without the live view getting choppy and without a
queue building up. An `ok` picture can arrive after `busy` pictures that were taken later (it waited for
its detections); the live view doesn't jump back to it (`camera.keep_newer_picture_sec`).

After `pi.detector.failures_before_pause` failures in a row the camera program stops asking the container
for a while (`pause_start_sec`, doubling up to `pause_max_sec`), asks its `/health` every
`health_check_every_sec` meanwhile, and goes back to it as soon as it's ready. A container that is still
converting a model, or busy with another picture, doesn't count as failing.

What the Pi said lately is in `GET /api/health` → `pi`: upload format, detector state and reason, how long
detection took, which model, and how far the Pi's clock is off. Lens says it in the chat when the Pi's
detector has been off for `lens.wait_before_saying_sec.pi_detector`.

## Events

Events are typed (`desktalk/domain/events.py`). On the event stream they're objects; in the event log and
the web API they're the same flat JSON as always: `id`, `ts` (when it happened, by this computer's clock),
`type`, and the event's own data.

| type | Class | Made by | Example data |
|---|---|---|---|
| `detection` | `DetectionEvent` | the producer | `frame_id`, `source`, `size`, `detected_obj: [{label, conf, bbox, center}]`, `person`, `moved`, `light: {brightness, lux}`, `taken_at`, `detected_on` |
| `agent_joined` / `agent_left` | `AgentJoined` / `AgentLeft` | the roster | `agent`, `name`, `label`, `conf` / `stayed_sec` |
| `alert` | `Alert` | desk monitor, lost items | `kind: proximity` + `agent` and details like `snapshot`; or `kind: scenario` + `scenario`, `feature`, `text` |
| `habit` | `HabitEvent` | the habit tracker | `habit: hydration`, `action: sip`, `say: "Sip detected!..."` |
| `nudge` | `NudgeSent` | habit reminders | `habit`, `rule`, `agent`, `evidence`, `text` |
| `rule_outcome` | `RuleOutcome` | the rule engine | `rule`, `habit`, `result: worked / missed`, `after_sec` |
| `object_state` | `ObjectStateChanged` | the object state tracker | `change: appeared / removed / moved`, `object`, `prev_state`, `new_state`, `device_id`, `conf_avg`, `bbox`, `dwell_sec` |
| `camera` | `CameraNotice` | Lens | `key: dark`, `kind: problem`, `text` |
| `compaction` | `Compaction` | the event log | `frames`, `intervals`, `from`, `to` |

The event stream drops the oldest **picture** events of a consumer that falls behind
(`event_stream.queue_size`), and counts them; joins, leaves, alerts, habits and nudges are never dropped.

## The event log (discrete → continuous)

```
data/events/2026-10-18/
  detections.jsonl   one line per analysed picture (3 a second), until the next compaction
  presence.jsonl     {"type": "presence", "label": "cup", "interval": [1792345678.1, 1792347461.9], "frames": 5310, ...}
  events.jsonl       joins, leaves, alerts, habits, nudges, camera problems, compactions
```

Every 10 minutes (`event_log.compact_every_min`) the scheduler runs `EventLog.compact()`: per-picture
detections become one interval per object per stretch on the desk (a gap longer than
`event_log.merge_gap_sec` starts a new interval), intervals that touch the last compaction's are joined,
and the per-picture lines are deleted. While it works, the pictures it's compacting wait in
`detections-<time>.compacting.jsonl`; if it stops part-way, the next compaction picks them up. Days older
than `event_log.keep_days` are deleted (`prune()`, every `event_log.prune_every_min`). The Event log page
in the app shows only the events that matter to you (`GET /api/log/events`: sips, breaks, food, finished
to-dos, sunlight, reminders, warnings and camera problems); the intervals stay in the files.

The rest of `data/` (settings → `storage.folder`, kept by `JsonFileStore`): `preferences.json`,
`habits/<day>.json`, `todos.json`, `learned_personalities.json`, `daily_summary.json`; and, when the developer
page is on, `trace_photos/<object>/` (`TracePhotoFiles`). The log file is
`data/logs/desktalk.log` (settings → `logging.folder` and `logging.file_name`).

The pictures themselves stay on the Pi and are deleted every 10 minutes (`pi.delete_every_min`). This
computer keeps only a few proof photos in memory.

## Background jobs

Everything that runs over and over is a `PeriodicWorker`: it has a `name` and a `tick(now)` that does one
round and returns how many seconds to wait before the next. It never starts a thread or sleeps itself.

The `Supervisor` (`desktalk/runtime/supervisor.py`) runs each job in its own thread. When a round raises,
it logs the error with the job's name and tries again after `supervisor.restart_start_sec` (1 s), then
twice that, up to `supervisor.restart_max_sec` (60 s). One good round starts the count over. (The waits come
from `Backoff` in `shared/desktalk_shared/backoff.py`, the same one the Pi's uploader and detector client use.) It never gives
up, so one bad AI reply can't switch a feature off until the app restarts. `GET /api/health` → `jobs`
shows each job: running, waiting after an error, or stopped, with its failures and last error.

The jobs, and how long each waits before its first round (`DeskTalk.background_jobs()`):

| Job | What it does | First round after |
|---|---|---|
| `DetectionProducer` | pictures → detection events | at once |
| `Scheduler` | timed jobs: compact the event log, delete old days, save today's habits, save the object states | at once |
| `RuleRunner` | checks the rules in `protocols.yaml` → `rules`, and whether earlier nudges worked | `rule_engine.check_every_sec` |
| `HabitReminders` | one Protocols nudge at a time | `reminders.check_every_sec` |
| `IdleChat` | small talk between agents | `idle_chat.start_delay_sec` |
| `LostItems` | says when a tracked item (keys, phone ...) has been gone a while; looks for small things with a vision AI on this computer, if there is one | `lost_items.start_delay_sec` |
| `Lens` | camera problems, a hot Pi, a Pi clock that's off, the Pi's detector being off | `lens.check_every_sec` |
| `DailySummary` | the end-of-day recap | `daily_summary.start_delay_sec` |
| `OllamaKeepAlive` | keeps Ollama's model loaded (only when the chat runs on Ollama) | `llm.ollama_keepalive_start_sec` |

Tests don't use threads for these: they call `tick()` with a `FakeClock`.

The event stream's consumers are separate: each has its own thread inside `EventStream`. A consumer whose
handler raises is logged and counted (`/api/health` → `events`), and keeps going.

**Starting and stopping.** The main web server's FastAPI "lifespan" (`desktalk/web/app.py`) calls
`desktalk.start()` when it starts and `desktalk.stop()` when it stops. `stop()` goes in this order: stop
the jobs, let the consumers finish the events still waiting, save the object states, save today's habits,
compact the event log, show the Mac notifications still waiting, close the event stream (and let go of a
webcam), close the object states database.
A step that fails is logged and the rest still happen.

**On the Pi**, the camera program has a few threads of its own (the detector worker, the uploader, the
settings watcher, the cleaner that deletes old pictures), so a slow container, Wi-Fi or SD card never holds
up the camera. Another, the watchdog, ends the program when the camera stops giving pictures, so systemd
starts it again. Each is a `WorkerThread` (`shared/desktalk_shared/workers.py`): one that crashes is logged
and goes on after `pi.pause_after_error_sec`. The container is started again
by Docker (`restart: unless-stopped`).

## When something fails

| What fails | What happens |
|---|---|
| **The detector container is down, crashing or still starting** | Docker starts it again. The camera program waits only `pi.detector.wait_per_picture_sec` per picture, then sends it as `off` with the reason, and this computer detects it. After a few failures in a row it stops asking for a while and checks `/health` now and then. `/api/health` and Lens say the Pi's detection is off, and why. |
| **Detection is slower than the camera** | Only one picture at a time goes to the container; pictures taken meanwhile go out as `busy` (live view only). No queue builds up. |
| **The container uses too much memory** | Memory limit in `compose.yaml` (`DETECTOR_MEMORY_LIMIT` in `.env`): Docker restarts it instead of the Pi freezing. |
| **The model is missing or won't convert** | The container's `/health` says `error` and why, keeps the last working model if there is one, and tries again with growing waits. Meanwhile the pictures go out as `off` and this computer detects them. On this computer, a model that won't load doesn't stop the start: the Pi's own detections keep working. |
| **The Pi gets hot and slows down** | The camera program sends the processor temperature and the slow-down flags with every picture. Lens says so (from `pi.hot_warning_c`); the detection time is in `/api/health`. |
| **This computer is away** (off, Wi-Fi drop) | The Pi keeps taking and saving pictures, and tries again after a wait that grows each time (`pi.upload_retry_start_sec` up to `pi.upload_retry_max_sec`), sending only the newest picture. No catching up later: old detections would make agents join minutes late. The event log shows the gap. |
| **The Pi and this computer run different versions** | Every upload carries the format version, and the detector's replies carry its API version. A mismatch gets a clear message saying what to run, instead of half-working. |
| **The Pi's clock is off** | This computer uses its own clock for all timing. When the Pi's clock is more than `pi.clock_off_warning_sec` off, Lens says so. |
| **The Pi reboots** | systemd starts the camera program again (when it's set up as a service); Docker starts the container on boot. |
| **The camera hangs** | After `pi.restart_after_missed_pictures` picture times without a picture, the watchdog ends the camera program and systemd starts it again. |
| **A background job on this computer throws** | The `Supervisor` logs it and runs it again after 1 s, 2 s, 4 s ... up to 60 s. `/api/health` shows each job's state. |
| **The AI is down, slow or out of quota** | `ResilientChatModel`: a time limit, retries with growing waits, a breaker that stops calling for a while after repeated failures, and background chatter that rests after a rate limit so the quota is kept for replying to you. Characters still speak with built-in lines (alerts use the agent's own `too_close` line, reminders the nudge's ready-made text); your own messages get a plain reason instead of a reply. |
| **The camera stops** | No pictures, no events: analysis pauses and nobody leaves the chat. Lens says the camera is off; `/api/health` → `camera` shows it. |
| **A consumer is slow** | Its bounded queue drops the oldest *picture* events (never joins, alerts or habits) and counts the drops. An open app page that stops reading is dropped by the `MessageHub`, and the browser connects again. |
| **A data file is half-written or broken** | Every save is all-or-nothing (temp file + rename). A broken JSON file reads as empty and is logged; broken lines in `.jsonl` files are skipped and logged, never fatal. |
| **Shutdown** | The lifespan stops everything in order, saves the habits and compacts the log (see above). |
| **Bad input on `/api/pi/frame`** | Wrong pairing key: 403. Too big (`web.pi_upload_max_mb`): 413. The `info` part is checked by `desktalk_shared.wire`: 400 with what's wrong. Not a JPEG: 400. Boxes outside the picture are dropped. |

Every number in this table is in `config/settings.yaml` (or, for the container, in
`raspberry_pi/detector_service/.env`). Logs go to the terminal and to a rotating file (`data/logs/` on this
computer; `journalctl -u desk-talk-camera` and `docker compose logs` on the Pi).

## Settings

`desktalk/config/` reads, in this order (later wins):

```
config/settings.yaml  →  config/settings.local.yaml (yours, not in git)  →  a few model names from .env
```

plus `config/agents.yaml`, `config/protocols.yaml`, `config/prompts.yaml` and the AI keys from `.env`
(hidden when printed). The result is one read-only `AppConfig` with a typed section per area
(`config.camera`, `config.pi`, `config.supervisor`, `config.prompts` ...). Each part gets only its own
section, so it can't depend on the rest.

**Wording lives in `config/prompts.yaml`**: every prompt the AI gets, Lens's wording, the habit nudges and
tips, the desk alerts and the fixed replies. The code only fills in the blanks (`{on_waking_ml}`,
`{break_min}` ...), and the numbers in them come from the settings. Each text may only use the blanks its
code fills in (`BLANKS` in `desktalk/config/prompts.py`); an unknown blank stops the start with the text's
name, and `tests/unit/test_prompts.py` checks every one.

A missing, misspelled or bad setting **stops the start** with a message that names the file, the setting
and what's wrong, for example:

```
Desk Talk could not start, a setting needs fixing:
config/settings.local.yaml: camera.fps must be a number above 0 (got "fast")
```

What each setting means is in the comments of `config/settings.yaml`. New numbers go there, with a plain
comment, and get a typed field in `desktalk/config/app.py`.

On the Pi, the camera program reads the `pi:` section: first the copy of `config/settings.yaml` in the
repo on the Pi, then what your computer sends (`/api/pi/config`, asked again every
`pi.settings_check_every_sec`), then its command-line flags. New `pi.detector` settings go to the
container right away (`PUT /model`); anything else needs the camera program restarted. The container's own
settings (port, memory limit, retry waits, Docker's health check, log file sizes) are in
`raspberry_pi/detector_service/.env`.

## Tests

| Folder | What | Needs |
|---|---|---|
| `tests/unit/` | Each part on its own with fakes and a `FakeClock`: `core/`, `adapters/`, `runtime/`, settings, `desktalk_shared`, the camera program, the detector service (FastAPI's `TestClient` and a fake YOLO). | nothing |
| `tests/contracts/` | One suite per interface. The fakes run them here; the real versions run them in `tests/unit/`. | nothing |
| `tests/integration/` | The whole app built by `bootstrap.build()` with a fake detector, a fake AI and a `FakeClock` (`app_kit.py`), and the web API through FastAPI (`web_kit.py`): pictures through the whole pipeline, background jobs, uploads, streams. | nothing |
| `tests/characterization/` | Every route of both web servers: status code, content type, a few headers and the JSON shape, saved in `snapshots/`. `test_route_coverage.py` fails for a route without a case in `cases.py`. Re-record a snapshot you changed on purpose with `DESKTALK_RECORD_SNAPSHOTS=1`, and check the diff. | nothing |
| `tests/slow/` and other `-m slow` tests | The real YOLO model: the ncnn model finds the same objects as the `.pt`, the detector service with a real model. Skipped when the model files aren't in `models/`. | model files |
| `tests/docker/` | Builds the detector's image, starts it with `docker compose`, checks `/health`, `/detect`, the `127.0.0.1` port and the memory limit (`-m docker`, opt-in). | Docker, the internet |
| `tests/e2e/` | The real app page in a headless Chromium (`-m e2e`, opt-in), against the whole app on a real web server with a fake detector and a fake AI (`tests/e2e_kit/server.py`): the page opens, a bottle joins the chat, a chat reply, a habit button, the page coming back after the server restarts, the demo without a server. Skipped when Chromium isn't installed. `test_full_stack.py` (`-m docker`) runs the camera program → the detector container → the server. | Chromium (`uv run playwright install chromium`, once); Docker for the full stack |
| `tests/fakes/` | The fakes (see the interface tables). | |
| `dev_ui/` (`*.test.ts`, `*.test.tsx`) | The developer page on its own (Vitest, Testing Library, a pretend browser page): the API client, the ports `npm run dev` reads from the settings, each tab with the server's replies faked. Run with `npm test` in `dev_ui/`; no server needed. | Node 22 (`npm install` in `dev_ui/`, once) |

**On your Mac**, in the `desk-talk` folder:

```bash
uv sync --all-packages
uv run pytest -m "not slow"                # the fast tests (what you run most)
uv run pytest tests/characterization       # just the routes
uv run pytest -m slow                      # the real model
uv run pytest -m e2e                       # the real page in a browser (uv run playwright install chromium, once)
uv run pytest -m docker                    # the detector's Docker image, and the full stack through it
uv run ruff check .                        # lint
uv run pyright                             # types: 0 errors
uv run vulture                             # dead code: prints nothing
```

GitHub runs `uv run ruff check .`, `uv run pyright`, `uv run vulture --min-confidence 80` and
`uv run pytest -m "not slow and not docker and not e2e" --cov` on every push and pull request
(`.github/workflows/tests.yml`). `--cov` fails the run when the tests cover less than `fail_under` in
`pyproject.toml` (branches counted too); it may only go up. `pyrightconfig.json` lists the files that had
type errors when pyright was switched on; the list only gets shorter. `tests/vulture_allow.py` lists the
names vulture can't see being used, each with what uses it. The default test run never needs the network,
an API key, a camera, a Pi, Docker or a browser. A second job (`page`) runs `npm ci`, `npm test` and
`npm run build` in `dev_ui/` with Node 22.

## Agents

See [AGENT_SYSTEM.md](AGENT_SYSTEM.md).
