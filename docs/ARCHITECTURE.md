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
glare) and the AI picture checks (lost items, vision habits) need them. Only YOLO moved to the Pi.
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
      │ one DetectionEvent per analysed picture
      ▼
 EventStream   (each consumer has its own queue and thread)
      ├─▶ EventLog          data/events/<day>/, compacted every 10 minutes
      ├─▶ Roster            agents join and leave       → AgentJoined, AgentLeft
      ├─▶ HabitTracker      water, breaks, light, phone → HabitEvent
      ├─▶ DeskMonitor       danger zones, edges, pets   → Alert
      ├─▶ LostItems         where your keys were        → Alert
      └─▶ MessageGenerator  joins, alerts and habits    → chat messages
            │
            ▼
          MessageHub ──── SSE: GET /api/events ────▶ every open app page (web_app/)
            ▲
            └── background jobs that speak by themselves: HabitReminders, Lens, IdleChat, DailySummary
```

The events the consumers make (`AgentJoined`, `HabitEvent`, `Alert` ...) go back on the same event
stream, so the event log keeps them and the message generator turns them into chat messages. A slow AI
reply only holds up the message generator's own queue, never the camera or the other consumers.

## The parts

| Folder / file | Job |
|---|---|
| `server.py` | Loads the settings, builds the app, prints the Pi command (or the phone link), runs the web servers with uvicorn. |
| `desktalk/config.py` | Reads `config/*.yaml` and `.env` into typed, read-only settings. A bad value stops the start (see [Settings](#settings)). |
| `desktalk/bootstrap.py` | **Builds every part and connects them.** The only place that knows the real classes. `build(config)` returns a `DeskTalk` with every part as a named field; `start()` and `stop()` run and stop the background jobs. |
| `desktalk/ports.py` | The interfaces between the parts (below). |
| `desktalk/domain/` | Plain data, no files or network: `Detection`, `Frame`, the event classes, `AgentProfile`, `Nudge`, and the errors `DetectorUnavailable` / `AIUnavailable`. |
| `desktalk/core/vision/` | `producer.py` (pictures → detection events), the desk rules (`rules.py`, `danger_zones.py`, `desk_edges.py`, `risky_pairs.py`, `analyzer.py`), `desk_monitor.py` (safety alerts), `camera_health.py` (Lens's checks on the picture), `live_view.py` (boxes on the live view), `proof_photos.py`. |
| `desktalk/core/events/` | `EventStream`: publish / subscribe inside the program, a bounded queue and a thread per consumer. |
| `desktalk/core/agents/` | `ProfileBook` (who each object becomes, from `config/agents.yaml`), `Roster` (who's in the chat), `PresenceTracker`. |
| `desktalk/core/object_states/` | `ObjectStateTracker`: detection events → a steady present / absent state for each kind of object (smoothing, then a small state machine), `object_state` events, and rows in the SQLite store. The person's state is the on/off switch for reminders, small talk and vision checks. |
| `desktalk/core/habits/` | `protocols.py` (the book's rules as plain functions), `HabitTracker` (today's numbers), `HabitReminders`, `Preferences` (wake time, bedtime, quiet hours), `DeskStatus`, sun times. The rule engine: `rule_engine.py` checks the rules in `config/protocols.yaml` → `rules` against the object states, and `RuleRunner` runs it once every `rule_engine.check_every_sec`, keeps the nudges that are due for the reminders, and checks whether each nudge worked (`rule_outcome`). |
| `desktalk/core/chat/` | `MessageGenerator` (events → messages, answering you), `MessageHub` (messages → open app pages), `prompts.py` (every prompt), `context.py`, `tracker_actions.py`. `workers/`: `IdleChat`, `LostItems`, `Lens`, `DailySummary`, `VisionHabits`. |
| `desktalk/adapters/camera/` | `PiFrameSource`, `PhoneFrameSource`, `WebcamSource`, and the pairing key. |
| `desktalk/adapters/detector/` | `UltralyticsDetector`: YOLO on this computer. |
| `desktalk/adapters/llm/` | `OpenAICompatibleChatModel` (Gemini / OpenAI / Groq / Ollama) and `ResilientChatModel`, which wraps it with time limits, retries and a breaker. |
| `desktalk/adapters/storage/` | `EventLog` (the files in `data/events/`), `JsonFileStore` (small JSON files, saved all-or-nothing) and `SqliteStateStore` (the object states' history, `object_states.database`). |
| `desktalk/runtime/` | `Supervisor` (runs the background jobs and starts them again after a crash), `Scheduler`, `SystemClock`, logging. |
| `desktalk/web/` | FastAPI. `app.py` makes the two servers (the main one, and the phone page's own HTTPS one), `routers/` has the routes by area (`pi`, `phone`, `live`, `video`, `chat`, `habits`, `log`, `settings`, `health`), `schemas.py` the request shapes, `streams.py` SSE and the video, `serve.py` runs uvicorn. |
| `desktalk/desk_state.py` | `DeskState`: what's on the desk right now, the live view pictures, the proof photos. Still shared directly (passed in, never through an `app` object). |
| `shared/desktalk_shared/` | Used on the Pi and on this computer, standard library only: `detection.py` (`Detection`, `BBox`), `wire.py` (the upload format), `detector_api.py` (the detector service's requests and replies), `ultralytics_results.py` (YOLO results → `Detection`s, the same on both sides). |
| `raspberry_pi/camera/` | The camera program: `cameras.py`, `photo_store.py`, `detector_client.py`, `uploader.py`, `health.py`, `watchdog.py`, `self_test.py`, `settings.py`, `main.py`. [README](../raspberry_pi/camera/README.md). |
| `raspberry_pi/detector_service/` | The detector service: `Dockerfile`, `compose.yaml`, `.env`, and `desktalk_detector/` (FastAPI: `POST /detect`, `GET /health`, `PUT /model`; converts `.pt` models to ncnn and keeps the last good one). [README](../raspberry_pi/detector_service/README.md). |
| `benchmarks/` | `deskbench`: times YOLO models on the Mac and on the Pi (in its own Docker image), to pick the Pi's model. [README](../benchmarks/README.md). |
| `web_app/`, `phone_camera/` | The app's page, and the page a phone opens to be the camera. Unchanged by the new design. |

There are three uv packages in one workspace with one `uv.lock`: `desktalk` (this folder), `desktalk-shared`
(`shared/`) and `desktalk-detector` (`raspberry_pi/detector_service/`). The camera program isn't installed
with uv: it runs on the Pi's own `python3` and finds `desktalk_shared` in the cloned repo's `shared/` folder.

## Interfaces

Every part is written against small interfaces (`typing.Protocol`s in `desktalk/ports.py`), never against
another part's class. A part asks in its constructor for only the interfaces it uses and its own section
of the settings, for example `Roster(bus, profiles, desk, config.agents, config.presence, on_desk=...)`.
`desktalk/bootstrap.py` passes in the real versions; tests pass in the fakes from `tests/fakes/`.

| Interface | What it promises | Real version | Fake for tests |
|---|---|---|---|
| `Clock` | `now()`, `monotonic()`, `sleep()` | `SystemClock` | `FakeClock` (you move time by hand) |
| `PeriodicWorker` | `name`, `tick(now)` → seconds until the next round | the producer, `Scheduler`, `RuleRunner`, `HabitReminders`, `Lens`, `IdleChat`, `LostItems`, `DailySummary`, `VisionHabits`, `OllamaKeepAlive` | tests call `tick()` themselves |
| `Detector` | picture → list of `Detection`, or `DetectorUnavailable` with a reason | `UltralyticsDetector` | `FakeDetector` |
| `FrameSource` | the newest picture, `kind`, `connected`, `fps` | `PiFrameSource`, `PhoneFrameSource`, `WebcamSource` | `FakeFrameSource` |
| `PictureInbox` | `push(jpeg, info)` for a camera that sends us pictures | `PiFrameSource`, `PhoneFrameSource` | `FakeFrameSource` |
| `EventBus` | publish, subscribe by event class, `wait_idle()`, `stats()` | `EventStream` | `SyncEventBus` |
| `EventStore` | write, read a day, compact, prune | `EventLog` | `InMemoryEventStore` |
| `KeyValueStore` | load / save small JSON documents | `JsonFileStore` | `InMemoryKeyValueStore` |
| `ChatModel` | messages (or a picture) → reply, or `AIUnavailable` with a reason | `OpenAICompatibleChatModel`, wrapped in `ResilientChatModel` | `FakeChatModel` |
| `Notifier` | send a chat message to every open app page | `MessageHub` | `RecordingNotifier` |
| `ChatVoice` | a character's words for a habit reminder | `MessageGenerator` | `FakeChatVoice` |
| `RosterView` | who's in the chat | `Roster` | `StaticRoster` |
| `Profiles` | who each detected object becomes | `ProfileBook` | `FakeProfiles` |
| `HabitsView` | today's habits, the reminders ready to send | `HabitTracker` | `FakeHabits` |
| `RuleNudges` | the rule engine's nudges ready to send (`nudges(now)`), and which one went out (`note_fired()`) | `RuleRunner` (`core/habits/rule_runner.py`) | `FakeRuleNudges` |
| `Preferences` | wake time, bedtime, quiet hours | `Preferences` (`core/habits/preferences.py`) | `FakePreferences` |
| `DeskStatus` | camera on? someone at the desk? (the person's object state) reminders allowed? | `DeskStatus` (`core/habits/desk_status.py`) | `FakeDeskStatus` |
| `ObjectStatesView` | each object's state now (`snapshot()`), what changed lately (`changes(since)`) | `ObjectStateTracker` | `FakeObjectStates` |
| `ObjectStateStore` | the object states' history: add rows, newest state per object, the changes | `SqliteStateStore` | `InMemoryStateStore` |
| `CameraHealthView`, `PictureCheck` | Lens's checks on the picture | `CameraHealth` | `FakeCameraHealth`, `FakePictureCheck` |
| `LiveViewPainter` | boxes and desk drawings on the live view | `DeskPainter` | `FakePainter` |

The background chat jobs need a few more small interfaces, in `desktalk/core/chat/workers/needs.py`:
`PiUploads` (what came with the Pi's newest picture: `PiFrameSource`), `PhoneCamera` (`PhoneFrameSource`)
and `DeskLayout` (the desk outline: `SafetyRules`).

**Contract tests** keep the fakes honest: each interface that talks to the outside world has one test
suite in `tests/contracts/`, and the fake and every real version run the same suite. The fakes run them in
`tests/contracts/`, the real versions next to their own tests in `tests/unit/` (for example `TestEventLog`
runs `EventStoreContract`). The detector service, asked over its HTTP API, passes the same `Detector`
suite as `UltralyticsDetector`.

The Pi's two programs are built the same way, with their own small pieces: the camera program's
`main.py` puts together a camera, a `DetectorClient`, an `Uploader`, a `HealthReader` and a `Watchdog`;
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
| `alert` | `Alert` | desk monitor, lost items, vision habits | `kind: proximity` + `agent` and details like `snapshot`; or `kind: scenario` + `scenario`, `feature`, `text` |
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
in the app shows the intervals as a timeline, plus the other events.

The rest of `data/` (settings → `storage.folder`, kept by `JsonFileStore`): `preferences.json`,
`habits/<day>.json`, `learned_personalities.json`, `daily_summary.json`. The log file is
`data/logs/desktalk.log` (settings → `logging`).

The pictures themselves stay on the Pi and are deleted every 10 minutes (`pi.delete_every_min`). This
computer keeps only a few proof photos in memory.

## Background jobs

Everything that runs over and over is a `PeriodicWorker`: it has a `name` and a `tick(now)` that does one
round and returns how many seconds to wait before the next. It never starts a thread or sleeps itself.

The `Supervisor` (`desktalk/runtime/supervisor.py`) runs each job in its own thread. When a round raises,
it logs the error with the job's name and tries again after `supervisor.restart_start_sec` (1 s), then
twice that, up to `supervisor.restart_max_sec` (60 s). One good round starts the count over. It never gives
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
| `VisionHabits` | posture, screen distance, phone in hand (Ollama only) | `vision_habits.start_delay_sec` |
| `OllamaKeepAlive` | keeps Ollama's model loaded (only when the chat runs on Ollama) | `llm.ollama_keepalive_start_sec` |

Tests don't use threads for these: they call `tick()` with a `FakeClock`.

The event stream's consumers are separate: each has its own thread inside `EventStream`. A consumer whose
handler raises is logged and counted (`/api/health` → `events`), and keeps going.

**Starting and stopping.** The main web server's FastAPI "lifespan" (`desktalk/web/app.py`) calls
`desktalk.start()` when it starts and `desktalk.stop()` when it stops. `stop()` goes in this order: stop
the jobs, let the consumers finish the events still waiting, save the object states, save today's habits,
compact the event log, close the event stream (and let go of a webcam), close the object states database. A step that fails is logged and the rest still happen.

**On the Pi**, the camera program has a few threads of its own (the detector worker, the uploader, the
settings watcher), so a slow container or Wi-Fi never holds up the camera. A fourth, the watchdog, ends the
program when the camera stops giving pictures, so systemd starts it again. The container is started again
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

`desktalk/config.py` reads, in this order (later wins):

```
config/settings.yaml  →  config/settings.local.yaml (yours, not in git)  →  a few model names from .env
```

plus `config/agents.yaml`, `config/protocols.yaml` and the AI keys from `.env` (hidden when printed). The
result is one read-only `AppConfig` with a typed section per area (`config.camera`, `config.pi`,
`config.supervisor` ...). Each part gets only its own section, so it can't depend on the rest.

A missing, misspelled or bad setting **stops the start** with a message that names the file, the setting
and what's wrong, for example:

```
Desk Talk could not start, a setting needs fixing:
config/settings.local.yaml: camera.fps must be a number above 0 (got "fast")
```

What each setting means is in the comments of `config/settings.yaml`. New numbers go there, with a plain
comment, and get a typed field in `desktalk/config.py`.

On the Pi, the camera program reads the `pi:` section: first the copy of `config/settings.yaml` in the
repo on the Pi, then what your computer sends (`/api/pi/config`, asked again every
`pi.settings_check_every_sec`), then its command-line flags. New `pi.detector` settings go to the
container right away (`PUT /model`); anything else needs the camera program restarted. The container's own
settings (port, memory limit, retry waits) are in `raspberry_pi/detector_service/.env`.

## Tests

| Folder | What | Needs |
|---|---|---|
| `tests/unit/` | Each part on its own with fakes and a `FakeClock`: `core/`, `adapters/`, `runtime/`, settings, `desktalk_shared`, the camera program, the detector service (FastAPI's `TestClient` and a fake YOLO). | nothing |
| `tests/contracts/` | One suite per interface. The fakes run them here; the real versions run them in `tests/unit/`. | nothing |
| `tests/integration/` | The whole app built by `bootstrap.build()` with a fake detector, a fake AI and a `FakeClock` (`app_kit.py`), and the web API through FastAPI (`web_kit.py`): pictures through the whole pipeline, background jobs, uploads, streams. | nothing |
| `tests/characterization/` | Every route of both web servers: status code, content type, a few headers and the JSON shape, saved in `snapshots/`. `test_route_coverage.py` fails for a route without a case in `cases.py`. Re-record a snapshot you changed on purpose with `DESKTALK_RECORD_SNAPSHOTS=1`, and check the diff. | nothing |
| `tests/slow/` and other `-m slow` tests | The real YOLO model: the ncnn model finds the same objects as the `.pt`, the detector service with a real model. Skipped when the model files aren't in `models/`. | model files |
| `tests/docker/` | Builds the detector's image, starts it with `docker compose`, checks `/health`, `/detect`, the `127.0.0.1` port and the memory limit (`-m docker`, opt-in). | Docker, the internet |
| `tests/fakes/` | The fakes (see the interface table). | |

**On your Mac**, in the `desk-talk` folder:

```bash
uv sync --all-packages
uv run pytest -m "not slow"                # the fast tests (what you run most)
uv run pytest tests/characterization       # just the routes
uv run pytest -m slow                      # the real model
uv run pytest -m docker tests/docker       # the detector's Docker image
```

GitHub runs `uv run ruff check .` and `uv run pytest -m "not slow and not docker"` on every push and pull
request (`.github/workflows/tests.yml`). The default test run never needs the network, an API key, a
camera, a Pi or Docker.

## Agents

See [AGENT_SYSTEM.md](AGENT_SYSTEM.md).
