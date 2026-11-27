# Working on Desk Talk

## Running it on a computer

The app runs on the Pi. For working on it, uv run python server.py runs it on a computer instead; the tests, the
phone camera and the webcam use this. Install [uv](https://docs.astral.sh/uv/) once (brew install uv, or see its
page). Then, **on your Mac**, in the desk-talk folder:

```bash
uv sync --all-packages                   # Python 3.12 and every package, YOLO included, exactly as in uv.lock
cp .env.example .env                     # add an AI key, or OLLAMA_URL for Ollama (optional)
uv run python server.py                  # the first start downloads the YOLO model (about 45 MB) into models/
```

Plain uv sync doesn't bring YOLO (PyTorch is big, and the Pi's app image doesn't want it). To run YOLO on a
computer you need uv sync --all-packages (everything) or uv sync --extra computer-detector (just the app and
YOLO). Without it, server.py stops and says so. You can also set detector.enabled: false to run with no detector of its own.

Open http://localhost:5000. A setting with a typo or a bad value stops it at the start, with a message that
names the file and the setting. With the Pi as the camera (camera.source: pi), the page links to the Pi's own Desk
Talk instead, at dev.pi_address, because the Pi's camera sends its pictures there. To see a camera
here, set camera.source: webcam (or phone) in config/settings.local.yaml.

## Tests

**On your Mac**, in the desk-talk folder:

```bash
uv run pytest -m "not slow"                # fast tests: no model, network, AI key, camera, Pi, Docker or browser needed
uv run pytest tests/characterization       # every web route still answers as before
uv run pytest tests/contracts              # the fakes behave like the real parts
uv run pytest -m slow                      # the real YOLO model, and ncnn against .pt (needs the models in models/)
uv run playwright install chromium         # once, for the browser tests
uv run pytest -m e2e                       # the real app page in a browser
uv run pytest -m docker                    # build the Pi's three images and start them (needs Docker and the internet)
DESKTALK_PI=pi uv run pytest -m pi -v -rs  # against the real Pi over ssh (every test skips without DESKTALK_PI)
uv run ruff check .                        # lint
uv run pyright                             # type check: 0 errors
uv run vulture                             # dead code check: prints nothing
```

GitHub runs the lint, the type check, the dead code check and the fast tests on every push
(.github/workflows/tests.yml). The fast tests run there with --cov, which fails when they cover less
of the code than fail_under in pyproject.toml. GitHub also runs the developer page's own tests and builds
the page (npm test and npm run build in dev_ui/).

- tests/unit/: each part on its own, with fakes and a clock you move by hand: the app's parts, the camera program,
  the detector service (with a fake YOLO)
- tests/contracts/: one test suite per interface; the fake and every real version must pass the same one
- tests/integration/: the whole app built by desktalk/bootstrap.py with a fake detector and a fake AI: pictures
  through the whole pipeline, the web API, background jobs
- tests/characterization/: every route of both web servers: status code, headers and JSON shape, saved in
  snapshots/. A route without a case fails the tests
- tests/slow/: the real model: the ncnn model finds the same objects as the .pt, and the detector service with a
  real model (-m slow)
- tests/docker/: builds the Pi's three images and the cloud server's, starts them and asks them questions (-m
  docker, opt-in)
- tests/e2e/: the real app page in a browser, against the whole app with a fake detector and a fake AI (-m e2e,
  opt-in); test_full_stack.py sends pictures from the camera program through the detector container to the page (-m
  docker)
- tests/pi/: the real Pi over ssh: the three containers, pictures flowing, restarts, the log files, no picture
  leaving (-m pi, opt-in). What only a person can do is in [Checking on the real
  Pi](RUNBOOK.md#checking-on-the-real-pi)
- tests/fakes/: the fakes: FakeClock, FakeDetector, FakeChatModel, SyncEventBus ...

The benchmark tool has its own tests: see [benchmarks/README.md](../benchmarks/README.md).

## Developer page

The developer page is a second page, for working on Desk Talk rather than using it, on a computer running
server.py. It's switched off on the Pi (dev.enabled: false in raspberry_pi/settings.pi.yaml). It has these tabs:

- Checks has one button that runs the fast tests, the lint, the type check, the dead code check and the page's own
  tests. It can also run every test on its own (or a whole file or folder), and shows the printout as it runs. The
  same checks from the terminal are under [Tests](#tests).
- Live shows each object's state (present or absent), the changes as they happen, and a chart of how the smoothing
  turned the last pictures into that state, so you can see why something flickers. Click an object for its whole
  trace: when it joined the chat, left or was turned away (and why: how sure, how long, the sensitivity), and its
  state changes, each with the picture taken then.
- System shows how every part is doing (background jobs, timed jobs, the event stream, detection, the AI), and the
  Pi's health checks when the Pi is the camera.
- Pi has charts of the Pi's processor, memory, disks, network, temperatures and fan, from dev.pi_address.
- Pi log shows one of the Pi's logs as it's written, over ssh pi.
- Database shows the tables in the SQLite databases, and has a box for a query that only reads.
- Storage shows what this browser keeps for the app page (localStorage), and the files in data/.
- Demo sets off pretend warnings after a countdown you set, for testing or recording a demo video.

You need [Node](https://nodejs.org/) 22 for it. **On your Mac**, in the desk-talk folder, once (and
again after dev_ui/ changes):

```bash
cd dev_ui && npm install && npm run build
```

Then start Desk Talk as usual and open http://localhost:5000/dev/ (web.port). Only this computer gets answers
from it. Other devices on the Wi-Fi, and other web pages open in your browser, are turned away (403). The page changes
nothing except that it can start and stop a test run, delete a localStorage key after you say yes, and send the Demo
tab's pretend warnings, which go out like real ones (to the chat and the Mac pop-ups; a pretend sip counts in today's
water). The database is opened read-only. To switch the page off, set dev.enabled: false in
config/settings.local.yaml; its other numbers are in the dev: section of config/settings.yaml.

To work on the page itself, npm run dev in dev_ui/ serves it at http://localhost:5173/dev/
(dev.vite_port) and changes show up as you save (the server must be running too). More in [dev_ui/README.md](../dev_ui/README.md).

## Adding a route

Every web address the servers answer is listed in [API.md](API.md). A new route on the main or phone server needs:

1. the route, in its file in desktalk/web/routers/ (one file per area), and a line in that file's top comment;
2. a case in tests/characterization/cases.py: tests/characterization/test_route_coverage.py fails without one;
3. an entry in [API.md](API.md), with the totals at its top kept right (nothing checks the page).

A cloud server route goes in cloud/server/desktalk_cloud/routers/, with tests in tests/unit/test_cloud\_\*.py.
tests/unit/test_cloud_bootstrap.py fails if it answers without a login, unless it's on that test's short list.

## Project structure

```
desk-talk/
├── server.py                 # start the app on a computer, for working on it: uv run python server.py (on the Pi, Docker runs it)
├── pyproject.toml, uv.lock   # the uv workspace: desktalk, desktalk-shared, desktalk-detector, desktalk-cloud
├── config/                   # every setting: settings.yaml, agents.yaml, protocols.yaml, prompts.yaml (the wording), safety_rules.json
├── desktalk/                 # the app (the `app` container on the Pi)
│   ├── bootstrap.py          #   builds every part and connects them (the only place that knows the real classes)
│   ├── config/               #   reads config/ and .env into typed settings, one file per config file; a bad value stops the start
│   ├── ports.py              #   the interfaces between the parts
│   ├── domain/               #   plain data: Detection, Frame, events, agent profiles, nudges, the desk right now
│   ├── core/                 #   the logic, written only against ports.py (no OpenCV, web or AI libraries)
│   │   ├── desk/             #     the desk right now: live view pictures, the scene, dangers, memories, proof photos
│   │   ├── vision/           #     producer (pictures → events), desk rules, desk monitor, Lens's checks, proof photos
│   │   ├── events/           #     the event stream
│   │   ├── agents/           #     profiles + roster (join / leave)
│   │   ├── habits/           #     Protocols rules, habit days, habit tracker, reminders, preferences, sun times
│   │   └── chat/             #     message generator and its voices, message hub, chat context; workers/: idle chat, lost items, Lens, recap
│   ├── adapters/             #   the real things behind the interfaces
│   │   ├── camera/           #     the Pi, phone and webcam as picture sources
│   │   ├── detector/         #     YOLO inside the app (Ultralytics), for a computer; off on the Pi
│   │   ├── llm/              #     the optional AI (Gemini / OpenAI / Groq / Ollama, or `llm.ai_url`), with time limits, retries and a breaker; text only on the Pi
│   │   ├── picture/          #     OpenCV: the picture tools, and the drawing on the live view
│   │   ├── storage/          #     the event log files and small JSON files (preferences, habit days)
│   │   └── dev/              #     for the developer page: the test runner, the read-only database and data folder
│   ├── runtime/              #   Supervisor (starts crashed background jobs again), Scheduler, clock, logging
│   ├── web/                  #   FastAPI: app.py, routers/ (one file per area), schemas.py, serve.py (uvicorn)
│   └── util.py               #   small helpers
├── shared/desktalk_shared/   # used in the Pi's containers and on a computer: Detection, the upload format, the detector's API, backoff, clock, worker thread
├── raspberry_pi/             # what runs on the Pi (raspberry_pi/README.md): compose.yaml, install.sh, update.sh, settings.pi.yaml, settings.mine.example.yaml, .env.example
│   ├── app/                  #   the `app` container: Dockerfile and start.sh
│   ├── camera/               #   the `camera` container: the camera program, cli.py, main.py, workers.py ...
│   └── detector_service/     #   the `detector` container: YOLO, Dockerfile, desktalk_detector/
├── cloud/                    # the optional cloud server, in Docker: its server, website and settings (cloud/README.md)
├── benchmarks/               # times YOLO models on your Mac and on the Pi, to pick the Pi's model
├── web_app/                  # the app you open at localhost:5000 (HTML / CSS / JS, art, fonts)
├── phone_camera/             # the page a phone opens to be the camera instead of the Pi (when the app runs on a computer)
├── dev_ui/                   # the developer page at localhost:5000/dev/ (Vite + React + TypeScript; dev_ui/README.md)
├── models/                   # YOLO model files (downloaded, not uploaded)
├── tests/                    # unit, contracts, integration, characterization, slow, docker, e2e, pi; fakes/, e2e_kit/
├── scripts/                  # download_models.py, link_cloud.py, camera_preview.py, yolo_webcam_detect.py, capture_app_screenshots.py
├── docs/                     # the guides: API.md, RUNBOOK.md, ARCHITECTURE.md, CHARACTERS.md ...
└── data/                     # event log, habit days, preferences, log files (made when it runs, not uploaded; on the Pi it's the app container's data folder)
```
