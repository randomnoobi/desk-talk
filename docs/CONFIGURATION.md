# Configuration

Desk Talk reads every number, wording and rule from a file; none of them are written into the code. Every setting is
checked when Desk Talk starts (`desktalk/config/`), and so is every blank in `config/prompts.yaml`. A typo or a bad
value stops it with a message that names the file and the setting.

## The files

| File | What's in it |
|---|---|
| `config/settings.yaml` | every number: the camera, the Pi, detection, joining and leaving, the event log, the AI, Lens, the web server ... (areas below) |
| `config/agents.yaml` | who each object becomes: name, personality, color, which habits it speaks for, and which pairs are risky |
| `config/protocols.yaml` | the habit rules from *Protocols*, with the chapter each comes from |
| `config/prompts.yaml` | every word Desk Talk says or tells the AI: the prompts, Lens's lines, habit nudges, desk alerts, the replies when the AI is down. The code only fills in the blanks (`{on_waking_ml}` ...) |
| `config/safety_rules.json` | danger zones and desk corners (the Settings page writes this) |
| `config/benchmark.yaml` | what the YOLO speed test in `benchmarks/` tries |
| `raspberry_pi/settings.pi.yaml` | the Pi's own settings, laid over `config/settings.yaml`. It comes with the code, so don't edit it |
| `raspberry_pi/mine/settings.mine.yaml` | your own changes on the Pi, laid over both (such as `llm.ai_url`). Made by `install.sh` from `settings.mine.example.yaml`, kept by `update.sh`, not in git |
| `raspberry_pi/.env` | the Pi's three containers: ports, memory limits, health checks, log sizes, the install command's numbers, and `NTFY_TOPIC`. Made by `install.sh` from `.env.example`, not in git |
| `config/settings.local.yaml` | your own changes on a computer, for development (optional, not in git) |
| `.env` | on a computer only: AI keys or `OLLAMA_URL` (not in git). Nothing like it goes on the Pi |

The cloud server has its own: `cloud/settings.yaml`, `cloud/prompts.yaml` and `cloud/.env` ([cloud/README.md](../cloud/README.md)).

## Areas of `config/settings.yaml`

Every setting has a comment in plain words above it. These are the areas you're most likely to look for:

| Area | What it's for |
|---|---|
| `camera` | where the pictures come from (`pi`, `phone` or `webcam`) and how many a second |
| `pi` | the `camera` container: pictures a second, cleanup, upload waits, temperature warnings. The camera asks the app for this area, so you change it only here |
| `pi.detector` | the Pi's detector container: model, format (`ncnn` or `pt`), picture size, how long to wait per picture, when to stop asking a container that keeps failing |
| `detector` | YOLO inside the app itself, for a computer with a phone or webcam. Off on the Pi |
| `agents` | who may join the chat, the sensitivity steps, how long before joining and leaving |
| `object_states`, `presence` | when something counts as put down, taken away or moved, and when you count as at the desk |
| `clock` | when the clock is too wrong to trust ([RUNBOOK.md](RUNBOOK.md#the-clock-is-wrong-after-a-power-cut)) |
| `event_log` | how many days of events to keep |
| `llm`, `chat`, `idle_chat` | the optional AI: `ai_url`, the waits and limits, and small talk |
| `lens`, `health_checks` | when Lens speaks up, and the levels for each health check |
| `notifications` | the speaker on the Pi or the Mac's pop-ups, which kinds go out, and the ntfy server for your phone |
| `cloud` | the optional link to your cloud server |
| `web` | the web server: port, upload limits, live updates, and the app page's refresh times and list sizes (the page asks for them at `GET /api/ui_settings`) |
| `drawing` | the boxes and desk drawings on the live view |
| `supervisor` | how background jobs start again after a crash: 1 second, then doubling up to 60 |
| `logging`, `storage` | the log file in `data/logs/`, and where preferences, habit days and learned personalities are kept |
| `dev` | the developer page, on a computer only |
