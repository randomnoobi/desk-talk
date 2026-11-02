# Desk Talk: notes for coding agents

What the app does and how it fits together: [README.md](README.md), [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
How it got this way (history): the refactor, [docs/plans/2026-10-21-refactor-plan.md](docs/plans/2026-10-21-refactor-plan.md), and the cleanup after it, [docs/plans/2026-10-23-codebase-cleanup-plan.md](docs/plans/2026-10-23-codebase-cleanup-plan.md), both finished. Not finished yet: [docs/plans/2026-10-30-pi-standalone-refactor-plan.md](docs/plans/2026-10-30-pi-standalone-refactor-plan.md), which moves everything onto the Pi.

## How to work
- **Before making a plan:** read the code the change will touch and find where the new pieces should plug in. Name those files and functions in the plan.
- **When carrying out a plan:** split the work into pieces that don't depend on each other and give each piece to its own subagent, all started at once. Use as many subagents as the plan allows, but never let two of them edit the same file.
- **Test first:** write a failing unit test, then the code that makes it pass. While working, run only the tests for the code you changed (e.g. `python -m pytest tests/test_<name>.py`). Run the full fast suite, plus the `slow` tests if you changed detection, once at the very end before opening the PR.
- **When the code is done:** open a PR, then start a subagent to review it with `/code-review high <PR number>`.
- **After the review:** start another subagent to fix what the review found, and give it the review comments. Then re-run the tests and push the fixes to the same PR.

## Check your work before saying it's done
- Packages come from uv (`uv sync --all-packages`; never pip). Run things with `uv run ...`.
- Fast tests: `uv run pytest -m "not slow"`. The `slow` tests run the real YOLO model; run them only when you change detection. `docker` tests build the Pi's images; run them only when you change them. `uv run pytest -m e2e` drives the real page in a browser (`uv run playwright install chromium` once); run it when you change `web_app/`, `phone_camera/` or a route they use. `DESKTALK_PI=pi uv run pytest -m pi -v -rs` runs against the real Pi over `ssh pi`; run it when you change `raspberry_pi/`, or anything that runs in the Pi's containers. Without `DESKTALK_PI` every test skips. If the compose file isn't in `~/desk-talk/raspberry_pi`, also set `DESKTALK_PI_DIR='~/your-folder/raspberry_pi'` (quoted, so `~` is the Pi's home). What only a person can do is in [docs/PI_CHECKS.md](docs/PI_CHECKS.md).
- Coverage: `uv run pytest -m "not slow and not docker and not e2e" --cov` fails below `fail_under` in `pyproject.toml`. It may only go up.
- Lint: `uv run ruff check <files you changed>`. Old files may still have ruff errors, so don't fix files you didn't touch. Just don't add new errors. Don't run `ruff format`.
- Types: `uv run pyright` must show 0 errors. `pyrightconfig.json`'s `ignore` list only shrinks: fix a file and take it off; never add one.
- Dead code: `uv run vulture` must print nothing. A name it can't see being used goes in `tests/vulture_allow.py`, with what uses it.
- The developer page (`dev_ui/`, TypeScript): run `npm test` and `npm run build` in `dev_ui/` when you change it. It keeps no numbers of its own: they come from `GET /api/dev/settings`. A new `/api/dev` route needs a characterization case like any other route.
- The cloud server (`cloud/`): its fast tests are in the fast suite. When you change `cloud/server/`, `cloud/settings.yaml`, `cloud/prompts.yaml`, `cloud/compose.yaml` or `cloud/web/nginx*.conf`, also run `uv run pytest -m docker tests/docker/test_cloud_stack.py tests/docker/test_cloud_postgres_store.py`. When you change `cloud/web/`, run `npm test` and `npm run build` there, and `uv run pytest -m e2e tests/e2e/test_cloud_site.py`.
- Show the test, ruff, pyright and vulture output. Don't just say they passed.

## Config, not code
- IMPORTANT: Never hardcode a number, timing, threshold, path or model name. Add it to `config/settings.yaml`, with a plain-language comment, and a typed field in `desktalk/config/app.py`; parts get their typed section passed in (`config.camera.fps`, etc.). The Pi's numbers go in the `pi:` section (the camera program reads it from the `app` container); the containers' own numbers (ports, memory limits, health checks) in `raspberry_pi/.env` (and `.env.example`).
- User-facing wording goes in `config/prompts.yaml` (code only fills in the blanks). A new blank also goes in `BLANKS` in `desktalk/config/prompts.py`.
- The web pages keep no copies of settings: `web_app/` reads `GET /api/ui_settings`, `phone_camera/` reads `GET /api/phone/config`.
- Habit rules go in `config/protocols.yaml`, characters in `config/agents.yaml`, and API keys in `.env` (also add new keys to `.env.example`). Never put keys in YAML.
- `config/settings.local.yaml` is the user's own file and isn't in git. Don't edit it.

## Interfaces first
- Parts talk through the interfaces in `desktalk/ports.py` and pass the plain data in `desktalk/domain/`. A part asks for only the interfaces it uses in its constructor. Never pass the whole `app` object.
- `desktalk/bootstrap.py` is the only place that knows the real classes: it builds every part and connects them. A new part gets built there; everything else only sees interfaces. (The camera program's `workers.py` (`build_program`) and the detector service's `bootstrap.py` do the same on the Pi.)
- New routes go in `desktalk/web/routers/` (one module per area) and need a characterization case in `tests/characterization/cases.py`; `test_route_coverage.py` fails without one.
- Ask a `Clock` for the time instead of calling `time.time()` or `time.localtime()` (`clock.local_hour(now)`, `clock.local_day(now)`), so tests can move time by hand.
- `desktalk/core/` and `desktalk/domain/` never import OpenCV, numpy, web or AI libraries: pictures go through `PictureTools`. `desktalk/web/` imports only interfaces, plain data, settings and the app. `tests/unit/test_layers.py` checks both.
- Background jobs are `PeriodicWorker`s run by the Supervisor, so one failure never switches a feature off for good.
- Use `logging`, never `print` (ruff rule T20). Scripts in `scripts/` may print.
- Changing `ports.py`, `desktalk/domain/` or `shared/desktalk_shared/` changes the contract every part builds on. Do it on purpose, and update the fakes and contract tests with it.

## Tests
- Tests must never need the network, an API key, a camera, a Pi, Docker or a browser (the opt-in `slow`, `docker`, `e2e` and `pi` tests aside). Use the fakes in `tests/fakes/` (`FakeClock`, `FakeDetector`, `FakeChatModel`, `SyncEventBus` ...).
- Every interface has a contract suite in `tests/contracts/`. A new real version of an interface gets a test class that runs the same suite.
- `tests/` is a package: import helpers as `from tests.fakes import ...` / `from tests.conftest import ...`. Test file names must be unique.
- Write the failing test first, watch it fail, then write the code (superpowers test-driven-development skill).

## Raspberry Pi
Being moved to this; see [the Pi standalone plan](docs/plans/2026-10-30-pi-standalone-refactor-plan.md). Until its last task is done, parts of the code and docs still show the old setup (a camera program outside Docker, the computer doing the rest).
- Everything on the Pi runs in Docker, three containers from one compose file: **`camera`** (`raspberry_pi/camera/`), **`detector`** (`raspberry_pi/detector_service/`) and **`app`**, which runs the `desktalk` code. Nothing runs outside Docker.
- `camera` and `detector` never import `desktalk/`. `camera` uses Debian's Python 3 (3.11 or newer) with apt's `picamera2`, `cv2` and `yaml`, so it is standard library plus those, plus `desktalk_shared`. Import the apt packages inside the function that uses them. `detector` may use anything uv installs, but imports only `desktalk_shared`.
- `shared/desktalk_shared/` is used on the Pi and on this computer. It must stay standard library only and work on Python 3.11.
- The Pi works by itself, with no computer and no AI: nothing on the Pi may need either. A computer or the cloud may add chat AI, but it's optional. No AI runs on the Pi, and API keys never go on the Pi; AI keys stay on the cloud server.
- Only events, state and goals may leave the Pi. No pictures, no to-do text. There are two exceptions, both text only and both off until the user sets `llm.ai_url`:
  - An AI on the user's own computer (Ollama) gets chat and the words the characters say by themselves (reminders, small talk, the recap), so to-do text may go to it.
  - Chat the user has turned on may pass through their cloud server, never stored or logged there. Reminders, small talk and the recap don't go through the cloud server.
- Code gets to the Pi with `bash raspberry_pi/update.sh` on the Pi, which runs `git pull` and then rebuilds with `docker compose up -d --build`. A new Pi gets it with `bash raspberry_pi/install.sh`, one command that replaces the old apt, systemd and `--server` / `--key` by-hand steps.
- Agents run things on the Pi with `ssh pi`, using a key login only. Never type or write down the Pi's password anywhere. If the key login fails, stop and ask the user.
- A Pi change isn't finished until its real-Pi checks pass (`DESKTALK_PI=pi uv run pytest -m pi`, and any command the task names, run over `ssh pi`). Paste the output into the PR. Don't say a Pi change works without that. Label each command you give the user **on the Pi** or **on your Mac**.

## Cloud server
- `cloud/` is a program of its own that keeps what a computer or Pi may send and writes a weekly report. It's optional: the app needs no account, and until `scripts/link_cloud.py` links it, the sender in `desktalk/core/cloud/` calls nothing. How to run it: [cloud/README.md](cloud/README.md).
- `cloud/server/` never imports `desktalk/`, and `desktalk/` never imports it. They share only `desktalk_shared`. `to_cloud_event()` in `shared/desktalk_shared/cloud_api.py` is the one filter of what may leave, and it lists what may go, never what may not. Pictures, to-do text and anything new stay home by default.
- It has its own rules for the same things: numbers in `cloud/settings.yaml` with a typed field in `cloud/server/desktalk_cloud/config.py`, wording in `cloud/prompts.yaml`, secrets in `cloud/.env` (and `cloud/.env.example`). `bootstrap.py` there is the only place that knows the real classes, and `ports.py` the interfaces.
- A new route goes in `cloud/server/desktalk_cloud/routers/`. `tests/unit/test_cloud_bootstrap.py` fails if it answers without a login, unless it's on that test's short list.
- A number that must match in two places (`compose.yaml`, `web/nginx*.conf` and `settings.yaml`) gets a test comparing them, as those in `test_cloud_bootstrap.py` do.
- Logs never hold a token, a code, a to-do or what Postgres said about a row.

## Writing style
- Plain, everyday words everywhere: code comments, docs, config comments, chat text, commit messages. Say "pictures" not "frames", "the Pi" not "the edge node". Match the existing README and `config/settings.yaml`.
- Commit messages: one short plain sentence about what changed, e.g. "Rename folders and files to clearer names".
