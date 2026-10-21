# Refactor plan: easier to change, harder to break

Date: 2026-10-21. Status: **in progress** on the `refactor` branch (started 2026-10-21).

Goal: make Desk Talk easy to change (maintainable), hard to break (reliable), and able to keep
going when one part fails (fault-tolerant). As part of it, **object detection moves to the Raspberry Pi**:
Ultralytics runs there inside Docker, using the ncnn model format. What the app does and how it looks stay the same.

## Where we are today

Checked on 2026-10-21:

- About 10,900 lines of Python in `desktalk/`, `safety_pipeline/`, `raspberry_pi/`, `scripts/`, `tests/`.
- 122 fast tests pass (`python -m pytest -m "not slow"`). `ruff check .` finds 42 errors.
- Packages come from `requirements.txt` / `requirements-dev.txt` with pip. No `pyproject.toml`, no lock file.
- Web server is Flask, with 37 routes in `desktalk/web/routes.py` and 7 more in `phone_server.py`.
- The Pi (`raspberry_pi/camera_node.py`, standard library + apt only) takes 3 pictures a second and sends each
  JPEG to `/api/pi/frame`. **This computer** runs YOLO on them through Ultralytics + PyTorch.

What makes it hard to change or easy to break:

| Problem | Where | Why it hurts |
|---|---|---|
| Nine classes take the whole `app` object | `MessageGenerator`, `DeskMonitor`, `LostItems`, `LensThread`, `IdleChatThread`, `HabitReminderThread`, `HabitSceneVlmThread`, `DailySummaryThread` | Each one can reach anything, so you can't test or change one without the rest. |
| Settings stored in module globals | `relationships.set_categories()` / `load_config()`, `camera_health.configure()` | Two apps (or two tests) in one process fight over the same numbers. |
| Numbers written straight into code | `camera_health.py` lines 26-41, `relationships.py` lines 42-153, `scheduler.every(3600, ...)`, `timeout=90` in `llm.py` | Breaks the "config, not code" rule in AGENTS.md. |
| Events and Pi uploads are plain dicts and HTTP headers | everywhere, `/api/pi/frame` | A typo in a key fails quietly at run time; the Pi and the computer can drift apart. |
| `print()` instead of logging (55 places), 33 `except Exception` blocks | everywhere | Errors get swallowed or lost; no log file to look at later. |
| Background threads that die stay dead | reminders, Lens, idle chat, vision habits, daily recap | One bad AI reply can quietly switch off a feature until restart. |
| `time.time()` called directly (57 places) | everywhere | Timing logic can only be tested by waiting or patching. |

## Detection on the Pi: how it works after

```
 Raspberry Pi                                                     This computer (desktalk)
 ─────────────────────────────────────────────────                ───────────────────────────────────────
 camera program (on the Pi itself, apt Python)                    /api/pi/frame
   camera → picture ──┬──▶ POST localhost ──▶ ┌──────────────────┐   picture + detections
                      │                        │ Docker container │       │
                      │    ◀── detections ──── │ detector service │       ▼
                      │                        │ FastAPI +        │   DetectionProducer
                      │                        │ Ultralytics,     │   (skips YOLO when detections came with it)
                      │                        │ ncnn model       │       │
                      │                        └──────────────────┘       ▼
                      └── picture + detections ── one POST over Wi-Fi ──▶ EventStream ─▶ event log, roster,
                                                                              habits, chat ... (as now)

 If the container is down, the camera program sends pictures alone and this computer runs Ultralytics instead.
```

Why split it like this, instead of putting the whole Pi program in Docker:

- **The camera stays outside Docker.** `picamera2` and `libcamera` come from Raspberry Pi OS's own apt packages
  and need the camera hardware, which is fiddly to pass into a container. So the camera program keeps running on
  the Pi directly, like `camera_node.py` does today (standard library + apt).
- **Only detection goes in Docker.** Ultralytics pulls in PyTorch, NumPy and much more. Inside a container none of
  that can clash with the Pi's apt packages, and the whole setup is one `docker compose up -d`.
- **They talk over HTTP on the Pi itself** (`127.0.0.1` only, not reachable from the network). If the container
  stops, crashes or is updated, the camera keeps going.

Details:

- **Ultralytics, with the ncnn model format.** Ultralytics turns the `.pt` model into an ncnn model once
  (`model.export(format="ncnn")`) and then runs it through ncnn, which is the fastest choice on the Pi's CPU and
  the one Ultralytics recommends for the Pi. The container does this export itself on first start and keeps the
  result in a Docker volume. The model and format are settings (`pi.detector.model`, `pi.detector.format: ncnn | pt`),
  so trying plain PyTorch for comparison is a settings change, not a code change.
- **The image** is built from Ultralytics' official 64-bit ARM image (`ultralytics/ultralytics:<exact version>-arm64`,
  pinned, never `latest`), plus our small detector service. uv installs the service's packages inside the image.
  It needs 64-bit Raspberry Pi OS and a few GB of space on the SD card.
- **One upload per picture**, as now, but it carries the detections too: a JPEG plus a small JSON part
  (`frame_id`, `taken_at`, `lux`, disk info, detector state, `detections`, and how many ms detection took).
  The format has a version number. The old header-only upload keeps working until the new Pi program is in place.
- **Detector state on each upload** says what the computer should do:
  `ok` (detections attached, use them), `busy` (the Pi's detector is still working on an earlier picture: this one
  is just for the live view, don't detect it), `off` (the Pi's detector is down: detect this one here).
  That way detection can run slower than 3 pictures a second without the live view getting choppy.
- **Pictures still go to the computer**, because the live view, proof photos, Lens (brightness, blur, glare) and the
  AI picture checks (lost items, vision habits) all need them. Only YOLO moves.
- **This computer keeps Ultralytics** for the phone and webcam camera sources and as the backup for the Pi. Both
  places use the same shared code to turn Ultralytics results into `Detection`s, so the labels and boxes match.
- **Speed is the big unknown.** The current model (`yolo26m-objv1-150`, 365 kinds of objects, 640 pixels) may only
  manage about one detection a second on a Pi. Before building on it, you run a timing script inside the container
  (Phase 0), and we pick the model size, picture size and detection rate from the numbers. The roster and habits
  work on seconds, not picture counts, so a lower detection rate is fine.

## Decisions (❓ = still needs your answer)

1. **uv** manages Python and packages, as a **uv workspace** with one `uv.lock`:

   | Package | Folder | What it is | Runs on |
   |---|---|---|---|
   | `desktalk` | `desktalk/` | the app: web server, agents, habits, chat, backup detection | this computer |
   | `desktalk-shared` | `shared/` | `Detection`, the Pi ↔ computer upload format, the detector API, and Ultralytics result → `Detection` (all standard library only) | both |
   | `desktalk-detector` | `raspberry_pi/detector_service/` | the detector service that runs in Docker | the Pi (in Docker) |

   The camera program (`raspberry_pi/camera/`) is not installed with uv: it runs on the Pi's own Python 3 from apt
   and only imports the standard library, apt packages and `desktalk_shared.wire` (which is standard library only),
   straight from the cloned repo. Dependency groups: `dev` (pytest, ruff, httpx, pytest-cov). Python: 3.12 on this
   computer and in the container; the shared upload format also has to work on the Pi's own Python (3.11 or newer).
   `requirements*.txt` go away.
2. **Docker on the Pi** runs only the detector service, with `docker compose`: `restart: unless-stopped`, a health
   check, a memory limit (so it can't starve the camera program), the port bound to `127.0.0.1`, and a volume for
   models. Docker isn't needed on this computer, except to build and test the image (Apple Silicon Macs are 64-bit
   ARM like the Pi, so the same image runs there).
3. **The AGENTS.md rule for the Pi changes** from "only `camera_node.py` runs on the Pi" to: the camera program
   stays standard library + apt (+ `desktalk_shared.wire`); everything else on the Pi runs in the Docker container.
   AGENTS.md and `raspberry_pi/README.md` get updated to match.
4. **ruff** settings live in `pyproject.toml`. Rules: `E, F, W, I, B, UP, SIM` plus `T20` (no `print`).
   Every file gets touched in this refactor, so the 42 old errors get fixed along the way.
   `ruff format` stays off, as AGENTS.md says, unless you want it.
5. **FastAPI** (run by uvicorn) replaces Flask on this computer, and is also what the detector service uses.
   Every URL and JSON reply the app uses stays the same, so `web_app/` and `phone_camera/` need no changes.
   Request and reply shapes become pydantic models. Live updates (SSE) use FastAPI's streaming reply. The phone
   camera's HTTPS server becomes a second uvicorn server with the same certificate.
6. **Interfaces first.** Every part is written against a small interface (a `typing.Protocol`), never against
   another part's class. One file, `desktalk/bootstrap.py`, builds the real parts and connects them; tests build
   the same parts with fakes. The camera program and the detector service get their own small `bootstrap` the same way.
7. **Threads stay.** The parts already run in threads. Rewriting everything to `async` would be a big risk for
   little gain. Only the web layers are async.
8. **Phone and webcam cameras keep working**, detected on this computer with Ultralytics, as now.
9. ❓ **How YOLO26 runs on this computer, so it matches the Pi** (from [the ncnn check](2026-10-21-ncnn-check.md)).
   ncnn can't run YOLO26's end-to-end head, so the exported model uses its second head plus the usual
   overlap filter, and always pads pictures to a square. Against the `.pt` run the same way, ncnn matches
   99.5% of boxes (fp32, 640); against the `.pt` as the app runs it today, about 91%.
   Default in this plan: run the `.pt` on this computer the same way too (two new `detector` settings,
   `end2end: false` and `rect: false`), so the Pi and this computer find the same objects.
   The other choice, swapping the head's weights before export, stays closer to today's results but depends on
   Ultralytics' internals. (yolo11 models have no end-to-end head; for them only the padding setting matters.)
10. ❓ **`safety_pipeline/` and `run_safety_pipeline.py`.** A separate program that posts to `/api/safety`.
   Default in this plan: keep it working but leave it out of the refactor. Other choices: fold it into Desk Talk,
   or delete it.

## The new shape

```
pyproject.toml     the uv workspace + the desktalk app package
uv.lock
shared/desktalk_shared/
  wire.py          the upload format (dataclasses, version number, to/from JSON); standard library only
  detection.py     Detection, BBox
  detector_api.py  the detector service's request and reply shapes
  ultralytics_results.py  Ultralytics results → list of Detection (used on the Pi and on this computer)
raspberry_pi/
  camera/          the camera program (replaces camera_node.py), run with the Pi's own python3
    cameras.py       Picamera2 / USB / folder / video (as now)
    photo_store.py   save and delete pictures (as now)
    detector_client.py  asks the container for detections; gives up quickly and backs off when it's down
    uploader.py      sends picture + detections; backs off when the computer is away
    health.py        CPU temperature, throttling, disk
    main.py          the loop, settings, command-line flags, --self-test
  detector_service/  runs in Docker
    Dockerfile       FROM ultralytics/ultralytics:<pinned>-arm64, adds the service with uv
    compose.yaml     restart policy, health check, memory limit, 127.0.0.1 port, models volume
    app.py           FastAPI: POST /detect, GET /health, PUT /model (load or export a model)
    benchmark.py     times each model size / picture size (Phase 0)
desktalk/
  domain/          plain data, no I/O: Detection, BBox, Event types (dataclasses), Agent, Habit
  ports.py         the interfaces (below)
  core/            the logic, written only against ports: roster, desk monitor, habits,
                   reminders, Lens, lost items, message generator, event stream
  adapters/        the real things behind the ports:
    camera/        PiFrameSource (now takes detections too), PhoneFrameSource, WebcamSource
    detector/      UltralyticsDetector (backup, phone, webcam)
    llm/           OpenAI-compatible client (Gemini / OpenAI / Groq / Ollama)
    storage/       event log files, habit days, preferences, safety_rules.json
  runtime/         Supervisor (restarts threads), Scheduler, SystemClock, logging setup
  web/             FastAPI app, routers by area (pi, live, chat, habits, log, rules, settings), schemas
  settings.py      pydantic models for config/*.yaml; a bad value stops startup with a clear message
  bootstrap.py     builds everything (the only place that knows the real classes)
tests/
  fakes/           FakeClock, FakeFrameSource, FakeDetector, FakeYolo, FakeLLM, InMemoryEventStore, ...
  contracts/       one test suite per interface; every real and fake version must pass it
  unit/  integration/  slow/  docker/
```

The interfaces:

| Interface | What it promises | Real version | Fake for tests |
|---|---|---|---|
| `Clock` | `now()`, `sleep()` | `SystemClock` | `FakeClock` (you move time forward by hand) |
| `Detector` | picture → list of `Detection`, or "unavailable" | `UltralyticsDetector` (computer, container), `DetectorClient` (Pi camera program → container) | scripted results, can be told to fail or be slow |
| `Camera` (Pi) | take a picture | Picamera2, USB, folder, video | list of pictures |
| `Uploader` (Pi) | send one picture + detections, say if the computer took it | HTTP | records what was sent |
| `FrameSource` (computer) | newest picture, its detections and detector state, `connected`, `kind` | Pi, phone, webcam | list of pictures |
| `EventBus` | publish, subscribe by event type | `EventStream` | synchronous bus |
| `EventStore` | write, read a day, compact, prune | files in `data/events/` | in memory |
| `ChatModel` | text (+ optional picture) → reply, or a clear "AI is unavailable" | OpenAI-compatible client | canned replies, can be told to fail |
| `KeyValueStore` | load/save small JSON (preferences, habit days, rules) | atomic file writes | dict |
| `Notifier` | send a chat message to the app (SSE) | SSE hub | list |

Each `core/` class asks for only the interfaces it uses. For example, `Roster(clock, bus, profiles, settings.agents)`
instead of `Roster(app)`. Settings are passed in as typed objects, so the module globals in `relationships.py` and
`camera_health.py` disappear.

## Keeping it running when something fails

| What fails | What happens now | What happens after |
|---|---|---|
| **Detector container is down, crashing or still starting** | n/a | Docker restarts it. The camera program waits only a short time per picture (setting), then marks it `off` and sends it anyway; this computer detects it. After a few failures in a row it stops asking for a while and checks `/health` now and then. `/api/health` and Lens say "Pi detection off" and why |
| **Detection is slower than the camera** | n/a | Only the newest picture goes to the container; pictures taken meanwhile go out as `busy` (live view only). No queue builds up |
| **The container uses too much memory** | n/a | Memory limit in `compose.yaml`; Docker restarts it instead of the Pi freezing |
| **Model missing or export fails** | Startup crashes on the computer | The container's `/health` reports it, keeps the last working model if there is one, and retries with backoff; meanwhile the computer detects |
| **The Pi gets hot and slows down** | Not noticed | The camera program reports temperature and throttling; Lens mentions it; detection time is shown in `/api/health` |
| **The computer is away** (off, Wi-Fi drop) | The Pi keeps saving pictures | Same, plus the Pi retries with growing waits instead of every picture. No catch-up later: old detections would make agents join minutes late. The event log shows the gap |
| **Pi and computer run different versions** | Silent mismatch | Every upload carries the format version; a mismatch gets a clear reply ("update the Pi: run ...") instead of half-working |
| **Pi clock is off** | Not noticed | The computer uses its own time for timing; if the Pi's clock is more than a few seconds off, Lens mentions it |
| **Pi reboots** | systemd restarts the camera program (if set up) | Same for the camera program; Docker starts the container on boot |
| A background thread on the computer throws | It stops for good | `Supervisor` logs it, restarts it after 1s, 2s, 4s ... up to a limit; `/api/health` shows each one's state |
| The AI is down or out of quota | Mixed: some retries, some silent | One `ResilientChatModel` wrapper: time limit, retries with backoff, a "circuit breaker" that stops calling for a while after repeated failures, and short built-in fallback lines so characters still speak |
| The camera stops | Analysis pauses (good) | Same, plus the state is in `/api/health` |
| A consumer is slow | Its queue fills | Bounded queue per consumer that drops the oldest *picture* events (never joins, alerts or habits) and counts the drops |
| A data file is half-written or broken | Some places use temp-file + rename, some don't | Every save uses `KeyValueStore` (temp file + rename); broken lines in `.jsonl` files are skipped and logged, not fatal |
| Shutdown | `try/finally` around Flask | FastAPI "lifespan" starts and stops everything in order, then saves habits and compacts the log |
| Bad input on `/api/pi/frame` | Partly checked | Pair key checked, picture size limited, pydantic checks the JSON part, boxes outside the picture are dropped |

All new numbers (timeouts, backoff, limits, breaker timing, queue sizes, memory limit, temperature warning) go in
`config/settings.yaml` with a plain comment. The Pi's numbers stay in the `pi:` section, which the camera program
reads from the computer as now and passes on to the container (`PUT /model`).
Logs go to the terminal and to a rotating file (`data/logs/` on the computer; `journalctl` and `docker compose logs`
on the Pi).

## How we test

We use the superpowers **test-driven-development** skill for every task: write a failing test, watch it fail,
write the least code that makes it pass, tidy up, commit.

1. **Safety net first.** Before moving any code, add "characterization" tests that record what the web API returns
   today for every route (status code + JSON shape), using the current Flask app with a fake AI and fake detector.
   The same tests then run against the FastAPI app. If they pass, the app (`web_app/`) can't tell the difference.
2. **Unit tests** for each `core/` class, the camera program and the detector service, using fakes and `FakeClock`
   (no sleeping, no network, no camera, no Pi, no Docker). The detector service is tested with FastAPI's
   `TestClient` and a fake YOLO model.
3. **Contract tests**: one test suite per interface. The real version and the fake must both pass it, so a fake can
   never quietly drift from the real thing. `DetectorClient` + the service and `UltralyticsDetector` pass the same
   `Detector` suite.
4. **Wire tests**: the camera program's encoder and the computer's decoder are tested against the same saved example
   uploads, including old-format, wrong-version, `busy` and `off` ones.
5. **Failure tests** for every row of the table above: container down, slow, or returning garbage; computer
   unreachable; AI raises or hangs; thread crashes; broken JSON file; full queue.
6. **Integration tests on this computer**: the camera program (fake camera reading sample photos) → detector service
   (in-process) → a real local server; chat messages come out over SSE.
7. **Slow tests** (`-m slow`): the real model on sample photos, and a check that the ncnn model finds the same
   objects as the `.pt` model (same labels, boxes within a few pixels) so the format switch doesn't make detection worse.
8. **Docker tests** (`-m docker`, opt-in because building the image needs the internet): build the image on this
   computer, start it with `docker compose`, run the `Detector` contract suite against it, then kill it and check the
   camera program falls back to `off`.
9. **On the Pi** (you run these; I can't): the benchmark inside the container, and `--self-test` in the camera
   program, which checks camera, container health, temperature and that the computer can be reached.
10. Coverage measured with `pytest-cov`; aim for 85% of `core/`, `domain/` and `desktalk_shared`.

Rules from AGENTS.md still hold: the default test run never needs the network, an API key, a camera, a Pi or Docker.

## Order of work, and what runs in parallel

Phases 0, 1 and 4 are done one step at a time in this session, because everything after depends on them.
Phase 2 is split across subagents (superpowers **dispatching-parallel-agents** / **subagent-driven-development**),
each in its own git worktree and branch, each owning its own folders so no two touch the same file.
After each one finishes, a review agent checks the work against this plan before it's merged.

### Phase 0: tools, safety net, and the Pi speed test
- [x] uv workspace: root `pyproject.toml`, `shared/` and `raspberry_pi/detector_service/` packages, `uv.lock`;
      `uv sync` works; remove `requirements*.txt`, `pytest.ini` (moved into `pyproject.toml`)
- [x] ruff settings in `pyproject.toml`; GitHub Actions job: `uv sync`, `uv run ruff check .`, `uv run pytest -m "not slow and not docker"`
- [x] Characterization tests for every route (54 on both servers, 133 cases) against the current Flask app
- [x] Check on this computer that Ultralytics exports `yolo26m-objv1-150` to ncnn and that it finds the same objects as the `.pt`
      ([results](2026-10-21-ncnn-check.md); decision 9)
- [x] First Docker image (just Ultralytics + the benchmark script) and `compose.yaml` (`benchmarks/`)
- [x] Benchmark tool: `benchmarks/` (`deskbench`, settings in `config/benchmark.yaml`, results in
      `benchmarks/results/`). Tries yolo11 n / s / m at 320 / 480 / 640 as pt, ncnn (fp32, fp16) and onnx
      (fp32, int8), on this computer and on the Pi in Docker
- [ ] **You, on the Pi:** install Docker and run the benchmark (steps in `benchmarks/README.md`).
      We pick the model, picture size and detection rate from the results and write them into `config/settings.yaml`.
      yolo11 models only know the 80 COCO objects; if the pick has to find keys or earphones, add the
      Objects365 models to `config/benchmark.yaml` and run again
- [x] Update AGENTS.md (new Pi rule, `uv run ...` commands) and README

### Phase 1: interfaces and fakes (one at a time; the contract every subagent builds against)
- [x] `desktalk_shared.wire` (upload format version 2, detector states `ok` / `busy` / `off`) and `Detection`
- [x] The detector service's HTTP API (request/reply models for `/detect`, `/health`, `/model`)
- [x] `domain/` data types and `ports.py`
- [x] `tests/fakes/` and `tests/contracts/`
- [ ] Typed settings (pydantic) that load today's `config/*.yaml` unchanged; move hardcoded numbers into `settings.yaml`
- [ ] `runtime/`: `Supervisor`, `Scheduler`, `SystemClock`, logging

### Phase 2: parallel subagents (each test-first, each in its own worktree)

Full briefs, with the rules every agent follows: [2026-10-21-phase2-briefs.md](2026-10-21-phase2-briefs.md).
New code goes in new folders; the old modules keep the app running until Phase 3 switches over.

| Agent | Owns | In short |
|---|---|---|
| A. Detector service | `raspberry_pi/detector_service/` | FastAPI `/detect`, `/health`, `/model`; ncnn export on first start; keeps the last good model; Dockerfile + `compose.yaml` |
| B. Camera program | `raspberry_pi/camera/` | camera, photo store, detector client and uploader with back-off, `ok` / `busy` / `off`, health, `--self-test`; standard library + apt only |
| C. Events and storage | `core/events/`, `adapters/storage/` | event stream with bounded queues and drop counts; event log, JSON files behind `EventStore` / `KeyValueStore` |
| D1. AI and message generator | `adapters/llm/`, `core/chat/` | `ResilientChatModel` with breaker and fallbacks; message hub; `MessageGenerator` takes interfaces |
| D2. Background chat jobs | `core/chat/workers/` | idle chat, lost items, Lens, recap, vision habits as `PeriodicWorker`s |
| E1. Cameras, detector, producer | `adapters/camera/`, `adapters/detector/`, `core/vision/producer.py` | sources pass the `FrameSource` contract; producer uses the Pi's detections on `ok`, skips on `busy`, detects here on `off` |
| E2. Desk rules and Lens checks | `core/vision/` | the 994-line `relationships.py` split by job, no module globals; camera health; desk monitor |
| F. Agents and habits | `core/agents/`, `core/habits/` | roster, profiles, habit tracker, reminders, preferences, desk status; Protocols rules unchanged and tested |

### Phase 3: web layer (after Phase 2 is merged)
- [ ] FastAPI app split into routers; pydantic request/reply models; SSE; phone HTTPS server
- [ ] `/api/pi/frame` takes version 2 uploads (and still the old ones); `/api/pi/config` adds the detector settings
- [ ] Characterization tests from Phase 0 pass against FastAPI
- [ ] `bootstrap.py` builds the real app; `server.py` starts uvicorn; lifespan start/stop

### Phase 4: finish
- [ ] Integration, failure and Docker tests end to end (camera program → container → real local server → SSE)
- [ ] Remove Flask, `raspberry_pi/camera_node.py`, old `app`-passing code
- [ ] `ruff check .` clean, all tests pass, coverage report
- [ ] Update README, `raspberry_pi/README.md` (Docker setup, systemd service for the camera program),
      ARCHITECTURE.md, AGENT_SYSTEM.md, AGENTS.md
- [ ] Try it for real with you: on the Pi, `docker compose up -d` and the camera program's self-test; open the app,
      check chat, alerts, habits, event log; then break things on purpose (`docker compose stop`, stop the server,
      pull the Wi-Fi) and watch it recover

Each phase ends with a commit (or a PR, if you prefer) that you can review before the next one starts.
Anything that has to run on the Pi is written as steps for you, each command labelled **on the Pi** or
**on your Mac**.

## Risks

- **Pi speed.** The 365-object model at 640 pixels may only manage around 1 detection a second or less on a Pi,
  and the Pi slows itself down when it gets hot. The Phase 0 benchmark decides the model and rate before we build
  on it. If small objects (keys, earphones) get missed with a smaller model or picture, we tell you and you choose
  between speed and accuracy. A Pi 5 with a cooler will do much better than a Pi 4.
- **Docker on the Pi.** Needs 64-bit Raspberry Pi OS, a few GB of SD card for the Ultralytics image, and memory
  (a 2 GB Pi will be tight; 4 GB or more is better). The image is pulled once, then works offline.
- **Ultralytics ncnn export of YOLO26 Objects365 models.** Checked on this computer in Phase 0 before anything
  depends on it. If the ncnn model can't match the `.pt` model closely, we tell you and fall back to `.pt` in the
  container (slower).
- **Small API differences** between Flask and FastAPI (trailing slashes, error replies, SSE framing). The Phase 0
  characterization tests are there to catch these.
- **Merge conflicts** between subagents. Kept low by giving each agent its own folders and freezing `ports.py`,
  `desktalk_shared.wire` and the detector service's API after Phase 1; a change to an interface goes through this session.
- **I can't run anything on the Pi.** Pi steps are only "done" once you've run them and told me the result.

## Tools already set up

- **superpowers** plugin 7.0.0, installed for your user (`superpowers@superpowers-marketplace`). Start a new
  Claude Code session for its skills to load.
- **Long-chat reminder**: `.claude/hooks/context_warning.py`, run from `.claude/settings.json` when you send a
  message and when Claude finishes a turn. It shows a note at 200k tokens and a stronger one at 250k. Change the
  limits with the `DESKTALK_CONTEXT_WARN_TOKENS` and `DESKTALK_CONTEXT_STOP_TOKENS` environment variables.
