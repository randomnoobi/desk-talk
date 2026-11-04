# Pi Standalone Refactor Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to carry out this plan, following "How to work" in `AGENTS.md`. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Everything runs in Docker on the Raspberry Pi, and the Pi keeps working with the computer switched off. A computer is only one way to open the app's page.

**Architecture:** Three containers from one compose file: `camera`, `detector` and a new `app` that runs the existing `desktalk` code (`server.py`). The camera keeps uploading to `/api/pi/frame`, but now over Docker's private network to `app`, so most of the code moves without being rewritten. Any computer or phone on the Wi-Fi opens the page at `http://<pi>:5000`. With no Wi-Fi and no computer, the Pi still watches the desk and speaks its warnings and reminders through a speaker. No AI runs on the Pi. Chat AI on a computer or the cloud is an optional extra.

**Tech Stack:** Docker Compose, Python 3.12 (`app`, `detector`), Debian's Python with apt's `picamera2` (`camera`), ncnn YOLO, SQLite, FastAPI, an offline text-to-speech program (Piper unless Task 2 rules it out).

**Spec:** the rules below, from the 2026-10-30 chat. There is no separate design document.

**When:** after the cloud server. Start from `main` once PR #11 and the cloud server work are merged.

## The rules this plan carries out

1. Everything on the Pi runs inside a Docker container.
2. All processing happens on the Pi, and the Pi runs without the computer.
3. Settings are tuned so everything runs on the Pi by itself.
4. Everything gets tested on a real Pi.
5. Non-sensitive means events and state only, no camera pictures.
6. With no Wi-Fi and no computer, the Pi works by itself and gives voice notifications through a speaker, with no AI at all.
7. Chat AI may run on the user's computer or in the cloud. It is optional, and the Pi runs without it.
8. Real-Pi work is done by the agent over SSH.

## Global Constraints

- Follow `AGENTS.md`: settings in `config/settings.yaml`, wording in `config/prompts.yaml`, parts built in `desktalk/bootstrap.py`, test first, plain words.
- `camera` and `detector` still never import `desktalk/`. `desktalk_shared` stays standard library only and works on Python 3.11, because the `camera` image uses apt's Python.
- Pictures never leave the Pi, except to a page someone opens on their own Wi-Fi. Nothing in this plan sends anything to the cloud server.
- `uv run python server.py` keeps working on a computer for development, because the tests and the phone and webcam cameras use it. It is no longer how people run the app.
- A Pi task is only done when its real-Pi check has passed and its output is pasted into the PR.

## Old rules that change

| Where | Old rule | New rule |
|---|---|---|
| `AGENTS.md`, Raspberry Pi | Two things run on the Pi: a camera program on the Pi's own Python and a detector in Docker | Three containers: `camera`, `detector`, `app`. Nothing runs outside Docker |
| `AGENTS.md`, Raspberry Pi | "You can't run anything on the Pi, so don't say a Pi change works" | Agents run things on the Pi over SSH, and a Pi change isn't finished until the real-Pi checks pass |
| `AGENTS.md`, Tests | Tests never need a Pi | Same for normal tests. New opt-in `pi` tests are the exception, like `slow`, `docker` and `e2e` |
| `AGENTS.md`, Config | The camera program reads the `pi:` section from the computer | It reads it from the `app` container |
| `AGENTS.md`, Raspberry Pi | Code gets to the Pi with `git pull` or scp | `git pull`, then `docker compose up -d --build` |
| `AGENTS.md`, `README.md` | An AI is part of the app | No AI runs on the Pi. The app must work fully with none set up. An AI on a computer or the cloud only adds chat and nicer wording |
| `raspberry_pi/detector_service/.env` | "API keys never go on the Pi" | **Stays**, pending the open question in "Decisions still needed" |
| `README.md`, `docs/ARCHITECTURE.md` | "Only YOLO moved to the Pi", and the computer does the rest and is the detector's backup | All of it runs on the Pi, with no backup detector |
| `raspberry_pi/README.md`, `camera/README.md` | apt packages, systemd, `--server` and `--key` by hand | One install command |
| `config/settings.yaml` | Comments such as "the picture goes to this computer to detect instead" | Say what happens on the Pi |

Rules that stay: `camera` and `detector` never import `desktalk/`, parts talk through `ports.py`, `Clock` for the time, config not code.

Don't sweep for the word "computer": `config/prompts.yaml:410` and the Computer character mean the laptop on the desk.

## What changes for the person using it

- **The speaker replaces Mac pop-ups**, because `osascript` can't run on the Pi. Warnings, reminders and sips the camera saw are spoken, using the same on/off settings and quiet hours as the pop-ups.
- **With no AI:** warnings and reminders use their ready-made words (`alert_voice.py` and `nudge_voice.py` already fall back to them). Small talk, chat replies, the daily recap's AI wording and the AI picture check for lost items don't run. `bootstrap.py` only sends pictures to a local AI, so `vision` stays `None` on the Pi.
- **No backup detector.** While `detector` is down, the live view keeps going and the app records no detections.
- **Picture speed:** `benchmarks/results/README.md` shows the detector alone reaching 3.0 to 3.8 pictures a second on the Pi with three models. The current setting (`yolo26m-objv1-150` at 640) isn't one of them. Those runs had only the detector working, so Task 10 measures the whole stack. The README's "about one picture a second" is out of date.
- **The developer page is off on the Pi.** `dev_only` only accepts requests from the machine running the server.

## Decisions made

- **AI keys stay in the cloud server**, so the "no API keys on the Pi" rule stays. The Pi's `OpenAICompatibleChatModel` is pointed at an optional address: the cloud server's `POST /v1/chat/completions`, using the Pi's token, or Ollama on the user's computer.
- **A USB speaker** and one default offline voice for everything. More voices can come in a later version.
- **SSH:** agents reach the Pi with `ssh pi`, using a key login set up by the user. Agents never type the Pi's password.
- **Cloud link:** the sender from the cloud server work (`docs/superpowers/specs/2026-10-30-cloud-server-design.md`) already lives in `desktalk`, so it moves to the Pi with `app`. Task 8's `install.sh` also takes `--cloud-url` and `--code` to link the Pi to its owner's account.

## Left for later

- **Clock drift.** A Pi with no internet and no clock battery can have the wrong time after a power cut, which throws off reminders, quiet hours and day totals. Ignored for now by the user's choice. Add a clock battery or a fix when this gets polished. No task in this plan handles it.

## Review Focus

1. **Time zone:** a container defaults to UTC, so nudges, quiet hours and day folders would all be off by hours. `clock.local_hour` must match the user's time. Pinned in Task 5.
2. **No Wi-Fi at boot:** the Pi has to start everything and keep working with no network at all. Pinned in Task 9. Wrong clock times are out of scope (see "Left for later").
3. **Power cut while writing:** pulling the plug must leave the event log and SQLite readable, and the app must start again by itself. Pinned in Task 9.
4. **One container restarting:** with `app` restarting, `camera` keeps retrying and catches up without its own restart. With `detector` restarting, `app` keeps its live view. Pinned in Task 9.
5. **An AI that has gone away:** the user's laptop sleeps, so its AI stops answering. Today a reminder could wait out `llm.timeout_sec` and its retries before using its ready-made words, so a spoken warning would come far too late. Warnings never wait for an AI, and a reminder waits only briefly. Pinned in Task 7.

---

### Task 1: Change the rules

**Files:**
- Modify: `AGENTS.md`, `CLAUDE.md` if needed

**Interfaces:**
- Produces: the rules every later task follows.

- [ ] **Step 1:** Rewrite the "Raspberry Pi" section of `AGENTS.md` and the other lines in "Old rules that change". Add the three containers, the `pi` tests and the real-Pi rule. Mark the section "being moved to this; see this plan" until Task 11.
- [ ] **Step 2:** Add this plan to the history line at the top of `AGENTS.md`.
- [ ] **Step 3:** Commit: "Change the Pi rules so everything runs in Docker on the Pi".

### Task 2: Prove the risky parts on a real Pi first

No product code. This is the first time any of this runs on a Pi, so stop here if a part can't work.

**Files:**
- Create: `raspberry_pi/camera/Dockerfile`
- Create: `docs/plans/2026-10-30-pi-first-run-notes.md`

**Interfaces:**
- Produces: the `camera` image and the facts Tasks 3 to 10 rely on.

- [ ] **Step 1:** Write the `camera` image: `debian:bookworm-slim`, Raspberry Pi's apt source, `python3-picamera2`, `python3-opencv`, `python3-yaml`.
- [ ] **Step 2 (on the Pi):** Build it and run `--self-test` with the devices it needs (`/dev/video*`, `/dev/media*`, `/dev/vchiq`, and `/run/udev` read-only). Expected: "took a picture", with a lux value.
- [ ] **Step 3 (on the Pi):** Build and start `detector` with `docker compose up -d --build`. Expected: `/health` answers and `/detect` finds objects in a real picture.
- [ ] **Step 4 (on the Pi):** Run the three models picked in `benchmarks/results/README.md` through `detector`, and record the speed, memory and temperature.
- [ ] **Step 5 (on the Pi):** Play a sentence through the speaker from inside a container, using Piper with `/dev/snd`. Record how long a sentence takes to start speaking while `detector` is busy. Expected: a clear voice, starting in under 2 seconds. If it's slower, try a smaller voice or `espeak-ng`.
- [ ] **Step 6:** Write the Pi model, its memory, disk type and the results in the notes file. **If the camera can't work inside a container, stop and ask the user**, since rule 1 has no fallback.
- [ ] **Step 7:** Commit: "Add the camera's Docker image and the first real Pi results".

### Task 3: Run the camera program in its container

**Files:**
- Modify: `raspberry_pi/camera/cli.py`, `raspberry_pi/camera/settings.py`
- Delete: `raspberry_pi/camera/desk-talk-camera.service`
- Test: `tests/unit/test_camera_program_cli.py`

**Interfaces:**
- Produces: `--key-file <path>`, and `DESKTALK_SERVER` and `DESKTALK_PAIR_KEY_FILE` as stand-ins for `--server` and `--key`.

- [ ] **Step 1:** Write failing tests. `test_key_comes_from_the_key_file`: the key equals the file's contents without the line ending. `test_flag_beats_the_environment`: `--server` wins over `DESKTALK_SERVER`. `test_missing_key_file_says_so`: the error names the path.
- [ ] **Step 2:** Run `uv run pytest tests/unit/test_camera_program_cli.py`. Expected: the three new tests FAIL.
- [ ] **Step 3:** Add the flag and the environment names in `cli.py`.
- [ ] **Step 4:** Run the same tests. Expected: PASS.
- [ ] **Step 5:** Delete the systemd file. The watchdog's exit now relies on Docker's `restart: unless-stopped`, so update its comment and `pi.restart_after_missed_pictures`' comment.
- [ ] **Step 6:** Commit: "Let the camera program run in Docker without systemd".

### Task 4: Let the app run without PyTorch or YOLO

**Files:**
- Modify: `pyproject.toml`, `uv.lock`, `desktalk/bootstrap.py` (`make_detector`), `desktalk/config/app.py` (`DetectorSettings`), `config/settings.yaml`, `config/prompts.yaml`
- Create: `desktalk/adapters/detector/no_detector.py`
- Test: `tests/contracts/detector_contract.py` (a new test class), `tests/unit/test_core_producer.py`, `tests/unit/test_app_without_yolo.py`

**Interfaces:**
- Produces: `detector.enabled: bool` (default `true`). `NoDetector.detect(picture)` raises `DetectorUnavailable`, which `DetectionProducer` already catches at `producer.py:172`. Install extra `desktalk[computer-detector]` holds `ultralytics`, `torch` and `torchvision`.

- [ ] **Step 1:** Write failing tests. `test_builds_without_ultralytics_or_torch` runs a subprocess that blocks both imports and calls `build()` with `detector.enabled: false`; expected: `app.detector` is a `NoDetector`. `test_off_picture_gets_live_view_and_no_event` uses `NoDetector` in the producer; expected: the live view updates and no `DetectionEvent` is published.
- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3:** Add `NoDetector`, the setting and the extra. `make_detector` returns `NoDetector` when off. Change Lens's detector-off wording in `config/prompts.yaml` so it no longer says the computer is doing the detecting.
- [ ] **Step 4:** Run the tests, the detector contract suite and `uv sync --all-packages`. Expected: PASS, and the computer's `uv run python server.py` still gets YOLO.
- [ ] **Step 5:** Commit: "Let the app run with no detector of its own".

### Task 5: The app's container and one compose file

**Files:**
- Create: `raspberry_pi/app/Dockerfile`, `raspberry_pi/compose.yaml`, `raspberry_pi/.env.example`, `raspberry_pi/settings.pi.yaml`
- Modify: `raspberry_pi/detector_service/compose.yaml` (removed), `raspberry_pi/detector_service/.env` (moved), `tests/docker/test_detector_service_image.py`
- Test: `tests/docker/test_pi_stack.py`

**Interfaces:**
- Consumes: Task 3's names, Task 4's `detector.enabled`.
- Produces: services `camera`, `detector` and `app`. `app` listens on port 5000. `detector` is reachable only at `http://detector:8100`. Data lives in `~/desk-talk/data`, bind-mounted so it can be backed up or copied with scp.

- [ ] **Step 1:** Write failing Docker tests. `test_stack_starts_healthy` starts all three with `camera=folder:<test pictures>`; expected: all healthy and `/api/live` shows pictures arriving. `test_app_uses_the_users_time_zone` sets `TZ=America/New_York`; expected: the app's `clock.local_hour` matches that zone. `test_detector_port_is_not_open_outside`: expected: port 8100 refuses from the host.
- [ ] **Step 2:** Run `uv run pytest -m docker tests/docker/test_pi_stack.py`. Expected: FAIL.
- [ ] **Step 3:** Write the image and compose file. `settings.pi.yaml` is mounted read-only as `config/settings.local.yaml`, the file `load_config` already lays over `settings.yaml`. It sets `detector.enabled: false`, `dev.enabled: false`, `pi.detector.url` and `pi.photo_dir`, and sets no AI. Share the pairing key through a volume: `app` writes it and `camera` reads it. Each service has `restart: unless-stopped`, a memory limit and small log files, with all of these numbers in `raspberry_pi/.env`.
- [ ] **Step 4:** Run the tests. Expected: PASS.
- [ ] **Step 5 (on the Pi):** Run `docker compose up -d --build` with the computer off, and open `http://<pi>:5000` from a phone. Expected: live view, chat page and event log all work.
- [ ] **Step 6:** Commit: "Run the camera, detector and app together in Docker on the Pi".

### Task 6: Fix what assumes it's on a computer

**Files:**
- Modify: `server.py`, `desktalk/web/pairing.py`, `desktalk/domain/events.py` (`DetectionEvent.detected_on` default and the `ts` comment), `desktalk/config/app.py`, `config/settings.yaml`
- Test: `tests/unit/test_server_startup_lines.py`

**Interfaces:**
- Produces: `web.public_address: str` (empty = work it out with `lan_ip()`). Inside a container `lan_ip()` finds Docker's private address, so `settings.pi.yaml` sets `desktalk.local:5000`.

- [ ] **Step 1:** Write failing tests. `test_startup_shows_the_public_address`: expected: the line has `desktalk.local:5000`. `test_startup_in_docker_skips_the_pi_command`: expected: no line telling you to run `python3 -m raspberry_pi.camera`.
- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3:** Add the setting and change `settings_lines` and the links. A filled-in `web.public_address` also means the Pi command isn't shown. `detected_on` keeps its values, since old log files use them.
- [ ] **Step 4:** Run `uv run pytest tests/unit/test_server_startup_lines.py`. Expected: PASS.
- [ ] **Step 5:** Run `uv run pytest -m e2e` with `web_app/` served from a different address than localhost. Expected: PASS.
- [ ] **Step 6:** Commit: "Show the Pi's own address when the app runs in Docker".

### Task 7: Voice through a speaker, and AI as an optional extra

**Files:**
- Create: `desktalk/adapters/notify/speaker.py`
- Modify: `desktalk/adapters/notify/mac.py` (`make_notifications`), `desktalk/adapters/notify/__init__.py`, `desktalk/config/app.py` (`NotificationsSettings`, `LlmSettings`), `desktalk/core/chat/alert_voice.py`, `desktalk/core/chat/nudge_voice.py`, `config/settings.yaml`, `raspberry_pi/settings.pi.yaml`, `raspberry_pi/app/Dockerfile`, `raspberry_pi/compose.yaml`, `.env.example`
- Test: `tests/contracts/notifier_contract.py` (a new test class), `tests/unit/test_adapters_notify_speaker.py`, `tests/integration/test_app_with_no_ai.py`

**Interfaces:**
- Consumes: `DesktopNotifications.show(title, text)` in `ports.py`, which `DesktopAlerts` already calls for warnings, reminders and sips. No new interface, and the speaker plugs in where pop-ups do.
- Produces: `SpeakerNotifications(settings, queue_size, run_command)`. `notifications.output: mac | speaker | none`, plus `notifications.speak_command` and `notifications.voice_model`. `llm.ai_url: str` (empty = no AI) and `llm.reminder_wait_sec: float`. When `llm.ai_url` is the cloud server's address, requests carry the Pi's cloud token.

- [ ] **Step 1:** Write failing tests. `test_show_never_waits_or_raises`: a stuck speak command doesn't hold up `show`. `test_says_one_thing_at_a_time`: two `show` calls run the command one after the other. `test_full_queue_drops_and_logs`. `test_action_tags_and_emoji_are_not_read_out`. `test_no_sound_device_is_only_logged`.
- [ ] **Step 2:** Write failing `test_app_with_no_ai.py` tests. `test_warning_and_reminder_speak_with_no_ai`: both reach the fake speaker with their ready-made words. `test_no_ai_makes_no_network_calls`. `test_warning_does_not_wait_for_a_missing_ai`: the words go out at the fake clock's same moment. `test_reminder_waits_at_most_reminder_wait_sec` with an AI that never answers. `test_quiet_hours_stay_silent`.
- [ ] **Step 3:** Run them. Expected: FAIL.
- [ ] **Step 4:** Add `SpeakerNotifications` (same queue and background thread as `MacNotifications`) and the settings, and have `make_notifications` choose by `notifications.output`. Add Piper and its voice to the `app` image, and `/dev/snd` to compose. Set `output: speaker`, `chat.ai_writes_warnings: false` and a short `llm.reminder_wait_sec` in `settings.pi.yaml`.
- [ ] **Step 5:** Run them. Expected: PASS. Then run the existing `DesktopAlerts` and message generator tests, and check that a computer still gets Mac pop-ups.
- [ ] **Step 6 (on the Pi):** With Wi-Fi unplugged, put a cup next to the laptop and wait for a reminder. Expected: both are spoken, and nothing tries to reach an AI.
- [ ] **Step 7 (on the Pi):** Set `llm.ai_url` to Ollama on the computer, chat from the page, then close the laptop. Expected: chat works, and spoken warnings carry on with no gap.
- [ ] **Step 8:** Commit: "Speak warnings and reminders through the Pi's speaker, with AI optional".

### Task 8: One install command

**Files:**
- Create: `raspberry_pi/install.sh`, `raspberry_pi/update.sh`
- Test: `tests/unit/test_pi_install_script.py`

**Interfaces:**
- Produces: `install.sh` is the setup script the cloud server's website gives people. It takes `--tz <zone>`, and optional `--cloud-url` and `--code`. It needs no AI or cloud, and is safe to run twice.

- [ ] **Step 1:** Write failing tests that run the script in dry-run mode. `test_refuses_a_32_bit_pi`: expected: exit 1 and a message saying so. `test_running_twice_keeps_data_and_key`: expected: `data/` and the pairing key are unchanged.
- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3:** Write the script: check `arm64`, free disk and memory, install Docker if missing, write `.env` (the time zone from the Pi), download the chosen model, set the hostname to `desktalk`, enable the camera, and run `docker compose up -d --build`.
- [ ] **Step 4:** Run the tests. Expected: PASS.
- [ ] **Step 5 (on the Pi):** Run it on a freshly flashed SD card. Expected: the page opens and the Pi still works after a reboot.
- [ ] **Step 6:** Commit: "Add one command that installs Desk Talk on the Pi".

### Task 9: Real Pi tests

**Files:**
- Create: `tests/pi/test_real_pi.py`, `tests/pi/conftest.py`, `docs/PI_CHECKS.md`
- Modify: `pyproject.toml` (the `pi` marker), `.github/workflows/tests.yml` (skip them), `docs/CHECKING.md`

**Interfaces:**
- Produces: `DESKTALK_PI=<host> uv run pytest -m pi`, run by the agent over SSH. `docs/PI_CHECKS.md` lists what only a person can do: put a cup on the desk, cut the power, listen to the speaker.

- [ ] **Step 1:** Write the tests. Each asserts over HTTP and with Docker over SSH:
  - `test_works_with_nothing_else_on`: a new `object_state` event appears for a real object.
  - `test_starts_after_power_cut`: all healthy, the event log and SQLite are readable, and the states are restored.
  - `test_app_restart_needs_no_camera_restart`: the camera's container start time doesn't change, and pictures come back within `pi.upload_retry_max_sec` plus the app's start time.
  - `test_detector_restart_keeps_live_view`: pictures keep arriving, a warning shows, and events come back afterwards.
  - `test_no_wifi_at_boot`: all containers healthy, with pictures and events flowing.
  - `test_runs_with_no_network_at_all`: with Wi-Fi off and no AI, `app` keeps recording events, and the speak command ran. The check reads its results over a cable or once Wi-Fi is back.
  - `test_times_match_local_time`: the newest event's local hour equals the Pi's.
  - `test_disk_stays_under_limit`: stored pictures stop growing past one `pi.delete_every_min` cycle, and free space stays above `pi.min_free_mb`.
  - `test_no_picture_goes_off_the_pi`: during a run with no AI set, `app` and `camera` open no connection outside the Pi, except to a page someone opened on the Wi-Fi.
- [ ] **Step 2:** Run them on the Pi with `uv run pytest -m pi`. Fix each failure in the task that owns the code, and paste the results in the PR.
- [ ] **Step 3:** Commit: "Add tests that run against a real Pi".

### Task 10: Tune the settings on the real Pi

Task 2 gives the first numbers. Starting targets, which are my suggestion and can be changed:

| What | Target |
|---|---|
| A cup put down until `object_state` appears | Under 5 seconds |
| A sip counted | Same as on the computer in 10 of 10 tries |
| Processor temperature, an hour of steady use | Stays under `pi.hot_warning_c` (75 °C), with no throttle flags in `health.py` |
| Memory | No container restarted for memory in 24 hours, and the Pi doesn't swap |
| Page | Live view and chat stay usable while detecting |
| Voice | A warning starts speaking within 3 seconds of being raised, while detecting |

**Files:**
- Modify: `raspberry_pi/settings.pi.yaml`, `raspberry_pi/.env.example`, `docs/plans/2026-10-30-pi-first-run-notes.md`
- Test: `tests/unit/test_pi_settings_file.py`

- [ ] **Step 1:** Write the failing test `test_pi_settings_load`: `settings.pi.yaml` loads over `settings.yaml` with no "no longer used" warnings, and `camera.fps == pi.fps`.
- [ ] **Step 2:** Run it, set both `fps` values, and run it again. Expected: FAIL, then PASS.
- [ ] **Step 3 (on the Pi):** Try `pi.detector.model`, `image_size`, `pi.fps`, picture size and `jpeg_quality`, changing one at a time and recording the table above for each.
- [ ] **Step 4 (on the Pi):** Check that `object_states` waits, habit rules and sips still work at the chosen speed. Change them in `settings.pi.yaml` only.
- [ ] **Step 5 (on the Pi):** Check how much the app writes to disk. If the SD card is the Pi's disk, move `pi.photo_dir` to memory (tmpfs) and raise `event_log.compact_every_min` if needed.
- [ ] **Step 6 (on the Pi):** Run for 24 hours, then run `uv run pytest -m pi` again. Expected: the table's targets met and all tests pass.
- [ ] **Step 7:** Commit: "Tune the settings for running everything on the Pi".

### Task 11: Bring the docs up to date and finish

**Files:**
- Modify: `AGENTS.md`, `README.md`, `docs/ARCHITECTURE.md`, `docs/API.md`, `docs/CHECKING.md`, `docs/PI_TRANSFER.md`, `raspberry_pi/README.md`, `raspberry_pi/camera/README.md`, `raspberry_pi/detector_service/README.md`, comments in `config/settings.yaml`

- [ ] **Step 1:** Update the drawings and steps. Remove every "Not tried on a real Pi yet" note, as Task 9 has now passed. Take the "being moved" mark off `AGENTS.md`.
- [ ] **Step 2:** Change the comments in `config/settings.yaml` that mean "the computer running the app". Keep the ones about the Computer character.
- [ ] **Step 3:** Run all checks and show the output: `uv run pytest -m "not slow"`, `uv run pytest -m slow`, `uv run pytest -m docker`, `uv run pytest -m e2e`, `uv run pytest -m "not slow and not docker and not e2e" --cov`, `uv run ruff check <changed files>`, `uv run pyright`, `uv run vulture`.
- [ ] **Step 4:** Open the PR, then have a subagent review it with `/code-review high <PR number>`, and another fix what it finds.
