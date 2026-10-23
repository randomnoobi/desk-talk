# Desk Talk: notes for coding agents

What the app does and how it fits together: [README.md](README.md), [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## How to work
- **Before making a plan:** read the code the change will touch and find where the new pieces should plug in. Name those files and functions in the plan.
- **When carrying out a plan:** split the work into pieces that don't depend on each other and give each piece to its own subagent, all started at once. Use as many subagents as the plan allows, but never let two of them edit the same file.
- **Test first:** write a failing unit test, then the code that makes it pass. While working, run only the tests for the code you changed (e.g. `python -m pytest tests/test_<name>.py`). Run the full fast suite, plus the `slow` tests if you changed detection, once at the very end before opening the PR.
- **When the code is done:** open a PR, then start a subagent to review it with `/code-review high <PR number>`.
- **After the review:** start another subagent to fix what the review found, and give it the review comments. Then re-run the tests and push the fixes to the same PR.

## Check your work before saying it's done
- Fast tests: `python -m pytest -m "not slow"`. The `slow` tests run the real YOLO model; run them only when you change detection.
- Lint: `ruff check <files you changed>`. The repo already has ruff errors, so don't fix files you didn't touch. Just don't add new errors. Don't run `ruff format`.
- Show the test and ruff output. Don't just say they passed.

## Config, not code
- IMPORTANT: Never hardcode a number, timing, threshold, path or model name. Add it to `config/settings.yaml`, with a plain-language comment, and read it through `desktalk/settings.py` (`settings.camera.fps`, etc.).
- Habit rules go in `config/protocols.yaml`, characters in `config/agents.yaml`, and API keys in `.env` (also add new keys to `.env.example`). Never put keys in YAML.
- `config/settings.local.yaml` is the user's own file and isn't in git. Don't edit it.

## Tests
- Tests must never need the network, an API key, a camera or a Pi. Use fakes for the LLM and the camera.

## Raspberry Pi
- `raspberry_pi/camera_node.py` is the only file that runs on the Pi. It must work on its own: no imports from `desktalk/`, `safety_pipeline/` or anything pip-installed. Only the standard library plus `picamera2`, `cv2` and `yaml`, which come from apt (see [raspberry_pi/README.md](raspberry_pi/README.md)). Import those inside the function that uses them, as the file does now.
- Code gets to the Pi by SSH/scp from the Mac. You can't run anything on the Pi, so don't say a Pi change works. Tell the user what to copy and run there, and label each command **on the Pi** or **on your Mac**.

## Writing style
- Plain, everyday words everywhere: code comments, docs, config comments, chat text, commit messages. Say "pictures" not "frames", "the Pi" not "the edge node". Match the existing README and `config/settings.yaml`.
- Commit messages: one short plain sentence about what changed, e.g. "Rename folders and files to clearer names".
