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

Nobody is in the chat at first. When the camera has seen something for 3 seconds it joins;
when it's been gone 45 seconds it leaves. These objects have their own personalities
(`config/agents.yaml`):

| Character | Objects | Personality | Looks after |
|-----------|---------|-------------|-------------|
| **Computer** (Monty) | laptop, keyboard, mouse, monitor | Fragile, anxious, emotional center of the desk | breaks, screens at night |
| **Cup** (Glug) | cup, mug, bottle, glass | Innocent, unaware of its own danger | water |
| **Snack** (Munch) | fruit, pizza, chips… | Hedonistic and unapologetic | food log |
| **Paper** (Sheets) | book, notepaper, folder | Gentle, anxious, loves other papers | to-do list, lost items |
| **Phone** (Buzz) | cell phone | Charismatic, attention-seeking | phone-free time |
| **Lamp** | lamp | Warm, a morning person | sunlight, dim evenings |
| **AirPods, Headphones, Keys, Cable (Zip), Sprout, Pen, Watch, Glasses, Scissors, Power Bank (Surge)** | | | |

**Anything else** the detector finds joins too (a stapler becomes "Stapler"), and the AI
invents a personality for it. People, chairs and the desk itself never join.

**Lens** is the camera. It speaks up when the view needs fixing (too dark, glare, blurry,
covered, something cut off, the Pi stopped sending pictures) and says thanks when it's fixed.
It also tells you when the Pi runs hot, when its clock is off, and when the Pi's own detector is off
(your computer then does the detecting). It doesn't use the AI.

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
     │    CABLE    │                   │    PAPER    │
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
| Paper (Sheets) | Water damage — fragile material | 🟡 High |
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
| Paper (Sheets) | Oil/grease stains | 🟠 Medium |
| Phone (Buzz) | Encourages eating while scrolling | 🔵 Low |

> **Example:** "Life's too short to worry about crumbs."

#### Paper / Homework (Sheets) — Gentle & Vulnerable

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
| Paper (Sheets) | Dragging items | 🟠 Medium |
| Power Bank (Surge) | Pulling motion risk | 🟠 Medium |

> **Example:** "I didn't touch anything… I just got pulled."

#### Power Bank (Surge) — Dominant but Clueless

| Nearby Object | Relationship | Severity |
|---------------|-------------|----------|
| Laptop (Monty) | **Pressure and heat** — oblivious to the damage | 🟡 High |
| Paper (Sheets) | Crushing risk | 🟡 High |
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
| **Water** (Nutrition Protocol 2) | 18–24 oz on waking; ~80 oz (2.4 L) in the first 10 hours; less in the hours before bed | A sip counts when the cup leaves the desk and comes back. Nudges when you're behind pace and haven't sipped in 45 min; quiet in the 2 hours before bed. |
| **Breaks** (Exercise Protocol 1, Learning Protocol 5) | Walk 5 min for every 30 min sitting; 5–10 min after meals; no phone during breaks | Counts sitting time; a break logs itself when you're away 5 min. Nudges after 30 min, and after food leaves the desk. |
| **Light, day and night** (Sleep 1–3, Light 1–3, Sleep 14) | Sunlight within an hour of waking (10/20/30 min clear/cloudy/overcast); light before sunset; dim lights and no screens in the last hour before bed | Asks about morning sunlight, reminds before sunset, notices a bright room at night (the Pi camera measures lux) or a dim room in the day, and says "screens off" an hour before bed. |
| **Phone-free time** (Learning Protocol 1) | Put your phone away; even having it nearby costs focus | Tracks phone-free desk time and streaks; nudges when the phone sits on the desk while you work, and in the last hour before bed. |

Set your wake time and bedtime in the app (Settings → Your day). The Habits page shows
today's numbers and has buttons for what the camera can't see (a glass of water, going outside).

---

## The app

One window, three columns:

1. **Chats (left)**: "The desk" group chat, then everyone who's on the desk (green dot) and
   anyone who left earlier. Habits, Event log, Captures and Settings sit underneath.
2. **Conversation (middle)**: join / leave notes, colored bubbles, proof photos, @mentions.
   Each 1-on-1 chat has its tracker on top (Cup's water, Computer's breaks, Paper's to-do list…).
3. **Desk cam (right)**: the live picture with boxes, who's on the desk, current risks, proof photos.
   Until the camera connects it shows the command to run on the Pi.

**Event log** page: a timeline of what was on the desk today, the events (joins, leaves,
alerts, habits, reminders, camera problems), and what one picture's event looks like.

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
| `config/safety_rules.json` | danger zones and desk corners (the Settings page writes this) |
| `config/settings.local.yaml` | your own overrides (optional, not uploaded) |
| `config/benchmark.yaml` | what the YOLO speed test in `benchmarks/` tries |
| `raspberry_pi/detector_service/.env` | the Pi's detector container: its port and memory limit (no secrets) |
| `.env` | AI keys / `OLLAMA_URL` (never uploaded) |

Areas of `config/settings.yaml` that came with the new design:

| Area | What it's for |
|---|---|
| `pi` | the camera program on the Pi: pictures a second, cleanup, upload waits, temperature and clock warnings. The Pi asks your computer for this section, so you only change it here. |
| `pi.detector` | the Pi's detector container: model, format (`ncnn` or `pt`), picture size, how long to wait per picture, when to stop asking a container that keeps failing |
| `detector` | YOLO on your computer (phone, webcam, and the backup for the Pi) |
| `supervisor` | how background jobs start again after a crash: 1 s, then doubling up to 60 s |
| `event_stream` | how many events may wait per consumer before old picture events are dropped |
| `web` | the web server: port, upload size limits, live updates |
| `logging` | the log file in `data/logs/` |
| `storage` | where preferences, habit days and learned personalities are kept (`data/`) |

Every setting is checked when Desk Talk starts (`desktalk/config.py`).

---

## Tests

**On your Mac**, in the `desk-talk` folder:

```bash
uv run pytest -m "not slow"                # fast tests: no model, network, AI key, camera, Pi or Docker needed
uv run pytest tests/characterization       # every web route still answers as before
uv run pytest tests/contracts              # the fakes behave like the real parts
uv run pytest -m slow                      # the real YOLO model, and ncnn against .pt (needs the models in models/)
uv run pytest -m docker tests/docker       # build and start the Pi's detector image (needs Docker and the internet)
uv run ruff check <files>                  # lint
```

GitHub runs the lint and the fast tests on every push (`.github/workflows/tests.yml`).

| Folder | What it checks |
|---|---|
| `tests/unit/` | each part on its own, with fakes and a clock you move by hand: the app's parts, the camera program, the detector service (with a fake YOLO) |
| `tests/contracts/` | one test suite per interface; the fake and every real version must pass the same one |
| `tests/integration/` | the whole app built by `desktalk/bootstrap.py` with a fake detector and a fake AI: pictures through the whole pipeline, the web API, background jobs |
| `tests/characterization/` | every route of both web servers: status code, headers and JSON shape, saved in `snapshots/`. A route without a case fails the tests |
| `tests/slow/` | the real model: the ncnn model finds the same objects as the `.pt`, and the detector service with a real model (`-m slow`) |
| `tests/docker/` | builds the detector's Docker image, starts it and asks it for detections (`-m docker`, opt-in) |
| `tests/fakes/` | the fakes: `FakeClock`, `FakeDetector`, `FakeChatModel`, `SyncEventBus` ... |

The benchmark tool has its own tests: see [benchmarks/README.md](benchmarks/README.md).

---

## Project structure

```
desk-talk/
├── server.py                 # start Desk Talk on your computer: uv run python server.py
├── pyproject.toml, uv.lock   # the uv workspace: desktalk, desktalk-shared, desktalk-detector
├── config/                   # every setting: settings.yaml, agents.yaml, protocols.yaml, safety_rules.json
├── desktalk/                 # the app that runs on your computer
│   ├── bootstrap.py          #   builds every part and connects them (the only place that knows the real classes)
│   ├── config.py             #   reads config/ and .env into typed settings; a bad value stops the start
│   ├── ports.py              #   the interfaces between the parts
│   ├── domain/               #   plain data: Detection, Frame, events, agent profiles, nudges
│   ├── core/                 #   the logic, written only against ports.py
│   │   ├── vision/           #     producer (pictures → events), desk rules, desk monitor, Lens's checks, live view
│   │   ├── events/           #     the event stream
│   │   ├── agents/           #     profiles + roster (join / leave)
│   │   ├── habits/           #     Protocols rules, habit tracker, reminders, preferences, sun times
│   │   └── chat/             #     message generator, message hub, prompts; workers/: idle chat, lost items, Lens, recap
│   ├── adapters/             #   the real things behind the interfaces
│   │   ├── camera/           #     the Pi, phone and webcam as picture sources
│   │   ├── detector/         #     YOLO on this computer (Ultralytics)
│   │   ├── llm/              #     the AI (Gemini / OpenAI / Groq / Ollama), with time limits, retries and a breaker
│   │   └── storage/          #     the event log files and small JSON files (preferences, habit days)
│   ├── runtime/              #   Supervisor (starts crashed background jobs again), Scheduler, clock, logging
│   ├── web/                  #   FastAPI: app.py, routers/ (one file per area), schemas.py, serve.py (uvicorn)
│   ├── desk_state.py         #   what's on the desk right now
│   └── util.py               #   small helpers
├── shared/desktalk_shared/   # used on the Pi and here: Detection, the upload format, the detector's API
├── raspberry_pi/             # what runs on the Pi (raspberry_pi/README.md)
│   ├── camera/               #   the camera program, on the Pi's own python3 (no Docker)
│   └── detector_service/     #   YOLO in Docker: Dockerfile, compose.yaml, .env, desktalk_detector/
├── benchmarks/               # times YOLO models on your Mac and on the Pi, to pick the Pi's model
├── web_app/                  # the app you open at localhost:5000 (HTML / CSS / JS, art, fonts)
├── phone_camera/             # the page a phone opens to be the camera instead of the Pi
├── models/                   # YOLO model files (downloaded, not uploaded)
├── tests/                    # unit, contracts, integration, characterization, slow, docker; fakes/
├── scripts/                  # download_models.py, camera_preview.py, yolo_webcam_detect.py, capture_app_screenshots.py
├── docs/                     # ARCHITECTURE.md, AGENT_SYSTEM.md, SAFETY_*.md, plans/, images/
├── run_safety_pipeline.py    # the older standalone safety checker (docs/SAFETY_PIPELINE.md)
├── safety_pipeline/          #   its modules
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
