# Desk Talk

**A habit-tracking app that brings your desk objects to life.**

A camera watches your desk. Every object it recognizes joins a group chat as a
character ("Cup joined the chat"), and leaves when you take it away. The
characters look out for each other (Computer panics when the cup gets close) and
for you: water, movement breaks, sunlight and dim evenings, and phone-free
focus time, following Andrew Huberman's *Protocols*.

---

## How it works

Everything runs on the Raspberry Pi, in three Docker containers from one compose file. Nothing else needs to be on
or installed: no computer, no AI, no account. Phones and computers on your Wi-Fi only open the app's page.

```
Raspberry Pi (docker compose, raspberry_pi/compose.yaml)
 camera                      detector                       app
 ──────                      ────────                       ───
 3 pictures a second,   ───▶ YOLO: what is in this picture? ──┐
 kept on the Pi,        ◀─── (no open port: only camera       │
 deleted every 10 min        and app can reach it)            ▼
        │                                          event log (data/events/), characters
        └── picture + what was found, one upload ─▶ join and leave the chat, safety warnings,
            for each picture                        habit reminders said on the speaker,
                                                    the page on port 5000
                                                        ▲
   http://desktalk.local:5000, from any phone or computer on your Wi-Fi
   optional, text only: chat → an AI on your own computer (Ollama) or on your cloud server
   optional, events only (never pictures or to-do text): your cloud server
```

- **`camera`** (`raspberry_pi/camera/`) takes the pictures, keeps them on the Pi, deletes them every 10 minutes,
  asks the detector what's in each one, and hands each picture with what was found to the app.
- **`detector`** (`raspberry_pi/detector_service/`) runs YOLO. It has no open port: only the other two containers
  can reach it. If it is still loading its model, or has crashed, the app gets the pictures with nothing found
  (the live view still works), Lens says the detector is off, and Docker starts it again. There is no second
  detector to fall back on.
- **`app`** (`desktalk/`) turns every picture into a detection event. Events flow through an **event stream** to
  separate consumers (decoupled, so a slow AI reply never holds up the camera), and are saved in the **event log**.
  Every 10 minutes the log is compacted: per-picture events become "the cup was on the desk from 2:02 to 2:31"
  intervals. It also serves the page.
- **Pictures never leave the Pi**, except to a page someone opens on your own Wi-Fi. No AI gets one, and the cloud
  server never does. There is no login on the Wi-Fi: anyone who can join your Wi-Fi can open the page.
- **A phone** (the page in `phone_camera/`) or a **webcam** can be the camera instead, and you can run the app on
  a computer: that is for working on Desk Talk (see [On a computer](#on-a-computer-for-working-on-it)).

Inside, every part is written against small interfaces (`desktalk/ports.py`). One file,
`desktalk/bootstrap.py`, builds the real parts and connects them; the tests build the same parts with
fakes. Background jobs (reminders, Lens, small talk ...) start again by themselves after a crash, and
the AI, the detector or the Wi-Fi going away never stops the rest.

> **What has and hasn't been tried on a real Pi.** Tried on a real Pi 5: all three containers together with
> the real camera, pictures flowing to events, one container restarting by itself, the update command, the
> install command in a dry run, the speak command (with a stand-in for the speaker, so nobody has heard
> it), and that no picture goes out. **Not tried yet:** the install command from a fresh card, a real USB speaker,
> a real object on the desk, a power cut, a start with no Wi-Fi, no network at all, a real phone, a real
> cloud link, and closing the laptop an AI runs on. Those are the steps in [docs/PI_CHECKS.md](docs/PI_CHECKS.md);
> until they pass, expect the Pi steps to need fixing the first time.

More detail: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

**No AI needed.** Warnings, habit reminders, the characters joining and leaving, the event log and the habits all
work with no AI at all, in their ready-made words. An AI only adds **chat** with the characters and nicer wording.
No AI runs on the Pi, and AI keys never go on it. To add one, set `llm.ai_url` in `raspberry_pi/settings.pi.yaml`
on the Pi, then **on the Pi** run `cd ~/desk-talk/raspberry_pi && docker compose restart app`:

- **Ollama on your own computer**: `ai_url: http://<your computer>:11434/v1`, and `llm.ollama_model` for the model
  (`ollama pull` it first). Text only goes there: your chat messages, and the words the characters say by themselves
  (reminders, small talk, the recap), which can include the text of your to-dos. Pictures never go: the Pi keeps
  `llm.send_pictures: false`. If the computer sleeps, warnings still come at once in their ready-made words, and a
  reminder waits at most `llm.reminder_wait_sec` for it.
- **Your cloud server**: `ai_url: <its address>/v1`, after linking it. Only chat you start yourself goes there, as
  text, and the server doesn't store or log it. Reminders, small talk and the recap don't use it.

**An account is not needed either.** A cloud server is optional: you run it yourself, in Docker, and it keeps your
habit events (never pictures, never to-do text) and writes a weekly report you read on its website. Until you link
the Pi to one, nothing is sent: the install command does it (`--server`, see the Pi guide), or on a computer
`scripts/link_cloud.py`. See [cloud/README.md](cloud/README.md).

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
It also tells you when the Pi runs hot or is slowing itself down, and when the detector is off (no objects are
found until it's back), and it gives the desk warnings that nobody in the chat can give.
It doesn't use the AI.

**The speaker**: warnings, habit reminders and the sips the camera sees are also said out loud, so you hear them
with the app page closed. Plug a USB speaker into the Pi (any time: no restart needed). They wait during your quiet
hours, and each kind can be switched off in the `notifications:` section of `config/settings.yaml`. With no speaker
plugged in the Pi says so once in its log and carries on. (A Mac running the app for development shows them as its
own pop-ups instead, through `osascript`.)

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
   Click anything under "On the desk" for its story: when it appeared, joined the chat, moved or went away,
   how sure the camera was each time, and the picture taken at each step with its box drawn on (for "went
   away", the empty spot with its last box). The pictures are kept in `data/trace_photos/`
   (settings → `object_story`).
   Until the camera connects it says so. (Only an app running on a computer, for development, shows the command to
   run on the Pi.)

**Event log** page: what happened each day, newest first: sips, breaks, food, finished to-dos, sunlight,
reminders, warnings and camera problems, with filters for habits, reminders and warnings.

**Desk edges**: in Settings, tap the corners of your desk on the camera picture. Then only
things on the desk can join the chat, and things near a real edge get a warning.

---

## Detection

| | |
|---|---|
| Model | **YOLO26 medium, Objects365** (`yolo26m-objv1-150.pt`): 365 kinds of objects, including earphones, keys, pens, watches, glasses and notepaper. The old `yolo26n.pt` knew 80. |
| Where it runs | **On the Pi**, in the `detector` container, as an ncnn model (the fastest kind on the Pi's processor), set in `pi.detector`. There is no backup: if the detector is off, no objects are found until it is back. On a computer running the app for development, the phone and the webcam are detected by the app itself (`detector`), with the same model and the same shared code that turns YOLO's results into boxes. |
| Speed | The camera takes 3 pictures a second. Measured on a Pi 5 (8 GB) with the real camera and the whole stack running: all 3 a second reach the app, and the detector answered about 1.5 a second with the medium model at 640 pixels (it is slower while the Pi is speaking). That is not the final number: the Pi speed test in [`benchmarks/`](benchmarks/README.md) will pick the Pi's final model. When the detector is slower than the camera, the pictures in between are only shown in the live view. On an Apple-chip Mac the app's own detector runs on the GPU (`device: auto`); too slow there? `yolo26s-objv1-150.pt`. |
| Joining | seen 3 s → joins; gone 45 s → leaves (habit rules use a shorter 1 s / 2.5 s) |
| Safety | danger zones, desk edges, chain reactions, objects approaching (`desktalk/core/vision/`, tuned in `config/safety_rules.json` and the Settings page) |

**On your Mac** (this is for the app's own detector there; the Pi's install command fetches its model itself), in the
`desk-talk` folder:

```bash
uv run python scripts/download_models.py --test    # download the model and time it on your computer
uv run python scripts/download_models.py --list    # every object it can detect
```

---

## Getting started

### On the Raspberry Pi

This is how people run Desk Talk. The full guide, with what to do when something goes wrong:
[raspberry_pi/README.md](raspberry_pi/README.md). You need:

- a Raspberry Pi with the camera module, running 64-bit Raspberry Pi OS, on your Wi-Fi. Only a Pi 5 with 8 GB has
  been tried; the install stops on a Pi with under about 2 GB of memory, and says to lower the detector's memory
  below about 3.5 GB;
- **a 32 GB SD card or larger.** The first install needs about 14 GB free (the three images and Docker's saved
  build steps take about 12.6 GB), so a 16 GB card is too small and the install stops and says so;
- a USB speaker, if you want to hear warnings and reminders (nothing else needs it).

**On the Pi**, one command:

```bash
git clone https://github.com/randomnoobi/desk-talk ~/desk-talk && bash ~/desk-talk/raspberry_pi/install.sh
```

It checks the Pi, installs Docker if it's missing, makes `raspberry_pi/.env` with this Pi's own numbers, fetches
the model and starts the three containers, which start again by themselves when the Pi boots. The first time
takes a while (it builds the images). It is safe to run again. At the end it prints the address to open, usually
**http://desktalk.local:5000**, on a phone or computer on the same Wi-Fi. To get new code later, **on the Pi**:
`bash ~/desk-talk/raspberry_pi/update.sh`.

The install command has not been run on a fresh card yet (see the note above); its dry run (`--dry-run`) and the
update command have been run on a real Pi.

### On a computer (for working on it)

`uv run python server.py` is for development: the tests, the phone camera and the webcam use it. It is no longer how
people run the app. Install [uv](https://docs.astral.sh/uv/) once (`brew install uv`, or see its page). Then,
**on your Mac**, in the `desk-talk` folder:

```bash
uv sync --all-packages                   # Python 3.12 and every package, YOLO included, exactly as in uv.lock
cp .env.example .env                     # add an AI key, or OLLAMA_URL for Ollama (optional)
uv run python server.py                  # the first start downloads the YOLO model (about 45 MB) into models/
```

Plain `uv sync` no longer brings YOLO (PyTorch is big, and the Pi's app image doesn't want it). To run YOLO on a
computer you need `uv sync --all-packages` (everything) or `uv sync --extra computer-detector` (just the app and
YOLO); without it `server.py` stops and says so. Or set `detector.enabled: false` to run with no detector of its own.

Open **http://localhost:5000**. A setting with a typo or a bad value stops it at the start, with a message that
names the file and the setting. With the Pi as the camera, the page shows the command to run on the Pi; the
pairing key is on the page only, never in the log or the terminal.

> **If you ran an older version on a computer.** Older versions wrote the pairing key into `data/logs/` (in the
> Pi command and the phone link). A key that is only in your own log is a small risk, but clear it if you share the
> folder or back it up. **On your Mac**, in the `desk-talk` folder, find the lines, delete them, and make a new key
> (restart `server.py` after; the phone page and any Pi started by hand need the new key):
>
> ```bash
> grep -l -e '--key' -e 'phone?key=' data/logs/* 2>/dev/null
> sed -i '' -E '/--key|phone\?key=/d' data/logs/desktalk.log*
> rm certs/pair-key.txt
> ```
>
> (On Linux, `sed -i -E ...` with no `''`.) Anything you copied from the terminal's scrollback is yours to clear.

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
| `config/settings.local.yaml` | your own overrides on a computer (optional, not uploaded). On the Pi the same job is done by `raspberry_pi/settings.pi.yaml`, which the containers mount in its place: the Pi's own settings, such as `llm.ai_url`, go there |
| `config/benchmark.yaml` | what the YOLO speed test in `benchmarks/` tries |
| `raspberry_pi/.env` | the Pi's three containers: ports, memory limits, Docker's health checks, log file sizes, and the install command's numbers (no secrets). The install command makes it from `raspberry_pi/.env.example`; it isn't in git |
| `.env` | on a computer only: AI keys / `OLLAMA_URL` (never uploaded). Nothing like it goes on the Pi |

Areas of `config/settings.yaml` that came with the new design:

| Area | What it's for |
|---|---|
| `pi` | the `camera` container: pictures a second, cleanup, upload waits, temperature warnings. It asks the app for this section, so you only change it in the settings. |
| `pi.detector` | the Pi's detector container: model, format (`ncnn` or `pt`), picture size, how long to wait per picture, when to stop asking a container that keeps failing |
| `detector` | YOLO inside the app itself, for a computer running it with a phone or webcam. Off on the Pi (`raspberry_pi/settings.pi.yaml`): the `detector` container does the finding |
| `supervisor` | how background jobs start again after a crash: 1 s, then doubling up to 60 s |
| `event_stream` | how many events may wait per consumer before old picture events are dropped |
| `web` | the web server: port, upload size limits, live updates, and the app page's refresh times, waits and list sizes (the page asks for them at `GET /api/ui_settings`) |
| `drawing` | the boxes and desk drawings on the live view: colors, line widths, writing sizes, how see-through |
| `notifications` | the speaker on the Pi (`output: speaker`) or the Mac's own pop-ups: on or off, which kinds (warnings, reminders, sips), how long one may take |
| `llm` | the optional AI: `ai_url` (an AI on your computer or your cloud server), the waits and limits |
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
uv run pytest -m docker                    # build the Pi's three images and start them (needs Docker and the internet)
DESKTALK_PI=pi uv run pytest -m pi -v -rs  # against the real Pi over ssh (docs/PI_CHECKS.md; every test skips without DESKTALK_PI)
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
| `tests/docker/` | builds the Pi's three images and the cloud server's, starts them and asks them questions (`-m docker`, opt-in) |
| `tests/e2e/` | the real app page in a browser, against the whole app with a fake detector and a fake AI (`-m e2e`, opt-in); `test_full_stack.py` sends pictures from the camera program through the detector container to the page (`-m docker`) |
| `tests/pi/` | the real Pi over ssh: the three containers, pictures flowing, restarts, the log files, no picture leaving (`-m pi`, opt-in). What only a person can do is in [docs/PI_CHECKS.md](docs/PI_CHECKS.md) |
| `tests/fakes/` | the fakes: `FakeClock`, `FakeDetector`, `FakeChatModel`, `SyncEventBus` ... |

The benchmark tool has its own tests: see [benchmarks/README.md](benchmarks/README.md).

---

## Developer page

A second page, for working on Desk Talk rather than using it, on a computer running `server.py`. It is switched off on the Pi (`dev.enabled: false` in `raspberry_pi/settings.pi.yaml`) ([docs/CHECKING.md](docs/CHECKING.md) says how to
check everything with it). It has five tabs:

- **Checks**: one button that runs the fast tests, the lint, the type check, the dead code check and the page's
  own tests, and every test on its own (or a whole file or folder), with the printout as it runs.
- **Live**: each object's state (present, absent), the changes as they happen, and a chart of how the
  smoothing turned the last pictures into that state, so you can see why something flickers. Click an object
  for its whole trace: when it joined the chat, left or was turned away (and why: how sure, how long, the
  sensitivity), its state changes, each with the picture taken then and its box (the same story the app
  page shows, kept in `data/trace_photos/`).
- **System**: how the Pi is doing (pictures a second, temperature, disk, its detector) and every part of the
  app (background jobs, timed jobs, the event stream, detection, the AI).
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
run, deleting a localStorage key after you say yes, and the **Demo** tab, which fires pretend warnings on
purpose after a countdown you set (for testing, or recording a demo video): they go out like real ones, to
the chat and the Mac pop-ups, and a pretend sip counts in today's water. The database is opened read-only. To switch it off, set
`dev.enabled: false` in `config/settings.local.yaml`; its other numbers are in the `dev:` section of
`config/settings.yaml`.

To work on the page itself, `npm run dev` in `dev_ui/` serves it at http://localhost:5173/dev/
(`dev.vite_port`) and changes show up as you save (the server must be running too). More in [dev_ui/README.md](dev_ui/README.md).

---

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
├── raspberry_pi/             # what runs on the Pi (raspberry_pi/README.md): compose.yaml, install.sh, update.sh, settings.pi.yaml, .env.example
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
├── docs/                     # ARCHITECTURE.md, API.md, AGENT_SYSTEM.md, PI_CHECKS.md, plans/
└── data/                     # event log, habit days, preferences, log files (made when it runs, not uploaded; on the Pi it's the app container's data folder)
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

Model files (`*.pt`) are gitignored. On a computer, `server.py` downloads the one it needs into **`models/`** the
first time it starts (or `uv run python scripts/download_models.py` does it ahead of time). On the Pi, the install
command fetches the Pi's model once and copies it into the detector's volume; the detector never downloads anything
itself.

---

## License

TBD
