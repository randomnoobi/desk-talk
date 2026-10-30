# Desk Talk

**A habit-tracking app that brings your desk objects to life.**

A camera watches your desk. Every object it recognizes joins a group chat as a
character ("Cup joined the chat"), and leaves when you take it away. The
characters look out for each other (Computer panics when the cup gets close) and
for you: water, movement breaks, sunlight and dim evenings, and phone-free
focus time, following Andrew Huberman's *Protocols*.

---

## How it works

```
Raspberry Pi                                     Your computer (uv run python server.py)
┌───────────────────────────────────┐            ┌──────────────────────────────────────────────┐
│ camera program                    │ picture +  │ one detection event per picture              │
│   3 pictures a second, kept       │ what was   │ event stream → event log (data/events/)      │
│   on the Pi, deleted every 10 min │ found      │   → agents join / leave the chat             │
│       │ picture   ▲ what's in it  │ ─────────▶ │   → safety alerts, habit reminders           │
│       ▼           │               │   Wi-Fi    │ YOLO here only when the Pi's detector is off │
│ detector (YOLO, in Docker)        │            │ app at http://localhost:5000                 │
└───────────────────────────────────┘            └──────────────────────────────────────────────┘
```

- **The Pi** runs two things. The **camera program** (`raspberry_pi/camera/`) takes the pictures, keeps
  them on the Pi and deletes them every 10 minutes. The **detector** (`raspberry_pi/detector_service/`)
  runs YOLO in a Docker container on the Pi and tells the camera program what's in each picture. Each
  picture then goes to your computer together with what was found, in one upload.
- **Your computer** (`uv run python server.py`) turns every picture into a detection event. When the Pi's
  detector is off (not started, still loading its model, crashed), your computer runs YOLO on the
  picture itself. Events flow through an **event stream** to separate consumers (decoupled, so a slow AI
  reply never holds up the camera), and are saved in the **event log**. Every 10 minutes the log is
  compacted: per-picture events become "the cup was on the desk from 2:02 to 2:31" intervals.
- A phone (the page in `phone_camera/`, over Wi-Fi) or a webcam can still be the camera instead:
  `camera.source` in `config/settings.yaml`. Then your computer does all the detecting.

Inside, every part is written against small interfaces (`desktalk/ports.py`). One file,
`desktalk/bootstrap.py`, builds the real parts and connects them; the tests build the same parts with
fakes. Background jobs (reminders, Lens, small talk ...) start again by themselves after a crash, and
the AI, the Pi's detector or the network going away never stops the rest.

> **Not tried on a real Pi yet.** The new camera program and the detector container are tested on the
> Mac, but they haven't run on a Raspberry Pi so far, and the detector's Docker image hasn't been built
> yet. Expect the Pi steps to need fixing the first time.

More detail: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

---

## Characters

Nobody is in the chat at first. Only Computer, Cup, Phone, Snack and Book can join
(`agents.can_join` in `config/settings.yaml`). One joins once the camera is sure enough of it for long enough,
which is the **Sensitivity** slider in Settings: from Picky (65% sure, for 10 seconds) to Eager (35%, 2 seconds);
Normal is 45% for 3 seconds. It leaves when it's been gone 2 minutes. The same slider sets how much flicker
(things flashing on and off, boxes jumping about) is let through: Picky waits twice as long before something
counts as put down, taken away or moved, or leaves the chat, and Eager half as long (each step's `steady`).
Something new, with no character of its own, needs a surer box and a longer wait at every step
(`agents.new_things`: at Normal, 60% sure instead of 45%, and 2 seconds longer). The Settings page says what each step does, with its numbers.
These objects have their own personalities (`config/agents.yaml`):

| Character | Objects | Personality | Looks after |
|-----------|---------|-------------|-------------|
| **Computer** (Monty) | laptop, keyboard, mouse, monitor | Fragile, anxious, emotional center of the desk | breaks, screens at night |
| **Cup** (Glug) | cup, mug, bottle, glass | Innocent, unaware of its own danger | water |
| **Snack** (Munch) | fruit, pizza, chips… | Hedonistic and unapologetic | food log |
| **Book** (Sheets) | book, notepaper, folder | Gentle, anxious, loves other papers | to-do list, lost items |
| **Phone** (Buzz) | cell phone | Charismatic, attention-seeking | phone-free time |
| **Lamp** | lamp | Warm, a morning person | sunlight, dim evenings |
| **AirPods, Headphones, Keys, Cable (Zip), Sprout, Pen, Watch, Glasses, Scissors, Power Bank (Surge)** | | | |

**Everything else** (the glasses, scissors, keys, a stapler ...) never joins the chat, but the desk warnings
still watch it: scissors next to the phone get a "Heads up" from Lens even when the phone isn't in the chat.
People, chairs and the desk itself are never counted at all.

**Lens** is the camera. It speaks up when the view needs fixing (too dark, glare, blurry,
blocked, something cut off, the Pi stopped sending pictures) and says thanks when it's fixed. "Blocked" means
it can't see the desk: the picture went black, it shows nothing at all (paper or a hand on the lens), or one
thing held up to the lens fills most of it.
It also tells you when the Pi runs hot, when its clock is off, and when the Pi's own detector is off
(your computer then does the detecting), and it gives the desk warnings that nobody in the chat can give.
It doesn't use the AI.

**Mac notifications**: warnings, habit reminders and the sips the camera sees also pop up as the Mac's own
notifications, so you see them with the app page closed. They come through the Mac's built-in `osascript`
(macOS may ask once to allow notifications from "Script Editor"), wait during your quiet hours, and each kind can
be switched off in the `notifications:` section of `config/settings.yaml`. On other systems nothing pops up.

## Character Relationships

### Interaction Map

```
                      ┌─────────────┐
            ┌────────→│   LAPTOP    │←────────┐
            │ spill!  │   (Monty)   │ pressure│
            │         └──────┬──────┘         │
            │           ↕ symbiotic            │
     ┌──────┴──────┐                   ┌──────┴──────┐
     │     CUP     │                   │ POWER BANK  │
     │   (Glug)    │                   │   (Surge)   │
     └──────┬──────┘                   └──────┬──────┘
            │           ↑ crumbs!             │
            │    ┌──────┴──────┐              │
            │    │    SNACK    │              │
     knock! │    │   (Munch)   │              │
            │    └─────────────┘              │ crush!
     ┌──────┴──────┐                   ┌──────┴──────┐
     │    CABLE    │                   │    BOOK     │
     │    (Zip)    │←── drag ─────────→│  (Sheets)   │
     └──────┬──────┘                   └─────────────┘
            │ pull!
     ┌──────┴──────┐
     │    PHONE    │
     │   (Buzz)    │ ← distracts everyone
     └─────────────┘
```

### Detailed Relationships

#### Laptop (Monty) — Fragile & Anxious

| Nearby Object | Relationship | Severity |
|---------------|-------------|----------|
| Cup (Glug) | **Existential threat** — liquid spill risk | 🔴 Critical |
| Cable (Zip) | Chain reaction risk — pulling, tugging | 🟡 High |
| Power Bank (Surge) | Pressure and heat risk | 🟡 High |
| Phone (Buzz) | Regulates each other, competes for focus | 🔵 Neutral |
| Snack (Munch) | Crumb contamination on keyboard | 🟡 High |

> **Example:** "I'm feeling a little tense… something heavy is leaning on me."

#### Cup (Glug) — Innocent but Dangerous

| Nearby Object | Relationship | Severity |
|---------------|-------------|----------|
| Laptop (Monty) | **Critical spill risk** — doesn't understand why everyone panics | 🔴 Critical |
| Book (Sheets) | Water damage — fragile material | 🟡 High |
| Cable (Zip) | Knock-over trigger! | 🔴 Critical |
| Phone (Buzz) | Distracted placement nearby | 🟠 Medium |
| Snack (Munch) | Attracts hands → instability | 🟠 Medium |

> **Example:** "I'm just sitting here… I don't see the problem."

#### Keyboard (merged into Monty)

| Nearby Object | Relationship | Severity |
|---------------|-------------|----------|
| Snack (Munch) | **Primary enemy** — crumbs everywhere | 🟡 High |
| Cup (Glug) | Liquid seep risk | 🟡 High |
| Laptop (Monty) | Mutual dependence — symbiotic | 🟢 Positive |
| Phone (Buzz) | Causes one-handed typing errors | 🔵 Low |

> **Example:** "Oh great. More crumbs. Just what I needed."

#### Snack (Munch) — Pleasure-Driven Disruptor

| Nearby Object | Relationship | Severity |
|---------------|-------------|----------|
| Laptop (Monty) | Crumb contamination on keyboard | 🟡 High |
| Cup (Glug) | Increases hand movement near liquids | 🟠 Medium |
| Book (Sheets) | Oil/grease stains | 🟠 Medium |
| Phone (Buzz) | Encourages eating while scrolling | 🔵 Low |

> **Example:** "Life's too short to worry about crumbs."

#### Book / Homework (Sheets) — Gentle & Vulnerable

| Nearby Object | Relationship | Severity |
|---------------|-------------|----------|
| Cup (Glug) | Terrified of water damage | 🟡 High |
| Power Bank (Surge) | Fears being crushed | 🟡 High |
| Snack (Munch) | Dreads oil stains | 🟠 Medium |
| Cable (Zip) | Dragging risk | 🟠 Medium |

> **Example:** "I'm so bored, where are my friends?"

#### Cable / Charger (Zip) — Chaotic Connector

| Nearby Object | Relationship | Severity |
|---------------|-------------|----------|
| Cup (Glug) | **Knock-over risk** — denies involvement | 🔴 Critical |
| Laptop (Monty) | Sudden tug damage | 🟡 High |
| Phone (Buzz) | Pull-off-desk risk | 🟠 Medium |
| Book (Sheets) | Dragging items | 🟠 Medium |
| Power Bank (Surge) | Pulling motion risk | 🟠 Medium |

> **Example:** "I didn't touch anything… I just got pulled."

#### Power Bank (Surge) — Dominant but Clueless

| Nearby Object | Relationship | Severity |
|---------------|-------------|----------|
| Laptop (Monty) | **Pressure and heat** — oblivious to the damage | 🟡 High |
| Book (Sheets) | Crushing risk | 🟡 High |
| Cable (Zip) | Pulling motion — could fall off desk | 🟠 Medium |

> **Example:** "I'm not doing anything."

#### Phone (Buzz) — Attention Magnet

| Nearby Object | Relationship | Severity |
|---------------|-------------|----------|
| Cup (Glug) | Distracted placement near liquids | 🟠 Medium |
| Laptop (Monty) | One-handed typing errors | 🔵 Low |
| Laptop (Monty) | Competes for focus, mutual regulation | 🔵 Low |
| Snack (Munch) | Encourages eating while scrolling | 🔵 Low |
| Cable (Zip) | Pull-off-desk risk | 🟠 Medium |

> **Example:** "Hey, just one more scroll."

---

## Habits from *Protocols*

The desk only nudges when the camera saw a reason, and the message says what it saw.
All numbers are in `config/protocols.yaml`, with the chapter each one comes from.

| Habit | What the book says | What the desk does |
|---|---|---|
| **Water** (Nutrition Protocol 2) | 18–24 oz on waking; ~80 oz (2.4 L) in the first 10 hours; less in the hours before bed | A sip counts when the drink is lifted and put back, or goes out of the picture and comes back near where it was but moved a little (a cup the camera only missed for a moment, or a hand covered, comes back exactly where it was, so that isn't a sip; nor is a covered camera, or a cup that seems to move while nobody is at the desk). Nudges when you're behind pace and haven't sipped in 45 min; quiet in the 2 hours before bed. |
| **Breaks** (Exercise Protocol 1, Learning Protocol 5) | Walk 5 min for every 30 min sitting; 5–10 min after meals; no phone during breaks | Counts sitting time; a break logs itself when you're away 5 min. Nudges after 30 min, and after food leaves the desk. |
| **Light, day and night** (Sleep 1–3, Light 1–3, Sleep 14) | Sunlight within an hour of waking (10/20/30 min clear/cloudy/overcast); light before sunset; dim lights and no screens in the last hour before bed | Asks about morning sunlight, reminds before sunset, notices a bright room at night (the Pi camera measures lux) or a dim room in the day, and says "screens off" an hour before bed. |
| **Phone-free time** (Learning Protocol 1) | Put your phone away; even having it nearby costs focus | Tracks phone-free desk time and streaks; nudges when the phone sits on the desk while you work, and in the last hour before bed. |

Set your wake time and bedtime in the app (Settings → Your day). The Habits page shows
today's numbers and has buttons for what the camera can't see (a sip, a break, going outside).

**To-dos** are Book's list. You can give one a due date (in the app, or in the chat: "add my essay, due
Friday"). Book reminds you the day before, on the day, and each day it's late; a to-do with no date gets a
check-in every 2 hours, naming a different one each time. Desk Talk keeps the list itself (`data/todos.json`),
and ticking one off shows in the Event log.

---

## The app

One window, three columns:

1. **Chats (left)**: "The desk" group chat, then everyone who's on the desk (green dot) and
   anyone who left earlier. Habits, Event log, Captures and Settings sit underneath.
2. **Conversation (middle)**: join / leave notes, colored bubbles, proof photos, @mentions.
   Each 1-on-1 chat has its tracker on top (Cup's water, Computer's breaks, Book's to-do list…).
3. **Desk cam (right)**: the live picture with boxes, who's on the desk, current risks, proof photos.
   Until the camera connects it shows the command to run on the Pi.

**Event log** page: what happened each day, newest first: sips, breaks, food, finished to-dos, sunlight,
reminders, warnings and camera problems, with filters for habits, reminders and warnings.

**Desk edges**: in Settings, tap the corners of your desk on the camera picture. Then only
things on the desk can join the chat, and things near a real edge get a warning.

---

## Detection

| | |
|---|---|
| Model | **YOLO26 medium, Objects365** (`yolo26m-objv1-150.pt`): 365 kinds of objects, including earphones, keys, pens, watches, glasses and notepaper. The old `yolo26n.pt` knew 80. |
| Where it runs | With the Pi as the camera: **on the Pi**, in Docker, as an ncnn model (the fastest kind on the Pi's processor), set in `pi.detector`. Your computer runs the same model (`detector`) for the phone and the webcam, and for any Pi picture whose detector was off. Both turn YOLO's results into boxes with the same shared code, so the names and boxes match. |
| Speed | 3 pictures a second. On an Apple-chip Mac it runs on the GPU (`device: auto`); too slow there? `yolo26s-objv1-150.pt`. On the Pi the medium model may only manage about one picture a second; the Pi speed test in [`benchmarks/`](benchmarks/README.md) will pick its final model. When the Pi's detector is slower than the camera, the pictures in between are only shown in the live view. |
| Joining | seen 3 s → joins; gone 45 s → leaves (habit rules use a shorter 1 s / 2.5 s) |
| Safety | danger zones, desk edges, chain reactions, objects approaching (`desktalk/core/vision/`, tuned in `config/safety_rules.json` and the Settings page) |

**On your Mac**, in the `desk-talk` folder:

```bash
uv run python scripts/download_models.py --test    # download the model and time it on your computer
uv run python scripts/download_models.py --list    # every object it can detect
```

---

## Getting started

### On your computer

Install [uv](https://docs.astral.sh/uv/) once (`brew install uv`, or see its page). Then, **on your Mac**,
in the `desk-talk` folder:

```bash
uv sync --all-packages                   # Python 3.12 and every package, exactly as in uv.lock
cp .env.example .env                     # add an AI key, or OLLAMA_URL for Ollama
uv run python server.py                  # the first start downloads the YOLO model (about 45 MB) into models/
```

Open **http://localhost:5000**. `server.py` prints the command to run on the Pi, with your computer's
address and the pairing key. A setting with a typo or a bad value stops it at the start, with a message
that names the file and the setting.

### On the Raspberry Pi

The full guide, with what to do when something goes wrong:
[raspberry_pi/README.md](raspberry_pi/README.md). It needs 64-bit Raspberry Pi OS and about 3 GB free
on the SD card. In short:

1. **On the Pi**, the camera program's packages and the code:

   ```bash
   sudo apt update && sudo apt install -y git python3-picamera2 python3-opencv python3-yaml
   git clone https://github.com/randomnoobi/desk-talk.git ~/desk-talk
   ```

2. **On the Pi**, Docker (then log out and back in) and the detector container:

   ```bash
   curl -fsSL https://get.docker.com | sh && sudo usermod -aG docker $USER
   ```

   ```bash
   cd ~/desk-talk/raspberry_pi/detector_service && docker compose up -d --build
   ```

3. **On the Pi**, check everything, then start the camera program with the address and key that
   `server.py` printed:

   ```bash
   cd ~/desk-talk && python3 -m raspberry_pi.camera --self-test --server http://<your computer>:5000 --key <key>
   ```

   ```bash
   python3 -m raspberry_pi.camera --server http://<your computer>:5000 --key <key>
   ```

Without the detector container the camera program still works: your computer then does all the detecting.

### Demo without a server

Open the GitHub Pages site, or serve `web_app/` with any static server. The app plays a short
demo where four characters join the chat.

---

## Configuration

| File | What's in it |
|---|---|
| `config/settings.yaml` | every number: camera source and rate, the Pi, detection, join/leave times, event log, AI limits, idle chat, Lens, the web server (areas below) |
| `config/agents.yaml` | who each object becomes: name, personality, color, which habits it speaks for |
| `config/protocols.yaml` | the habit rules from *Protocols* and your day (wake / bed) |
| `config/prompts.yaml` | every word Desk Talk says or tells the AI: the prompts, Lens's lines, habit nudges and tips, desk alerts, the replies when the AI is down. The code only fills in the blanks (`{on_waking_ml}` ...) |
| `config/safety_rules.json` | danger zones and desk corners (the Settings page writes this) |
| `config/settings.local.yaml` | your own overrides (optional, not uploaded) |
| `config/benchmark.yaml` | what the YOLO speed test in `benchmarks/` tries |
| `raspberry_pi/detector_service/.env` | the Pi's detector container: its port, memory limit, retry waits, Docker's health check and log file sizes (no secrets) |
| `.env` | AI keys / `OLLAMA_URL` (never uploaded) |

Areas of `config/settings.yaml` that came with the new design:

| Area | What it's for |
|---|---|
| `pi` | the camera program on the Pi: pictures a second, cleanup, upload waits, temperature and clock warnings. The Pi asks your computer for this section, so you only change it here. |
| `pi.detector` | the Pi's detector container: model, format (`ncnn` or `pt`), picture size, how long to wait per picture, when to stop asking a container that keeps failing |
| `detector` | YOLO on your computer (phone, webcam, and the backup for the Pi) |
| `supervisor` | how background jobs start again after a crash: 1 s, then doubling up to 60 s |
| `event_stream` | how many events may wait per consumer before old picture events are dropped |
| `web` | the web server: port, upload size limits, live updates, and the app page's refresh times, waits and list sizes (the page asks for them at `GET /api/ui_settings`) |
| `drawing` | the boxes and desk drawings on the live view: colors, line widths, writing sizes, how see-through |
| `notifications` | the Mac's own pop-ups: on or off, which kinds (warnings, reminders, sips), how long one may take to show |
| `logging` | the log file in `data/logs/` |
| `storage` | where preferences, habit days and learned personalities are kept (`data/`) |

Every setting is checked when Desk Talk starts (`desktalk/config/`), and so is every blank in
`config/prompts.yaml`.

---

## Tests

**On your Mac**, in the `desk-talk` folder:

```bash
uv run pytest -m "not slow"                # fast tests: no model, network, AI key, camera, Pi, Docker or browser needed
uv run pytest tests/characterization       # every web route still answers as before
uv run pytest tests/contracts              # the fakes behave like the real parts
uv run pytest -m slow                      # the real YOLO model, and ncnn against .pt (needs the models in models/)
uv run playwright install chromium         # once, for the browser tests
uv run pytest -m e2e                       # the real app page in a browser
uv run pytest -m docker                    # build and start the Pi's detector image (needs Docker and the internet)
uv run ruff check .                        # lint
uv run pyright                             # type check: 0 errors
uv run vulture                             # dead code check: prints nothing
```

GitHub runs the lint, the type check, the dead code check and the fast tests on every push
(`.github/workflows/tests.yml`). The fast tests run there with `--cov`, which fails when they cover less
of the code than `fail_under` in `pyproject.toml`. It also runs the developer page's own tests and builds
it (`npm test` and `npm run build` in `dev_ui/`).

| Folder | What it checks |
|---|---|
| `tests/unit/` | each part on its own, with fakes and a clock you move by hand: the app's parts, the camera program, the detector service (with a fake YOLO) |
| `tests/contracts/` | one test suite per interface; the fake and every real version must pass the same one |
| `tests/integration/` | the whole app built by `desktalk/bootstrap.py` with a fake detector and a fake AI: pictures through the whole pipeline, the web API, background jobs |
| `tests/characterization/` | every route of both web servers: status code, headers and JSON shape, saved in `snapshots/`. A route without a case fails the tests |
| `tests/slow/` | the real model: the ncnn model finds the same objects as the `.pt`, and the detector service with a real model (`-m slow`) |
| `tests/docker/` | builds the detector's Docker image, starts it and asks it for detections (`-m docker`, opt-in) |
| `tests/e2e/` | the real app page in a browser, against the whole app with a fake detector and a fake AI (`-m e2e`, opt-in); `test_full_stack.py` sends pictures from the camera program through the detector container to the page (`-m docker`) |
| `tests/fakes/` | the fakes: `FakeClock`, `FakeDetector`, `FakeChatModel`, `SyncEventBus` ... |

The benchmark tool has its own tests: see [benchmarks/README.md](benchmarks/README.md).

---

## Developer page

A second page, for working on Desk Talk rather than using it ([docs/CHECKING.md](docs/CHECKING.md) says how to
check everything with it). It has five tabs:

- **Checks**: one button that runs the fast tests, the lint, the type check, the dead code check and the page's
  own tests, and every test on its own (or a whole file or folder), with the printout as it runs.
- **Live**: each object's state (present, absent), the changes as they happen, and a chart of how the
  smoothing turned the last pictures into that state, so you can see why something flickers. Click an object
  for its whole trace: when it joined the chat, left or was turned away (and why: how sure, how long, the
  sensitivity), its state changes, and a few pictures of it with its box (kept in `data/trace_photos/`).
- **System**: how the Pi is doing (pictures a second, temperature, disk, its detector) and every part on
  this computer (background jobs, timed jobs, the event stream, detection, the AI).
- **Database**: the tables in the SQLite databases, and a box to run a query that only reads.
- **Storage**: what this browser keeps for the app page (localStorage), and the files in `data/`.

You need [Node](https://nodejs.org/) 22 for it. **On your Mac**, in the `desk-talk` folder, once (and
again after `dev_ui/` changes):

```bash
cd dev_ui && npm install && npm run build
```

Then start Desk Talk as usual and open **http://localhost:5000/dev/** (`web.port`, 5000 unless you changed
it). Only this computer gets answers from it: other devices on the Wi-Fi, and other web pages open in
your browser, are turned away (403). It can't change anything, apart from starting and stopping a test
run and deleting a localStorage key after you say yes. The database is opened read-only. To switch it off, set
`dev.enabled: false` in `config/settings.local.yaml`; its other numbers are in the `dev:` section of
`config/settings.yaml`.

To work on the page itself, `npm run dev` in `dev_ui/` serves it at http://localhost:5173/dev/
(`dev.vite_port`) and changes show up as you save (the server must be running too). More in [dev_ui/README.md](dev_ui/README.md).

---

## Project structure

```
desk-talk/
├── server.py                 # start Desk Talk on your computer: uv run python server.py
├── pyproject.toml, uv.lock   # the uv workspace: desktalk, desktalk-shared, desktalk-detector
├── config/                   # every setting: settings.yaml, agents.yaml, protocols.yaml, prompts.yaml (the wording), safety_rules.json
├── desktalk/                 # the app that runs on your computer
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
│   │   ├── detector/         #     YOLO on this computer (Ultralytics)
│   │   ├── llm/              #     the AI (Gemini / OpenAI / Groq / Ollama), with time limits, retries and a breaker
│   │   ├── picture/          #     OpenCV: the picture tools, and the drawing on the live view
│   │   ├── storage/          #     the event log files and small JSON files (preferences, habit days)
│   │   └── dev/              #     for the developer page: the test runner, the read-only database and data folder
│   ├── runtime/              #   Supervisor (starts crashed background jobs again), Scheduler, clock, logging
│   ├── web/                  #   FastAPI: app.py, routers/ (one file per area), schemas.py, serve.py (uvicorn)
│   └── util.py               #   small helpers
├── shared/desktalk_shared/   # used on the Pi and here: Detection, the upload format, the detector's API, backoff, clock, worker thread
├── raspberry_pi/             # what runs on the Pi (raspberry_pi/README.md)
│   ├── camera/               #   the camera program, on the Pi's own python3 (no Docker): cli.py, main.py, workers.py ...
│   └── detector_service/     #   YOLO in Docker: Dockerfile, compose.yaml, .env, desktalk_detector/
├── benchmarks/               # times YOLO models on your Mac and on the Pi, to pick the Pi's model
├── web_app/                  # the app you open at localhost:5000 (HTML / CSS / JS, art, fonts)
├── phone_camera/             # the page a phone opens to be the camera instead of the Pi
├── dev_ui/                   # the developer page at localhost:5000/dev/ (Vite + React + TypeScript; dev_ui/README.md)
├── models/                   # YOLO model files (downloaded, not uploaded)
├── tests/                    # unit, contracts, integration, characterization, slow, docker, e2e; fakes/, e2e_kit/
├── scripts/                  # download_models.py, camera_preview.py, yolo_webcam_detect.py, capture_app_screenshots.py
├── docs/                     # ARCHITECTURE.md, AGENT_SYSTEM.md, plans/
└── data/                     # event log, habit days, preferences, log files (made when it runs, not uploaded)
```

---

## Design Principles

1. **Familiar over novel** — It's a chat app. Everyone knows how to use a chat app.
2. **Personality through typography** — Each character's identity comes from their bubble shape, color, and font.
3. **Relationship-driven** — Every interaction is grounded in the character relationship chart. The LLM uses these relationships to generate contextually appropriate messages.
4. **Ambient awareness** — Notifications are gentle. The app nudges, it doesn't nag.
5. **Camera stays invisible** — The detection system runs quietly. Users interact with characters, not with CV output.
6. **Local-first** — Everything runs on your desk, your cameras, your device.

---

## GitHub Pages (app demo)

Pushing to **`main`** runs [`.github/workflows/deploy-pages.yml`](.github/workflows/deploy-pages.yml), which publishes the **`web_app/`** folder (in demo mode, since there's no server there).

1. Create a new repository on GitHub and push this project (`main`).
2. On GitHub: **Settings → Pages → Build and deployment → Source: GitHub Actions**.
3. Open the **Actions** tab and confirm the **Deploy site to GitHub Pages** workflow succeeds.
4. Your site URL will be `https://<username>.github.io/<repo>/` (unless you use a custom domain).

Model files (`*.pt`) are gitignored. `server.py` downloads the one it needs into **`models/`** the first
time it starts (or `uv run python scripts/download_models.py` does it ahead of time).

---

## License

TBD
