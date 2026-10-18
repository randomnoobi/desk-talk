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
Raspberry Pi camera                         Your computer
┌───────────────────────────┐   pictures   ┌──────────────────────────────────────────────┐
│ 3 pictures a second       │ ───────────▶ │ YOLO detects objects → one event per picture  │
│ saved on the Pi           │    Wi-Fi     │ event stream → event log (data/events/)       │
│ deleted every 10 minutes  │              │   → agents join / leave the chat              │
└───────────────────────────┘              │   → safety alerts, habit reminders            │
                                           │ app at http://localhost:5000                  │
                                           └──────────────────────────────────────────────┘
```

- **The Pi** (`pi/camera_node.py`) takes the pictures, keeps them on its own storage and deletes them every 10 minutes.
- **Your computer** (`python server.py`) runs the object detector and turns every picture into a
  detection event. Events flow through an **event stream** to separate consumers (decoupled, so a
  slow AI reply never holds up the camera), and are saved in the **event log**. Every 10 minutes
  the log is compacted: per-picture events become "the cup was on the desk from 2:02 to 2:31" intervals.
- A phone (over Wi-Fi) or a webcam can still be the camera instead: `camera.source` in `config/settings.yaml`.

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
It doesn't use the AI.

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
| Speed | 3 pictures a second; on an Apple-chip Mac it runs on the GPU (`device: auto`). Too slow? `yolo26s-objv1-150.pt`. |
| Joining | seen 3 s → joins; gone 45 s → leaves (habit rules use a shorter 1 s / 2.5 s) |
| Safety | danger zones, desk edges, chain reactions, objects approaching (`desktalk/vision/relationships.py`, tuned in `config/safety_rules.json` and the Settings page) |

```bash
python scripts/download_models.py --test    # download the model and time it on your computer
python scripts/download_models.py --list    # every object it can detect
```

---

## Getting started

### On your computer

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env              # add an AI key, or OLLAMA_URL for Ollama
python scripts/download_models.py # the YOLO model (about 45 MB)
python server.py
```

Open **http://localhost:5000**. `server.py` prints the command to run on the Pi.

### On the Raspberry Pi

See [pi/README.md](pi/README.md). In short:

```bash
sudo apt install -y git python3-picamera2 python3-opencv python3-yaml
git clone https://github.com/randomnoobi/desk-talk.git && cd desk-talk
python3 pi/camera_node.py --server http://<your computer>:5000 --key <key>
```

### Demo without a server

Open the GitHub Pages site, or serve `app/` with any static server. The app plays a short
demo where four characters join the chat.

---

## Configuration

| File | What's in it |
|---|---|
| `config/settings.yaml` | every number: camera source and rate, Pi cleanup, model, join/leave times, event log, AI limits, idle chat, Lens |
| `config/agents.yaml` | who each object becomes: name, personality, color, which habits it speaks for |
| `config/protocols.yaml` | the habit rules from *Protocols* and your day (wake / bed) |
| `config/safety_rules.json` | danger zones and desk corners (the Settings page writes this) |
| `config/settings.local.yaml` | your own overrides (optional, not uploaded) |
| `.env` | AI keys / `OLLAMA_URL` (never uploaded) |

---

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest
```

Unit tests (`tests/unit/`) check each piece on its own; integration tests (`tests/integration/`)
run them together: scripted pictures through the whole pipeline, the web API with a fake AI,
the Pi node streaming to a real server, and the real YOLO model on sample photos.

---

## Project structure

```
desk-talk/
├── server.py                 # start the app on your computer
├── main.py                   # standalone safety pipeline (CLI, docs/SYSTEM_LOGIC.md)
├── config/                   # settings.yaml, agents.yaml, protocols.yaml, safety_rules.json
├── desktalk/                 # the app (Python package)
│   ├── app.py                #   builds everything and starts it
│   ├── settings.py           #   reads config/ and .env
│   ├── events/               #   event stream + event log
│   ├── vision/               #   camera sources, YOLO, producer, desk geometry, Lens
│   ├── agents/               #   profiles + roster (join / leave)
│   ├── habits/               #   Protocols rules, habit tracker, reminders
│   ├── chat/                 #   the AI: replies, alerts, idle chat, lost items, recap
│   └── web/                  #   Flask routes (app, Pi, phone)
├── pi/                       # runs on the Raspberry Pi
├── app/                      # the computer app (HTML / CSS / JS)
├── phone/                    # phone camera page
├── pipeline/                 # the standalone pipeline's modules
├── scripts/                  # download_models.py, camera preview tools
├── tests/                    # unit + integration tests
├── website/                  # project website (GitHub Pages)
├── weights/                  # YOLO model files (not uploaded)
└── data/                     # event log, habit days (not uploaded)
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

## GitHub Pages (portfolio site + app demo)

Pushing to **`main`** runs [`.github/workflows/deploy-pages.yml`](.github/workflows/deploy-pages.yml), which publishes the **`app/`** UI at the site root and **`website/`** under **`/website/`**, with links to switch between them.

1. Create a new repository on GitHub and push this project (`main`).
2. On GitHub: **Settings → Pages → Build and deployment → Source: GitHub Actions**.
3. Open the **Actions** tab and confirm the **Deploy site to GitHub Pages** workflow succeeds.
4. Your site URL will be `https://<username>.github.io/<repo>/` (unless you use a custom domain).

Model weights (`*.pt`) are gitignored. `python scripts/download_models.py` puts them in **`weights/`**.

---

## License

TBD
