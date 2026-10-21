# Desk Talk: notes for coding agents

What the app does and how it fits together: [README.md](README.md), [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
The refactor in progress: [docs/plans/2026-10-21-refactor-plan.md](docs/plans/2026-10-21-refactor-plan.md).

## Check your work before saying it's done
- Packages come from uv (`uv sync --all-packages`; never pip). Run things with `uv run ...`.
- Fast tests: `uv run pytest -m "not slow"`. The `slow` tests run the real YOLO model; run them only when you change detection. `docker` tests build the Pi's detector image; run them only when you change it.
- Lint: `uv run ruff check <files you changed>`. Old files may still have ruff errors, so don't fix files you didn't touch. Just don't add new errors. Don't run `ruff format`.
- Show the test and ruff output. Don't just say they passed.

## Config, not code
- IMPORTANT: Never hardcode a number, timing, threshold, path or model name. Add it to `config/settings.yaml`, with a plain-language comment, and a typed field in `desktalk/config.py`; parts get their typed section passed in (`config.camera.fps`, etc.). The old code still reads `desktalk/settings.py` until the refactor removes it.
- Habit rules go in `config/protocols.yaml`, characters in `config/agents.yaml`, and API keys in `.env` (also add new keys to `.env.example`). Never put keys in YAML.
- `config/settings.local.yaml` is the user's own file and isn't in git. Don't edit it.

## Interfaces first
- Parts talk through the interfaces in `desktalk/ports.py` and pass the plain data in `desktalk/domain/`. A part asks for only the interfaces it uses in its constructor. Never pass the whole `app` object.
- Ask a `Clock` for the time instead of calling `time.time()`, so tests can move time by hand.
- Background jobs are `PeriodicWorker`s run by the Supervisor, so one failure never switches a feature off for good.
- Use `logging`, never `print` (ruff rule T20). Scripts in `scripts/` may print.
- Changing `ports.py`, `desktalk/domain/` or `shared/desktalk_shared/` changes the contract every part builds on. Do it on purpose, and update the fakes and contract tests with it.

## Tests
- Tests must never need the network, an API key, a camera, a Pi or Docker. Use the fakes in `tests/fakes/` (`FakeClock`, `FakeDetector`, `FakeChatModel`, `SyncEventBus` ...).
- Every interface has a contract suite in `tests/contracts/`. A new real version of an interface gets a test class that runs the same suite.
- `tests/` is a package: import helpers as `from tests.fakes import ...` / `from tests.conftest import ...`. Test file names must be unique.
- Write the failing test first, watch it fail, then write the code (superpowers test-driven-development skill).

## Raspberry Pi
- Two things run on the Pi:
  - **The camera program**, on the Pi's own Python 3 (3.11 or newer). Standard library only, plus `picamera2`, `cv2` and `yaml` from apt, plus `desktalk_shared` straight from the cloned repo. Import the apt packages inside the function that uses them. Today the camera program is `raspberry_pi/camera_node.py`; the refactor replaces it with `raspberry_pi/camera/`.
  - **The detector service** (`raspberry_pi/detector_service/`), which runs in Docker and may use anything uv installs.
- `shared/desktalk_shared/` is used on the Pi and on this computer. It must stay standard library only and work on Python 3.11.
- Code gets to the Pi with `git pull` on the Pi (or scp from the Mac). You can't run anything on the Pi, so don't say a Pi change works. Tell the user what to copy and run there, and label each command **on the Pi** or **on your Mac**.

## Writing style
- Plain, everyday words everywhere: code comments, docs, config comments, chat text, commit messages. Say "pictures" not "frames", "the Pi" not "the edge node". Match the existing README and `config/settings.yaml`.
- Commit messages: one short plain sentence about what changed, e.g. "Rename folders and files to clearer names".
