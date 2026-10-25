# Codebase Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

Date: 2026-10-23. Status: **approved 2026-10-24.** Work happens on the `cleanup` branch, made from `main` after PR #3.

**Goal:** Remove dead code, move the last hardcoded values into config, finish the "interfaces first" design the
refactor started, and fill the real test gaps. What the app does and how it looks stay the same.

**Architecture:** Follows [the refactor plan](2026-10-21-refactor-plan.md): plain data in `desktalk/domain/`,
interfaces in `desktalk/ports.py`, logic in `desktalk/core/` that only knows interfaces, real things in
`desktalk/adapters/`, wiring only in `desktalk/bootstrap.py`. This plan first adds every new interface and
setting in one go (Task 4), then changes the code area by area in parallel (Tasks 5-9), then removes the last
shared object, `DeskState` (Task 10).

**Tech Stack:** Python 3.12, uv, FastAPI, pydantic, pytest + pytest-cov, ruff, pyright, vulture, Playwright (tests only).

**Spec:** this file. The audit numbers below were taken on the `refactor` branch on 2026-10-23. The refactor is on `main` now,
so line numbers may have moved: each task checks them against the code before acting on them.

## Global Constraints

- Every rule in `AGENTS.md` holds: test first, config not code, plain words, no keys in YAML, don't edit `config/settings.local.yaml`.
- The default test run never needs the network, an API key, a camera, a Pi, Docker or a browser download.
- Every URL and JSON reply that `web_app/` and `phone_camera/` use stays the same. `tests/characterization/` must keep passing; a deleted route also deletes its case.
- Settings key names that already exist keep their names and places, so the user's `config/settings.local.yaml` keeps loading (see Review Focus 1).
- `raspberry_pi/camera/` stays standard library + apt + `desktalk_shared` only. `shared/desktalk_shared/` stays standard library only and Python 3.11-compatible.
- No two subagents edit the same file. The ownership table in "Order of work" is binding.
- `ruff check .` stays clean. Coverage never drops below the floor that Task 1 sets.
- Commit messages: one short plain sentence.

## Review Focus

1. **The user's own `settings.local.yaml` after the config split.** Expected: it loads exactly as before. Test in Task 4: `test_local_settings_with_every_old_key_still_loads`.
2. **A slow AI reply (Ollama loading a model).** Expected: the app waits as long as the server does, then shows the error. Today the app gives up at 60 s while the server may still be on its 90 s try. Test in Task 4: `test_ui_chat_timeout_covers_the_slowest_server_reply`.
3. **The app page open while the server restarts.** Expected: the page shows "can't reach Desk Talk" and recovers on its own once the server is back. It must not get stuck on missing UI settings. Test in Task 3: `test_page_recovers_after_server_restart`.
4. **Event log files written before the cleanup.** Expected: old days still open in the Log tab, including lines with fields this plan stops writing. Test in Task 2: `test_old_log_lines_with_removed_fields_still_read`.
5. **A Pi still running the old `camera_node.py` (upload format 1).** Expected: still accepted until you decide otherwise (Decision 3). Kept by the existing `tests/integration/test_web_pi_uploads.py`; Task 2 must not delete `info_from_v1_headers`.

---

## Where we are (audit of the `refactor` branch, 2026-10-23; checked again on `main` 2026-10-24)

**The refactor is on `main`.** It landed in PR #3 (merge `8e459ac`), so Task 0 is done. This plan runs on a
`cleanup` branch made from that `main`. Two open PRs build on the same `main` and will be moved onto the cleaned
`main` afterwards by someone else: #6 (object states, which also carries the rule engine from #4) and #5 (safety
depth). So this cleanup stays out of code that only those PRs add.

On 2026-10-24 `main` has 1,656 fast tests passing and `ruff check .` clean. The findings below still hold
(for example the 60 s chat wait is still at `app.js:419`), but some line numbers have moved.

**Tests are already strong.** On `refactor`, 1,630 fast tests passed, with 98% line coverage. Branch coverage hasn't been measured.
There are contract tests for 9 interfaces, characterization tests for every route, and whole-app tests
with real threads and real uvicorn. `ruff check .` is clean.

| Area | Finding | Size |
|---|---|---|
| Dead code | Test-only methods: `KeyValueStore.delete`, `presence.absent_for`, `Roster.is_member`, `MessageGenerator.generate_chat_response`, `protocols.pick_nudge`, `BBox.iou`, `PhotoStore.deleted_total`. Never read: `RuleState.sips` and `RuleState.last_break_ts`. `active_scenarios` toggles are always on. 11 routes nothing calls. Unused CSS (`.drops`) and 4 art files. `docs/images/early-sitemap.png`. The `requests` dependency nothing imports | ~25 items |
| Hardcoded values | Colors, line widths, font sizes and see-through amounts in `drawing.py`. `HEALTHY_FOODS` and `DRINK_LABELS` lists. Numbers written into prompt and tip text (600 ml, 5 min, 10 min). The phone cert file names. About 30 timing and size constants in `web_app/app.js` and `phone_camera/camera.js` that copy `settings.yaml`. All the prompt and Lens wording in Python | 19 clear, 19 judgement calls, 12 duplicated defaults |
| **Bug found** | `app.js:419` gives up on a chat reply after 60 s; the server allows Ollama 90 s and up to 3 tries | 1 |
| Design | `DeskState` is an untyped shared object with 25 fields, used directly by 12 core and 5 web modules (`desk.lock` appears 44 times). `bootstrap.DeskTalk` is typed with concrete classes, so the `EventStore` and `PictureInbox` interfaces have no users. Web routes write desk state and import adapters. 5 core files import `cv2`, and 2 import `runtime`. 4 interfaces are defined outside `ports.py`. Two core classes have the same names as interfaces (`Preferences`, `DeskStatus`). 6 duplicated pieces of logic, including backoff written 3 times. `desktalk/config.py` (978 lines) does 3 jobs; `message_generator.py` does 5 | — |
| Tools | `pyright` isn't installed, so `pyrightconfig.json` is never used. Branch coverage isn't measured. Nothing stops dead code from coming back | — |
| Tests missing | No test drives the real web page. No end-to-end test of camera program → detector container → server → page, and no failure test for it (the refactor plan's unticked Phase 4 box). `safety_pipeline/` has no tests and is left out of lint | — |

Checked and clean: no `print()`, no module-level mutable state, every `except Exception` logs or re-raises,
no `time.time()` in core, only `bootstrap.py` builds adapters, and every key in the YAML files is read somewhere.

## Decisions (answered by Sam on 2026-10-24)

1. **Land the refactor first.** Done: PR #3 merged into `main`. Task 0 is skipped.
2. **`safety_pipeline/` and `run_safety_pipeline.py`.** **Delete** them, along with `POST /api/safety` and `GET /api/safety`,
   the `SafetyNews` schema, the 4 `docs/SAFETY_*.md` files and the `requests` dependency (git keeps the history).
3. **Upload format 1** (the old `camera_node.py`). **Keep** it for now (`info_from_v1_headers` stays). Delete it in a
   small follow-up once the new camera program has run on the Pi.
4. **Prompts and wording into config.** **Yes.** Every system prompt, Lens's wording, nudge lines and fixed replies move
   to a new `config/prompts.yaml`. Code keeps only the logic that fills in the blanks.
5. **JavaScript unit tests.** **No.** One Playwright browser test suite (Task 3), left out of the default test run.
6. **Test-only methods are deleted** (YAGNI). Interfaces with no production user (`EventStore`, `PictureInbox`) are **kept and put to use** in Task 10, because the design says the web layer talks to interfaces.
7. **The object state machine plan** ([2026-10-22](2026-10-22-object-state-machine-plan.md)) is being built in PR #6. This cleanup goes first; PR #6 and PR #5 are moved onto the cleaned `main` afterwards.
8. **Order.** Cleanup first, then #6 and #5 are rebased onto it. Keep this PR focused; don't reach into code only those PRs add.

## What was done (2026-10-24)

Tasks 1-11 are done on the `cleanup` branch. Where the work differs from the steps below:

- **Task 1:** pyright skips the files that had errors on a per-file list (not per folder), so every other file is
  checked. Vulture's settings live in `pyproject.toml` (`uv run vulture`), and it ignores route and pydantic
  decorators instead of listing every route. CI runs the fast tests with `--cov`, so the coverage floor is enforced.
- **Task 2:** `NudgeSent.rule` stays (PR #6's rule engine reads it), and so do `DetectionEvent.detected_on` (the
  Log tab and tests read it) and `GET /api/video_feed_raw` without a camera number (`benchmarks/README.md` tells
  people to open it). Removed POST/PUT routes answer 405, not 404, because the page files are served at `/`.
- **Task 3:** the page has no Lens chat in the chat list, so the Lens card is checked through `/api/agents`. The
  offline note didn't exist; Task 9 added it. The Docker full-stack tests are written but haven't run (no Docker here).
- **Tasks 4-10:** the interfaces' final names are in `desktalk/ports.py`; several differ from the list below
  (for example `camera_numbers`, `newest_light`, `DeskScene.set_members` + `note_picture`). `OpenCvPictureTools` was
  written before Tasks 5-9 because both vision and chat needed it. Tasks 5-9 and the Task 10 follow-ups ran in
  their own worktrees and were merged, so each could make small edits to `bootstrap.py` and the settings.
  `WholeDesk` (in `core/desk/`) passes calls on to the five desk parts for the seven constructors that still take
  one object for several desk interfaces.
- **Chat wait:** the worked-out wait for a chat reply (every agent, every retry) came to about 73 minutes, so the
  page now stops at `web.chat_reply_max_ms` (3 minutes) and shows the error instead of looking frozen.
- **Not done:** upload format 1 stays (Decision 3). The chat box's `maxlength="500"` in `web_app/index.html` is
  shorter than `chat.message_max_chars` (1200); it was left alone because changing it changes what you can type.

## Order of work, and who owns which files

```
Task 0  land refactor ──▶ Task 1  tool guards ──▶ ┌ Task 2  delete dead code ┐ ──▶ Task 4  interfaces + settings first
        (this session)          (this session)    └ Task 3  end-to-end tests ┘          (this session)
                                                       (parallel)                              │
          ┌──────────────┬──────────────┬──────────────┬──────────────┐ ◀──────────────────────┘
          Task 5 vision  Task 6 chat    Task 7 habits  Task 8 Pi+shared Task 9 web page     (parallel)
          └──────────────┴──────────────┴──────────────┴──────────────┘
                                         ──▶ Task 10  desk state + web layer ──▶ Task 11  docs, checks, PR, review
```

| Task | Owns (only it may edit these) |
|---|---|
| 2 | Every file on Task 2's list below (including `pyproject.toml` during this wave), `tests/characterization/cases.py`, `tests/characterization/snapshots/` |
| 3 | `tests/e2e/` (new), `tests/e2e_kit/` (new) |
| 4 | `desktalk/ports.py`, `desktalk/domain/**`, `desktalk/config.py` → `desktalk/config/**`, `config/*.yaml`, `desktalk/desk_state.py`, `desktalk/runtime/errors.py`, `desktalk/web/routers/settings.py`, `desktalk/web/routers/phone.py`, `desktalk/web/schemas.py`, `tests/fakes/**`, `tests/contracts/**`, `tests/characterization/**`, the renamed files listed in Task 4 |
| 5 | `desktalk/core/vision/**`, `desktalk/adapters/picture/**` (new) |
| 6 | `desktalk/core/chat/**` except `tracker_actions.py`, `desktalk/adapters/llm/**` |
| 7 | `desktalk/core/habits/**`, `desktalk/core/chat/tracker_actions.py` |
| 8 | `raspberry_pi/**`, `shared/**`, `desktalk/runtime/**` except `errors.py` |
| 9 | `web_app/**`, `phone_camera/**` |
| 10 | `desktalk/bootstrap.py`, `desktalk/web/**`, `desktalk/core/desk/**` (new), deletes `desktalk/desk_state.py` |
| 11 | `docs/**`, `README.md`, `AGENTS.md`, `raspberry_pi/README.md` |

Each task's tests live next to its own code's tests (`tests/unit/test_<area>_*.py`), so tests follow the same split.
**Tasks 5-9 do not edit `bootstrap.py`.** When a class is split, its old class stays the public front: it keeps the
same constructor and builds its new parts inside. Task 10 may then wire the parts directly. This keeps every
whole-app test passing after each task.

---

### Task 0: Land the refactor on `main` (this session)

**Done:** PR #3 merged on 2026-10-23. Skipped in this cleanup.

**Files:** conflict fixes only: `benchmarks/README.md`, `benchmarks/deskbench/cli.py`, `benchmarks/deskbench/report.py`, `benchmarks/results/README.md`, `benchmarks/tests/test_runner_results_report.py`, `config/benchmark.yaml`, `docs/plans/2026-10-21-refactor-plan.md`

- [ ] **Step 1:** In the `refactor` worktree, `git merge main`. For the benchmark files, take `main`'s side (it has the newer desk test), then re-apply the refactor's benchmark commits only where `main` doesn't already have them (`836faa3`: the best pick only from ncnn and pt). For the plan file, keep `refactor`'s version, which has the ticks.
- [ ] **Step 2:** Run `uv run pytest -m "not slow and not docker"` and `cd benchmarks && uv run pytest`. Expected: both pass. Run `uv run ruff check .`. Expected: `All checks passed!`
- [ ] **Step 3:** Commit "Merge main into the refactor branch". Ask before you push. Open a PR `refactor` → `main`, and run `/code-review high <PR>` in a subagent, as AGENTS.md says. Fix what it finds and merge after you OK it.
- [ ] **Step 4:** Make a `cleanup` branch from the new `main` for Tasks 1-11.

### Task 1: Tool guards (this session)

**Files:** Modify `pyproject.toml`, `.github/workflows/tests.yml`, `pyrightconfig.json`. Create `tests/vulture_allow.py`.

- [ ] **Step 1:** Add `pyright`, `vulture` and `playwright` to the `dev` group (Playwright is for Task 3; its browser is downloaded only when someone runs `-m e2e`). Run `uv run pyright` and write down the error count per folder. Then set `pyrightconfig.json` to `basic` and add per-folder ignores for the folders that fail today, so the count starts at 0 and can only go down.
- [ ] **Step 2:** In `[tool.coverage.run]`, set `branch = true`. Run `uv run pytest -m "not slow and not docker" --cov`, and set `[tool.coverage.report] fail_under` to the measured total, rounded down.
- [ ] **Step 3:** Make `tests/vulture_allow.py`. It lists the names vulture can't see being used: FastAPI route functions, pydantic fields read through `getattr`/`model_dump` (`RiskWeights.*`, `AiReplies.alert`, `LensWaits.*`, `VisionNudgeRepeats.slouch`), `should_exit`, and the event fields the app reads in JS (`stayed_sec`, `from_ts`, `last_cleanup_ts`). Run `uv run vulture desktalk shared raspberry_pi scripts server.py tests/vulture_allow.py --min-confidence 60`. Expected: it lists only the items Task 2 deletes.
- [ ] **Step 4:** Add the `e2e` marker to `[tool.pytest.ini_options]` ("drives the real page in a browser; needs `uv run playwright install chromium` once"). In CI, add `uv run pyright`, add the vulture command with `--min-confidence 80`, and change the pytest line to `-m "not slow and not docker and not e2e"`.
- [ ] **Step 5:** Commit "Add pyright, branch coverage and a dead code check to the tests".

### Task 2: Delete dead code (subagent, parallel with Task 3)

**Files:** `desktalk/adapters/storage/json_files.py`, `desktalk/ports.py` (only `KeyValueStore.delete`), `desktalk/core/agents/presence.py`, `desktalk/core/agents/roster.py`, `desktalk/core/chat/message_generator.py`, `desktalk/core/habits/protocols.py`, `desktalk/core/habits/tracker.py` (lines writing `sips` / `last_break_ts`), `desktalk/desk_state.py` (`active_scenarios`), the 3 workers that read `active_scenarios`, `shared/desktalk_shared/detection.py`, `raspberry_pi/camera/photo_store.py`, `desktalk/web/routers/{chat,habits,live,settings,phone,video}.py`, `desktalk/web/schemas.py`, `web_app/style.css`, `web_app/art/{blob,burst,dots,squiggle}.svg`, `docs/images/early-sitemap.png`, `scripts/capture_app_screenshots.py`. If Decision 2 is delete: also `safety_pipeline/`, `run_safety_pipeline.py`, `docs/SAFETY_*.md`, and the `requests` line plus the safety excludes in `pyproject.toml`.

**Interfaces:** Produces `KeyValueStore` without `delete`. Routes removed: `GET /api/context`, `GET /api/daily_stats`, `GET|PUT /api/habit_policy`, `GET /api/detections`, `GET|POST /api/safety`, `GET /api/scenarios`, `GET /api/phone/info`, `GET /api/video_feed`, `GET /api/video_feed_raw` (the forms without a camera number), `GET /api/latest_frame/{camera}`. **Keep** `/api/health` (the docs tell people to curl it).

- [ ] **Step 1: Write the failing tests.** In `tests/integration/test_web_routes.py`, add `test_removed_routes_answer_404`, which loops over the routes above and asserts `status_code == 404`. In `tests/unit/test_storage_event_log.py`, add `test_old_log_lines_with_removed_fields_still_read`: write a day file with a line that has `detected_on` and a nudge line with `rule`, and assert that `events(day, None, 10)` returns both lines.
- [ ] **Step 2:** Run `uv run pytest tests/integration/test_web_routes.py -k removed_routes`. Expected: FAIL (the routes answer 200).
- [ ] **Step 3:** Delete each item, and with it the tests that only test it and its characterization case. Delete the `active_scenarios` checks in the 3 workers, which leaves the "always on" branch.
- [ ] **Step 4:** Run `uv run pytest -m "not slow and not docker and not e2e"` and the vulture command from Task 1. Expected: all pass, and vulture prints nothing.
- [ ] **Step 5:** Commit "Delete code, routes and files that nothing uses".

### Task 3: End-to-end tests as a safety net (subagent, parallel with Task 2)

Written **before** the design changes, through the page and HTTP only, so Tasks 4-10 can't quietly break the app.

**Files:** Create `tests/e2e_kit/server.py`, `tests/e2e/test_page.py`, `tests/e2e/test_full_stack.py`. (Task 1 already added `playwright` to the `dev` group.)

**Interfaces:** Produces `running_app(tmp_path, *, pictures: list[Path]) -> ContextManager[str]` in `tests/e2e_kit/server.py`. It builds the app with `bootstrap.build(...)` using a `FakeDetector` (scripted: a bottle in picture 2) and a `FakeChatModel` (canned replies), serves it with `desktalk/web/serve.py` on a free port, and yields the base URL. Use `tests/integration/web_kit.py` as the model.

- [ ] **Step 1: Write the tests** (marked `@pytest.mark.e2e`, and skipped when Chromium isn't installed):
  - `test_page_loads_and_shows_lens` — the page opens and the Lens card is in the chat list.
  - `test_bottle_on_desk_joins_the_chat` — after picture 2, a chat named after the bottle shows up within 5 s.
  - `test_sending_a_message_gets_a_reply` — type "hi" in Lens's chat and see the canned reply.
  - `test_habit_log_click_updates_today` — click a water button and see today's water number change.
  - `test_page_recovers_after_server_restart` — stop and start the server, and the page shows the offline note and then clears it (Review Focus 3).
  - In `test_full_stack.py` (`@pytest.mark.docker`): `test_camera_program_to_page_through_the_container` — start the detector container with `docker compose`, run `raspberry_pi/camera` with the folder camera on `tests/fixtures/desk_photos`, and see a detection reach `/api/live`. Then `test_container_stopped_falls_back_to_off`: `docker compose stop`, and the next upload's state is `off` while detections still arrive (detected on this computer).
- [ ] **Step 2:** Run `uv run playwright install chromium` and `uv run pytest -m e2e`. Expected: PASS against today's code. These tests pin current behaviour, so a failure here is a real bug to report, not something to fix by changing the test.
- [ ] **Step 3:** Commit "Add browser and full-stack tests that drive the real page".

### Task 4: Interfaces and settings first (this session, one step at a time)

Everything later tasks build against is added here, before any code moves.

**Files:** Split `desktalk/config.py` into `desktalk/config/__init__.py` (re-exports, so `from desktalk.config import X` keeps working), `desktalk/config/app.py` (the settings.yaml models), `desktalk/config/habits.py` (the protocols.yaml models), `desktalk/config/agents.py` (the agents.yaml models), `desktalk/config/prompts.py` (new), and `desktalk/config/loader.py` (YAML + .env loading and error wording). Create `config/prompts.yaml`. Modify `config/settings.yaml`, `config/protocols.yaml`, `desktalk/ports.py`, `desktalk/domain/`, `desktalk/desk_state.py`, `desktalk/runtime/errors.py`, `desktalk/web/routers/settings.py`, `desktalk/web/routers/phone.py` (the new `/api/phone/config` fields), `desktalk/web/schemas.py`, `tests/fakes/`, `tests/contracts/`. Renames: `desktalk/core/habits/protocols.py` → `habit_rules.py`, class `core/habits/preferences.Preferences` → `PreferencesFile`, class `core/habits/desk_status.DeskStatus` → `DeskStatusService` (only the renames, with their imports updated).

**Interfaces (produces):**
- New settings, each with a plain comment:
  - `drawing:` — `colors` (named BGR lists, including the risk colors), `line_px`, `font_scale`, `fill_alpha.{zone,desk,danger}`, `trail.{start_after,keep}`, `dash`, `arrow_tip`, `radius`.
  - `web:` — `refresh_ms.{live,log,habits,health}`, `request_timeout_ms`, `activity_ping_ms`, `toast_ms`, `list_sizes.{log,photos}`.
  - `camera.phone_cert_file`, `camera.phone_key_file`, `camera.phone_max_side`, `camera.phone_jpeg_quality`, `camera.phone_tilt_max`.
  - `logging.file_name`, `logging.preview_chars`, `llm.error_text_chars`.
  - `protocols.yaml`: `food.healthy` (today's `HEALTHY_FOODS`).
  - `config/prompts.yaml` (Decision 4): every constant from `core/chat/prompts.py`, the `camera_health.py` text tables, `lens.PI_FIXED`, `vision_habits.NUDGES`, `lost_items.TRIGGERS` / `NOT_SPOTTED`, `chat.LENS_CARD`, and the fixed replies `NEEDS_AN_AI` / `EMPTY_REPLY` (once; today they're written twice). Numbers inside the text become `{placeholders}` filled from settings (600 ml → `{on_waking_ml}`, 5-minute walk → `{break_min}`, "use 10 if unsure" → `{sunlight_default_min}`).
- `GET /api/ui_settings` → `UiSettings` (pydantic): the `web:` section, plus the values the JS copies today: chat message and to-do limits, `sunlight_default_min`, `light.morning_minutes`, `breaks.*`, `edge_proximity`, `quiet_hours`, `desk_border_share`, `max_desk_corners`, `pi.fps`, `pi.delete_every_min`, the phone camera values, `PALETTE`, the Lens card, and `chat_reply_timeout_ms`. That last one is computed: (`ollama_timeout_sec` × `max_retries` + the retry waits) × 1000 + `web.request_timeout_ms`.
- New interfaces in `ports.py`, each with a fake in `tests/fakes/` and a contract suite in `tests/contracts/`:
  - `PictureTools`: `decode_jpeg(jpeg: bytes, *, gray: bool = False) -> Picture | None`, `encode_jpeg(picture: Picture, quality: int) -> bytes | None`, `shrink(picture: Picture, width: int) -> Picture`, `to_gray(picture: Picture) -> Picture`, `difference(a: Picture, b: Picture) -> float`, `sharpness(gray: Picture) -> float`. (`Picture = Any` lives in `domain/`.)
  - `PhotoTaker`: `save(label: str, crop_boxes: Sequence[Box] = ()) -> str | None`. This is today's `ProofPhotos.save`.
  - `DeskRulesView`: `current() -> DeskRules`, `has_outline() -> bool`, `reference_gravity() -> tuple[float, ...] | None`, `save(changes: Mapping[str, Any]) -> None`, `for_settings_page() -> dict[str, Any]`.
  - The `DeskState` split (frozen domain dataclasses `SeenObject`, `SafetyNow`, `Photo`; every method does its own locking; no `lock` attribute):
    - `LivePictures`: `put(cam: int, plain: bytes, boxed: bytes | None, size: Size) -> None`, `plain(cam: int | None = None) -> bytes | None`, `boxed(cam: int) -> bytes | None`, `cameras() -> list[int]`, `size() -> Size`, `set_light(brightness: float, lux: float | None) -> None`, `light() -> Light`.
    - `DeskScene`: `update(members: Mapping[str, SeenObject], seen: Mapping[str, Box], now: float) -> None`, `members() -> dict[str, SeenObject]`, `seen_labels() -> dict[str, Box]`, `first_seen(agent_id: str) -> float | None`, `last_seen(agent_id: str) -> float | None`, `person_last_seen() -> float | None`, `note_motion(now: float) -> None`, `last_motion() -> float | None`, `session_start() -> float | None`, `set_scan_labels(labels: set[str]) -> None`, `scan_labels() -> set[str]`.
    - `SafetyBoard`: `report(state: str, dangers: Sequence[Danger], result: Mapping[str, Any] | None, now: float) -> None`, `now() -> SafetyNow`, `danger_since(pair: frozenset[str]) -> float | None`, `note_event(event: Mapping[str, Any]) -> None`, `recent_events(limit: int) -> list[dict[str, Any]]`.
    - `CharacterMemory`: `remember(agent_id: str, text: str) -> None`, `recent(agent_id: str) -> list[str]`, `set_widget(agent_id: str, data: Mapping[str, Any]) -> None`, `widget(agent_id: str) -> dict[str, Any] | None`, `note_widget_view(agent_id: str, action: str, now: float) -> None`, `note_user_activity(now: float) -> None`, `last_user_activity() -> float`, `set_vision_brief(text: str) -> None`, `vision_brief() -> str`.
    - `PhotoAlbum`: `add(jpeg: bytes, label: str, tags: Sequence[str], ts: float) -> str`, `get(photo_id: str) -> Photo | None`, `newest(limit: int) -> list[Photo]`.
  - Moved into `ports.py` from where they are now: `HabitLog` (`core/chat/tracker_actions.py:25`), and `PhoneCamera`, `PiUploads`, `DeskLayout` (`core/chat/workers/needs.py`).
  - `PiStatus` and `PhoneControls`: the methods web routes call today that no interface has (`status()`, `pi_info()`, `recent_gravity()`, plus phone start and stop). Read `desktalk/web/routers/{pi,phone,live}.py` to get the exact list.
  - `Clock.local_hour(now: float) -> int` and `Clock.local_day(now: float) -> str`, so core stops calling `time.localtime`.
- `plain_error` moves from `desktalk/runtime/errors.py` to `desktalk/domain/errors.py`, with its two core imports updated.
- `DeskState` gets methods so it implements all five desk interfaces at once (temporary, removed in Task 10). The old attributes stay until Task 10.

- [ ] **Step 1: Write the failing tests:**
  - `tests/unit/test_config.py::test_local_settings_with_every_old_key_still_loads`. Copy `config/settings.yaml` as it is now into a fixture `tests/fixtures/settings_before_cleanup.yaml`, load it as a local override, and assert no error and the same values.
  - `test_every_new_setting_has_a_comment`. Parse `config/settings.yaml` and assert every key added in this task has a `#` comment on its line or the line above.
  - `tests/integration/test_web_routes.py::test_ui_settings_reply` (shape, plus a characterization case).
  - `test_ui_chat_timeout_covers_the_slowest_server_reply`: with `ollama_timeout_sec: 90, max_retries: 3`, assert `chat_reply_timeout_ms >= 270_000`.
  - `tests/unit/test_prompts.py::test_every_prompt_placeholder_has_a_value`: format every prompt with the real settings and assert no `KeyError` and no `{` left.
  - `test_desk_state_passes_<port>_contract` for each of the 5 desk interfaces, and `test_fake_passes_<port>_contract` for every new interface.
- [ ] **Step 2:** Run `uv run pytest tests/unit/test_config.py tests/unit/test_prompts.py tests/contracts -q`. Expected: FAIL (missing).
- [ ] **Step 3:** Implement in this order, and run the fast suite after each: split `config.py` (pure move) → renames → new settings and `prompts.yaml` → `/api/ui_settings` → new interfaces, fakes and contracts → `DeskState` methods → move `plain_error`.
- [ ] **Step 4:** Run `uv run pytest -m "not slow and not docker and not e2e"`, `uv run ruff check .` and `uv run pyright`. Expected: all pass, 0 new pyright errors.
- [ ] **Step 5:** Commit after each piece, e.g. "Split the settings code into one file per config file".

### Task 5: Vision (subagent)

**Files:** `desktalk/core/vision/**`; create `desktalk/adapters/picture/opencv_tools.py` and `desktalk/adapters/picture/painter.py`.
**Interfaces:** Consumes `PictureTools`, `PhotoTaker`, `DeskRulesView`, `LivePictures`, `DeskScene`, `SafetyBoard`, `PhotoAlbum`, and the `drawing:` settings from Task 4.

- [ ] Move `core/vision/drawing.py` to `adapters/picture/painter.py`. It already implements `LiveViewPainter`. Read every color, width, font size, see-through amount, trail length and radius from `settings.drawing`. Test `test_painter_uses_drawing_settings`: with `line_px: 5`, a box edge is 5 pixels thick.
- [ ] `OpenCvPictureTools` in `adapters/picture/opencv_tools.py` must pass the `PictureTools` contract.
- [ ] Remove `import cv2` from `producer.py`, `camera_health.py` and `proof_photos.py`; they take `PictureTools`. Add a test, `tests/unit/test_layers.py::test_core_imports_no_io_libraries`: walk `desktalk/core` and `desktalk/domain` with `ast`, and assert no import of `cv2`, `numpy`, `requests`, `openai`, `ultralytics`, `fastapi`, `desktalk.adapters`, `desktalk.web`, `desktalk.runtime` or `desktalk.bootstrap`. Mark it `xfail(strict=True)` for the chat files until Task 6 lands; Task 11 removes the xfail.
- [ ] Split `camera_health.py` into `camera_measure.py` (numbers from a picture), `camera_problems.py` (the problem state machine, with typed parameters on `_check`, `_problems_now`, `_picture_problems` and `_confirm_and_clear`), and the wording, which now comes from `prompts.yaml`. `CameraHealth` stays the public front.
- [ ] `desk_monitor`, `analyzer` and `live_view` take `DeskRulesView`, `PhotoTaker` and the narrow desk interfaces instead of `DeskState`, `SafetyRules`, `DeskAnalyzer` and `ProofPhotos`. Keep each constructor's parameter names so `bootstrap.py` still works.
- [ ] Run `uv run pytest tests/unit/test_core_vision_*.py tests/unit/test_core_producer.py tests/integration -q`, then `uv run pytest -m slow` (detection path). Commit per piece.

### Task 6: Chat (subagent)

**Files:** `desktalk/core/chat/**` except `tracker_actions.py`; `desktalk/adapters/llm/**`.
**Interfaces:** Consumes `CharacterMemory`, `DeskScene`, `SafetyBoard`, `PictureTools`, `PhotoTaker`, `Clock.local_hour`, and `settings.prompts` from Task 4.

- [ ] Split `message_generator.py` into `alert_voice.py`, `nudge_voice.py`, `chat_answers.py` and `memory.py` (a `CharacterMemory` user, and the only writer of character memory: `idle_chat.py:208-215` uses it too). `MessageGenerator` stays the front that builds them.
- [ ] Split `context.py` into `names.py` (delete its `mention_words` copy and call `Profiles.mention_words()`), `history.py` and `live_context.py`.
- [ ] Split `workers/lost_items.py`: answering "where are my keys" goes to `workers/lost_items_answers.py`. Picture comparing uses `PictureTools.difference`.
- [ ] `lens.py` deletes `_quiet_hours` and uses the shared `quiet_hours_now`. `DRINK_LABELS` comes from `settings.safety.liquids`. All wording comes from `settings.prompts`. `ASK_FOR_A_SENTENCE` and the cut lengths `[:160]`, `[:200]`, etc. come from settings.
- [ ] `time.localtime` in `daily_summary.py` and `lens.py` becomes `clock.local_hour` / `local_day`.
- [ ] Test `test_chat_uses_prompt_settings`: change one prompt in a test settings object and see it sent to the `FakeChatModel`. Run `uv run pytest tests/unit/test_core_chat_*.py tests/unit/test_core_workers_*.py tests/unit/test_llm_*.py tests/integration -q`. Commit per piece.

### Task 7: Habits (subagent)

**Files:** `desktalk/core/habits/**`, `desktalk/core/chat/tracker_actions.py`.
**Interfaces:** Consumes `Clock.local_hour/local_day`, `food.healthy`, the prompt placeholders, and the renamed `habit_rules.py`, `PreferencesFile` and `DeskStatusService` from Task 4.

- [ ] Add a `HabitDay` dataclass in `core/habits/habit_days.py` to replace the untyped `self._d` dict (39 uses). Its on-disk JSON stays the same: test `test_habit_day_reads_and_writes_todays_file_format` uses a saved file from before the change.
- [ ] Split `tracker.py` into `habit_days.py` (storage and rollover), `habit_reader.py` (reading habits from detections; `HEALTHY_FOODS` comes from settings) and `tracker.py` (manual logging and nudges, still the front).
- [ ] The tip and action texts in `habit_rules.py` and `tracker_actions.py` use the `prompts.yaml` placeholders. Test `test_water_tip_follows_on_waking_ml`: set `on_waking_ml: 450` and see "450 ml" in the tip.
- [ ] `HabitsView.save` moves out of the view into a separate `HabitsLifecycle` interface (`save(force: bool = False)`), which `bootstrap` alone calls.
- [ ] Run `uv run pytest tests/unit/test_core_habits_*.py tests/unit/test_core_chat_tracker_actions.py tests/integration -q`. Commit per piece.

### Task 8: Pi, shared code and runtime (subagent)

**Files:** `raspberry_pi/**`, `shared/**`, `desktalk/runtime/**` except `errors.py`.
**Interfaces:** Produces, in `shared/desktalk_shared/` (standard library only, Python 3.11):
- `backoff.py`: `Backoff(first: float, most: float)` with `.next() -> float` and `.reset() -> None`
- `clock.py`: `SystemClock` (the one Clock implementation)
- `workers.py`: `WorkerThread(name: str, step: Callable[[], float], backoff: Backoff, clock)` that restarts its step after a crash

- [ ] Write the tests first: `test_backoff_doubles_up_to_most_and_resets`, `test_worker_thread_restarts_after_a_crash`.
- [ ] `raspberry_pi/camera/uploader.py`, `detector_client.py` and `desktalk/runtime/supervisor.py` use `Backoff`. The Pi's `clock.py` and `main.py:212 WorkerThread` use the shared ones. `desktalk/runtime/clock.py` re-exports `SystemClock` and adds `local_hour` / `local_day`.
- [ ] Split `raspberry_pi/camera/main.py`: workers go to `workers.py` and the command line to `cli.py`, leaving the loop.
- [ ] `test_pi_settings_match_the_server_model`: assert that the field names `raspberry_pi/camera/settings.py` checks equal `PiSettings.model_fields`, so the two hand-written checks can't drift.
- [ ] `ERROR_TEXT_MAX` and `--camera` default: read from the `pi:` settings. In `compose.yaml`, healthcheck and log sizes come from `.env` (`DETECTOR_HEALTH_INTERVAL`, etc.; add them to `.env.example`). "MB" means 1024 × 1024 everywhere: fix `detector_service/settings.py:14`.
- [ ] Run `uv run pytest tests/unit/test_camera_program_*.py tests/unit/test_detector_service_*.py tests/unit/test_runtime_*.py tests/unit/test_shared_*.py -q` and `uv run ruff check raspberry_pi shared`. Tell the user what to copy to the Pi, labelled **on your Mac** / **on the Pi**; don't claim it works on the Pi. Commit per piece.

### Task 9: Web page and phone page (subagent)

**Files:** `web_app/**`, `phone_camera/**`.
**Interfaces:** Consumes `GET /api/ui_settings` from Task 4.

- [ ] On start, `app.js` fetches `/api/ui_settings` and keeps it in one `ui` object. Every constant in the hardcoded-values audit (refresh times, timeouts, limits, minutes, slider snap, corners, palette, Lens card, the "3 pictures a second" text) reads from `ui`. If the fetch fails, the page shows its offline note and retries; it never falls back to copied numbers.
- [ ] The chat request uses `ui.chat_reply_timeout_ms` (fixes the 60 s bug).
- [ ] `camera.js` takes `MAX_SIDE`, JPEG quality, size and fps from `/api/phone/config`, which already exists. Task 4 added the fields.
- [ ] Check: `grep -nE '\b[0-9]{2,}\b' web_app/app.js phone_camera/camera.js` lists only the Low rows from the audit, which stay in the page (debounce times, slider ranges). Run `uv run pytest -m e2e`. Expected: PASS. Commit "Have the web page and phone page take their numbers from the server".

### Task 10: Remove `DeskState` and clean the web layer (this session or one subagent)

**Files:** `desktalk/bootstrap.py`, `desktalk/web/**`, create `desktalk/core/desk/{live_pictures,scene,safety_board,character_memory,photo_album}.py`, delete `desktalk/desk_state.py`.

- [ ] Write each class in `core/desk/` so it passes its contract suite from Task 4.
- [ ] `bootstrap.DeskTalk`: type every field as an interface (`bus: EventBus`, `event_log: EventStore`, `ai: ChatModel`, `pi_inbox: PictureInbox`, `pi: PiStatus`, `phone: PhoneControls`, ...). Delete the `isinstance` checks at `bootstrap.py:163,167`. Wire the parts that Tasks 5-7 split directly, where that's clearer than going through the front classes.
- [ ] Web routes: `live.py:123-127` was deleted with `POST /api/safety` (Task 2) or calls `SafetyBoard.report`. `chat.py:56,62` calls `CharacterMemory.note_user_activity`. `pairing.py` takes a `KeyValueStore` instead of importing `write_atomically`. `is_day` moves to `domain/`. The `Desk` dependency becomes `App`.
- [ ] Delete `desktalk/desk_state.py` and every `desk.lock`. Test `tests/unit/test_layers.py::test_web_imports_only_ports_domain_and_bootstrap`.
- [ ] Run `uv run pytest -m "not slow and not docker"` and `uv run pytest -m e2e`. Commit per piece.

### Task 11: Docs, last checks, PR and review (this session)

- [ ] Update `docs/ARCHITECTURE.md` (no `DeskState`; the new desk interfaces; `config/prompts.yaml`), `docs/AGENT_SYSTEM.md`, `README.md` (folder tree, `pyright`, `-m e2e`), and `AGENTS.md` (add: "user-facing wording goes in `config/prompts.yaml`"; "run `uv run pyright`"). Fix `docs/SAFETY_RULES.md:12` "Flask server" if that file survives Decision 2.
- [ ] Remove the `xfail` from `test_core_imports_no_io_libraries`. Raise coverage `fail_under` to the new total, rounded down.
- [ ] Run, and paste the output: `uv run pytest -m "not slow and not docker and not e2e"`, `uv run pytest -m slow`, `uv run pytest -m e2e`, `uv run ruff check .`, `uv run pyright`, and the vulture command. Run `uv run pytest -m docker` if Docker is up.
- [ ] Open the PR. Start a subagent with `/code-review high <PR>`, then another subagent to fix what it finds. Re-run the checks and push to the same PR.

## Risks

- **Two big PRs in a row.** Landing the refactor (117 commits) and then this cleanup. Each one's review must finish before the next starts.
- **Task 4 is wide.** It touches config, interfaces and fakes all at once. It runs in this session, one step at a time, with the fast suite after each step, because everything after it depends on it.
- **Moving prompts into YAML** makes a typo in a placeholder a startup-time problem instead of a code problem. `test_every_prompt_placeholder_has_a_value` catches it in tests, and the loader stops startup with the prompt's name.
- **The browser tests need Chromium downloaded once.** They're opt-in (`-m e2e`) and left out of the default run and CI, like `slow` and `docker`.
- **Pi changes (Task 8) can't be run from here.** They're done only when you've copied them to the Pi and run `--self-test`.
