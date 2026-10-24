# Desk Talk: notes for coding agents

What the app does and how it fits together: [README.md](README.md), [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
How it got this way (history, finished): the refactor, [docs/plans/2026-10-21-refactor-plan.md](docs/plans/2026-10-21-refactor-plan.md), and the cleanup after it, [docs/plans/2026-10-23-codebase-cleanup-plan.md](docs/plans/2026-10-23-codebase-cleanup-plan.md).

## How to work
- **Before making a plan:** read the code the change will touch and find where the new pieces should plug in. Name those files and functions in the plan.
- **When carrying out a plan:** split the work into pieces that don't depend on each other and give each piece to its own subagent, all started at once. Use as many subagents as the plan allows, but never let two of them edit the same file.
- **Test first:** write a failing unit test, then the code that makes it pass. While working, run only the tests for the code you changed (e.g. `python -m pytest tests/test_<name>.py`). Run the full fast suite, plus the `slow` tests if you changed detection, once at the very end before opening the PR.
- **When the code is done:** open a PR, then start a subagent to review it with `/code-review high <PR number>`.
- **After the review:** start another subagent to fix what the review found, and give it the review comments. Then re-run the tests and push the fixes to the same PR.

## Check your work before saying it's done
- Packages come from uv (`uv sync --all-packages`; never pip). Run things with `uv run ...`.
- Fast tests: `uv run pytest -m "not slow"`. The `slow` tests run the real YOLO model; run them only when you change detection. `docker` tests build the Pi's detector image; run them only when you change it. `uv run pytest -m e2e` drives the real page in a browser (`uv run playwright install chromium` once); run it when you change `web_app/`, `phone_camera/` or a route they use.
- Coverage: `uv run pytest -m "not slow and not docker and not e2e" --cov` fails below `fail_under` in `pyproject.toml`. It may only go up.
- Lint: `uv run ruff check <files you changed>`. Old files may still have ruff errors, so don't fix files you didn't touch. Just don't add new errors. Don't run `ruff format`.
- Types: `uv run pyright` must show 0 errors. `pyrightconfig.json`'s `ignore` list only shrinks: fix a file and take it off; never add one.
- Dead code: `uv run vulture` must print nothing. A name it can't see being used goes in `tests/vulture_allow.py`, with what uses it.
- Show the test, ruff, pyright and vulture output. Don't just say they passed.

## Config, not code
- IMPORTANT: Never hardcode a number, timing, threshold, path or model name. Add it to `config/settings.yaml`, with a plain-language comment, and a typed field in `desktalk/config/app.py`; parts get their typed section passed in (`config.camera.fps`, etc.). The Pi's numbers go in the `pi:` section (the camera program reads it from the computer); the detector container's in `raspberry_pi/detector_service/.env`.
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
- Tests must never need the network, an API key, a camera, a Pi, Docker or a browser (the opt-in `slow`, `docker` and `e2e` tests aside). Use the fakes in `tests/fakes/` (`FakeClock`, `FakeDetector`, `FakeChatModel`, `SyncEventBus` ...).
- Every interface has a contract suite in `tests/contracts/`. A new real version of an interface gets a test class that runs the same suite.
- `tests/` is a package: import helpers as `from tests.fakes import ...` / `from tests.conftest import ...`. Test file names must be unique.
- Write the failing test first, watch it fail, then write the code (superpowers test-driven-development skill).

## Raspberry Pi
- Two things run on the Pi:
  - **The camera program** (`raspberry_pi/camera/`, run with `python3 -m raspberry_pi.camera` from the repo folder), on the Pi's own Python 3 (3.11 or newer). Standard library only, plus `picamera2`, `cv2` and `yaml` from apt, plus `desktalk_shared` straight from the cloned repo. No imports from `desktalk/`. Import the apt packages inside the function that uses them.
  - **The detector service** (`raspberry_pi/detector_service/`), which runs in Docker and may use anything uv installs, but never imports `desktalk/` (only `desktalk_shared`).
- `shared/desktalk_shared/` is used on the Pi and on this computer. It must stay standard library only and work on Python 3.11.
- Code gets to the Pi with `git pull` on the Pi (or scp from the Mac). You can't run anything on the Pi, so don't say a Pi change works. Tell the user what to copy and run there, and label each command **on the Pi** or **on your Mac**.

## Writing style
- Plain, everyday words everywhere: code comments, docs, config comments, chat text, commit messages. Say "pictures" not "frames", "the Pi" not "the edge node". Match the existing README and `config/settings.yaml`.
- Commit messages: one short plain sentence about what changed, e.g. "Rename folders and files to clearer names".
