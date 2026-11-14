# Desk Talk's Own Room on the SD Card: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Desk Talk a fixed-size space on the Pi's SD card (a disk image mounted at the data folder) that
nothing else can fill, keep saving history while the rest of the card is full, and keep running (saving nothing, and
saying so) when that space isn't mounted.

**Architecture:** A new script, `raspberry_pi/data_room.sh`, makes, checks and grows the image and moves the history
in. A systemd unit mounts it before Docker at boot. `install.sh` runs that script. Inside the app container,
`start.sh` decides one of three saving modes (`plain`, `room`, `off`) and hands it to the app through the environment.
The app picks its data folder and its health checks from that mode, and Lens says what's wrong.

**Tech Stack:** bash (install scripts, tested with stand-ins in `tests/unit/test_pi_install_script.py`'s style),
POSIX sh (`start.sh`), systemd, Docker Compose, Python 3.12 (app), TypeScript (`dev_ui/`).

**Spec:** `docs/superpowers/specs/2026-11-11-reserved-data-space-design.md`. Read it first.

**Branch:** `data-room`, cut from `pi-reliability`. The PR goes into `pi-reliability`.

## Global Constraints

- AGENTS.md applies in full. In particular:
  - Write a failing test first.
  - No hardcoded numbers, paths or timings. Pi install numbers go in `raspberry_pi/.env.example`. App numbers go in
    `config/settings.yaml`, with a typed field in `desktalk/config/app.py`. Wording goes in `config/prompts.yaml`, and
    each new blank goes in `BLANKS` in `desktalk/config/prompts.py`.
  - Use plain words, and `logging`, never `print`.
  - `uv run ruff check <changed files>`, `uv run pyright` (0 errors) and `uv run vulture` (prints nothing) must all
    pass, and coverage may only go up.
- install.sh's style:
  - It's safe to run again.
  - It never touches the data unless it's moving it.
  - It asks for sudo only where needed, and adds a `later` line saying what to run by hand when sudo fails.
  - `--dry-run` changes nothing.
- Default sizes (from the spec): the space is **8192 MB**, the smallest space is **1024 MB**, and the free space kept
  on the card for a rebuild is the existing `INSTALL_MIN_DISK_BUILT_MB`.
- The space's blocks are all allocated up front (`fallocate`, never a sparse file), and that's checked afterwards.
- History is never lost. During the move, the old data folder stays exactly as it was until the copy has been
  checked, and it's then kept, renamed, until the person deletes it.
- Nothing can be run on the Pi from here. Each Pi step that's handed back to the person gets **On the Pi** commands.

## Names every task shares

| Name | Where it's set | Value / meaning |
|---|---|---|
| `DATA_ROOM_SIZE_MB` | `.env.example` | `8192` |
| `DATA_ROOM_SMALLEST_MB` | `.env.example` | `1024` |
| `DATA_ROOM_FILE` | `.env.example` | `../data.img` (relative to `raspberry_pi/`, like `DESKTALK_DATA`) |
| `DATA_ROOM_MARKER` | `.env.example` | `.desktalk-data-room`: a file inside the image, written once the move is checked |
| `DATA_ROOM_ON` | `.env.example` `no`. install.sh writes `yes` or `no` on every run | whether the data folder is the space |
| `APP_MEMORY_DATA_SIZE` | `.env.example` | `200m`: the in-memory data folder used in `off` mode |
| `INSTALL_DATA_ROOM_SERVICE_FILE` | `.env.example` | `/etc/systemd/system/desktalk-data-room.service` |
| `DATA_ROOM`, `DATA_ROOM_MARKER`, `MEMORY_DATA_FOLDER` | `compose.yaml` → the app's environment | `${DATA_ROOM_ON:-no}`, `${DATA_ROOM_MARKER:-.desktalk-data-room}`, `/memory-data` |
| `DESKTALK_SAVING` | `start.sh` → the app | `plain`, `room` or `off`. Missing means `plain` (a Mac) |
| `DESKTALK_DATA_FOLDER` | `start.sh` → the app, only with `off` | the folder the app writes to instead of `data/` |
| `Saving` | `desktalk/domain/health.py` | `StrEnum`: `PLAIN='plain'`, `ROOM='room'`, `OFF='off'` |
| health measures | `HealthChecks.measures()` | the new keys are `card_rest` and `saving` |

## Review Focus

1. **An image file is already there but isn't mounted** when install.sh runs. It must never make a new image over it
   (it may hold the only history). Task 5: `test_an_existing_image_is_never_made_again`.
2. **The mount fails right after the swap.** The old folder must go back, untouched. Task 5:
   `test_a_failed_first_mount_puts_the_old_folder_back`.
3. **The space is missing, so `data/` is the bare mount point.** `start.sh` must not `chown` it, or the guard that
   stops writes to the card is gone. Task 4: `test_off_mode_leaves_the_bare_mount_point_alone`.
4. **The repo folder's path has a space in it** (`/home/sam/my desk/desk-talk`). Every path in `data_room.sh` and the
   systemd unit must be quoted. Task 5: `test_works_with_a_space_in_the_folder_name`.
5. **An update on a Pi that already has the space** must not ask for sudo, stop containers or copy anything. Task 6:
   `test_an_update_with_the_room_in_place_changes_nothing`.

---

## Waves

- **Wave 1, all at once (no shared files):** Tasks 1, 2, 4, 5, 7, 8.
- **Wave 2:** Task 3 (needs 1 and 2) and Task 6 (needs 5).
- **Last:** the full checks, the PR and the review (Task 9).

---

### Task 1: The saving modes and the two new health checks

**Files:**
- Modify: `desktalk/domain/health.py`, `desktalk/domain/__init__.py` (export `Saving`)
- Modify: `desktalk/core/health/checks.py`
- Modify: `desktalk/config/app.py` (`HealthChecksSettings`), `config/settings.yaml` (`health_checks:`)
- Test: `tests/unit/test_core_health_checks.py`, `tests/contracts/test_health_parts_contract.py` (if it lists the keys)

**Interfaces:**
- Produces:
  - `Saving` (see the table above).
  - `HealthChecks(source, detections, disk, settings, pi_settings, *, clock, pi=None, saving: Saving = Saving.PLAIN,
    card: DiskSpace | None = None)`. `disk` is still the data folder; `card` is the rest of the SD card.
  - `measures()` returns, in this order: `camera`, `detections`, `pi_temperature`, `card`, `pi_heartbeat`,
    `card_rest`, `saving`.
- The new settings (`ZeroOrMore`, with `_in_order` pairs serious < warning):
  - `room_warning_below_mb: 300`
  - `room_serious_below_mb: 100`
  - `card_rest_warning_below_mb: 2000`
  - `card_rest_serious_below_mb: 500`

  Each gets a plain comment in `settings.yaml`, for example "Desk Talk's own space on the Pi's SD card (raspberry_pi/data_room.sh)".

- [ ] **Step 1: Write the failing tests** in `test_core_health_checks.py`:
  - `test_plain_mode_measures_the_card_as_before`: `card` uses `card_*` levels; `card_rest` is `UNKNOWN`; `saving` is `OK`.
  - `test_room_mode_measures_the_room_with_its_own_levels`: data folder free 250 → `card` `WARNING`, 50 → `SERIOUS`, 400 → `OK`.
  - `test_room_mode_measures_the_rest_of_the_card`: `card` DiskSpace free 1500 → `card_rest` `WARNING` with value 1500.0, 400 → `SERIOUS`.
  - `test_off_mode_says_nothing_is_saved`: `saving` is `SERIOUS`; `card` is `UNKNOWN` (the data folder is in memory); `card_rest` is still measured.
  - `test_unreadable_rest_of_the_card_is_unknown`: `card.free_mb()` returns None → `card_rest` `UNKNOWN`.
  - A settings test (in this file, or wherever `HealthChecksSettings` order is tested) that `room_serious_below_mb >= room_warning_below_mb` is refused.
- [ ] **Step 2: Run** `uv run pytest tests/unit/test_core_health_checks.py`. Expected: FAIL (no `Saving`, unknown keyword `saving`).
- [ ] **Step 3: Implement** `Saving` and the measures (`_card()` picks its levels by mode; new `_card_rest()`, `_saving()`), and add the settings and fields. Update the module docstring's list of checks.
- [ ] **Step 4: Run** the same tests, then `uv run pytest tests/contracts tests/unit/test_config*.py -q`. Expected: PASS.
- [ ] **Step 5: Commit**: "Add saving modes and a check for the rest of the SD card".

### Task 2: What Lens says

**Files:**
- Modify: `desktalk/core/chat/workers/lens.py`, `config/prompts.yaml` (`lens:`), `desktalk/config/prompts.py` (`LensPrompts`, its `pi_fixed` model, `BLANKS`)
- Test: `tests/unit/test_core_workers_lens.py`

**Interfaces:**
- Consumes: `Saving` and the measure keys from Task 1. Import `Saving` from `desktalk.domain`. If Task 1 hasn't landed
  yet, test against `HealthMeasure` objects built by hand.
- Produces: the new keyword argument `Lens(..., saving: Saving = Saving.PLAIN)`.
- New prompts (single strings: the space exists only on the Pi), with blanks:
  - `lens.room_low` and `lens.room_full` (`free_mb`)
  - `lens.card_rest_low` and `lens.card_rest_full` (`free_mb`)
  - `lens.saving_off` (no blanks)
  - `lens.pi_fixed.room`, `lens.pi_fixed.card_rest`, `lens.pi_fixed.saving`

  The wording must say that Desk Talk **is still saving** when the rest of the card is full, and that updates and the
  Pi's own logs need room. `saving_off` must say that nothing is being saved right now, and point to "If Desk Talk's
  space is missing" in `raspberry_pi/README.md`.

- [ ] **Step 1: Write the failing tests:**
  - `test_room_mode_uses_the_room_words_for_the_card_check`
  - `test_the_rest_of_the_card_running_low_says_saving_goes_on`: the text holds `room` / `card_rest` words, with `free_mb` filled in.
  - `test_saving_off_is_said_once_and_ranked_serious`
  - `test_plain_mode_words_are_unchanged`
- [ ] **Step 2: Run** `uv run pytest tests/unit/test_core_workers_lens.py`. Expected: FAIL.
- [ ] **Step 3: Implement:**
  - New `_PiWatch` entries for `card_rest` and `saving`.
  - `_card_text` picks `room_*` words when `saving is Saving.ROOM`, and the `card` watch's fixed words then come from `pi_fixed.room`.
  - New `_card_rest_text` and `_saving_text`.
- [ ] **Step 4: Run** the Lens tests and `uv run pytest tests/unit/test_config_prompts*.py -q` (the prompts/BLANKS checks). Expected: PASS.
- [ ] **Step 5: Commit**: "Let Lens talk about Desk Talk's own space and the rest of the SD card".

### Task 3: Wire the mode through the app (after Tasks 1 and 2)

**Files:**
- Create: `desktalk/runtime/saving.py`
- Modify: `desktalk/bootstrap.py` (`build`), `server.py` (`main`, `start_logging`), `desktalk/web/routers/health.py` (its docstring's list of checks)
- Modify: `tests/characterization/snapshots/health.json`, `health-phone-camera.json`, `health-after-pi-heartbeat.json`
- Test: `tests/unit/test_runtime_saving.py` (new), `tests/integration/test_app_build.py`, `tests/unit/test_server_startup_lines.py`

**Interfaces:**
- Produces: `saving_from_environment(environ: Mapping[str, str]) -> tuple[Saving, Path | None]` in `desktalk/runtime/saving.py`:
  - It reads `DESKTALK_SAVING` and `DESKTALK_DATA_FOLDER`.
  - A missing value means `PLAIN`.
  - An unknown value means `PLAIN`, with a logged warning.
  - `OFF` with no folder logs a warning and returns `(Saving.OFF, None)`, and `build` then uses a `tempfile.mkdtemp()` folder.
- `build(..., saving: Saving = Saving.PLAIN)`:
  - With `OFF`, everything that `data_path` resolves, plus `storage.rules_folder`, goes under `data_folder`.
  - It passes `saving` to `HealthChecks` and `Lens`.
  - When `saving` isn't `PLAIN`, it passes `card=FolderDiskSpace(Path('/'))`. That `/` is the container's own root,
    which Docker keeps on the SD card; name it as a constant with a comment.
- `server.main()` calls `saving_from_environment(os.environ)` and gives the folder to `start_logging` and `build`. With
  `OFF`, it adds one start-up line saying nothing is being saved.

- [ ] **Step 1: Write the failing tests:**
  - `test_runtime_saving.py`: the four cases above.
  - `test_app_build.py::test_off_mode_writes_only_to_the_given_folder`: `build` with `saving=Saving.OFF` and `data_folder=tmp/a`. After `start()`, publish one event and `stop()`. Nothing is written under the project's `data/` or `rules_folder`, and `tmp/a` has the event log.
  - `test_app_build.py::test_room_mode_adds_the_rest_of_the_card_check`: `/api/health`-style `measures()` include `card_rest` with a number.
  - `test_server_startup_lines.py::test_off_mode_says_nothing_is_saved_at_start`
- [ ] **Step 2: Run** those files. Expected: FAIL.
- [ ] **Step 3: Implement** the pieces above, then update the three health snapshots (they gain `card_rest: unknown` and `saving: ok`).
- [ ] **Step 4: Run** `uv run pytest tests/unit/test_runtime_saving.py tests/integration/test_app_build.py tests/unit/test_server_startup_lines.py tests/characterization -q`. Expected: PASS.
- [ ] **Step 5: Commit**: "Pick the data folder and health checks from the saving mode start.sh gives".

### Task 4: start.sh decides the mode

**Files:**
- Modify: `raspberry_pi/app/start.sh`
- Test: `tests/unit/test_pi_app_start_script.py`

**Interfaces:**
- Consumes: the `DATA_ROOM`, `DATA_ROOM_MARKER` and `MEMORY_DATA_FOLDER` environment from `compose.yaml` (Task 5).
- Produces: it runs the app with `DESKTALK_SAVING` set, plus `DESKTALK_DATA_FOLDER` when the mode is `off`.

| `DATA_ROOM` | `data/$DATA_ROOM_MARKER` | Mode | `chown data` and the safety rules copy |
|---|---|---|---|
| not `yes` | any | `plain` | as today |
| `yes` | there | `room` | as today |
| `yes` | missing | `off`, with `DESKTALK_DATA_FOLDER=$MEMORY_DATA_FOLDER` (or `mktemp -d` if that's unset, saying so) | **skipped**. Print one plain line saying the space isn't mounted and nothing is saved |

- [ ] **Step 1: Write the failing tests.** The stand-in app prints `$DESKTALK_SAVING $DESKTALK_DATA_FOLDER`:
  - `test_without_the_room_the_mode_is_plain`
  - `test_with_the_room_mounted_the_mode_is_room`
  - `test_off_mode_leaves_the_bare_mount_point_alone`: the `chown` stand-in logs its calls, and `data` isn't among them. No `safety_rules.json` is made in `data/`.
  - `test_off_mode_uses_the_memory_folder`
  - `test_off_mode_without_a_memory_folder_still_starts`
- [ ] **Step 2: Run** `uv run pytest tests/unit/test_pi_app_start_script.py`. Expected: the new tests FAIL, and the old ones pass.
- [ ] **Step 3: Implement** the table above. Keep the header comment's promise that `docker compose up` works on a new Pi (the default is `plain`).
- [ ] **Step 4: Run** the same tests. Expected: PASS.
- [ ] **Step 5: Commit**: "Have the app's start script tell it whether its own space is there".

### Task 5: data_room.sh, its systemd unit, and the settings

**Files:**
- Create: `raspberry_pi/data_room.sh`, `raspberry_pi/systemd/desktalk-data-room.service`
- Modify: `raspberry_pi/.env.example` (the names in the table, each with a plain comment, in the install script's part), `raspberry_pi/compose.yaml` (the app's `environment` and `tmpfs`)
- Test: `tests/unit/test_pi_data_room_script.py` (new, with stand-ins in the style of `test_pi_install_script.py`'s `STAND_IN`: `df`, `stat`, `findmnt`, `fallocate`, `mkfs.ext4`, `e2fsck`, `mount`, `umount`, `losetup`, `resize2fs`, `sudo`, `systemctl`), `tests/unit/test_pi_compose_file.py`

**Interfaces:**
- Produces: `bash raspberry_pi/data_room.sh status` prints exactly one word:
  - `room`: the data folder is the mounted image, with the marker inside.
  - `elsewhere`: the data folder is on another disk than `/`, so it's left alone.
  - `unmounted`: the image file exists but isn't mounted.
  - `none`: there's no image yet.
- Produces: `bash raspberry_pi/data_room.sh make [--dry-run]`. install.sh calls it with the containers stopped.
  - `room`: it grows the image if `DATA_ROOM_SIZE_MB` is now bigger (`fallocate -l`, `sudo losetup -c`, `sudo resize2fs`). If the setting is now smaller, it says shrinking isn't supported and leaves it. Otherwise it does nothing.
  - `elsewhere`: it says so and exits 0.
  - `unmounted`: it starts the unit once. If the image still isn't mounted, it **stops with a message and never makes a new image**.
  - `none`, in this order:
    1. Size = min(`DATA_ROOM_SIZE_MB`, free MB − `INSTALL_MIN_DISK_BUILT_MB`). If that's below `DATA_ROOM_SMALLEST_MB`, stop and say how much to free.
    2. `fallocate` `<image>.new`, and check that its allocated bytes (`stat -c '%b %B'`) are at least the size.
    3. `mkfs.ext4 -q -F -m 0 -E root_owner=<uid>:<gid>`.
    4. `sudo mount -o loop` it on a temporary folder.
    5. `cp -a data/. <temp>/`, check with `diff -r`, write the marker, and `sudo umount`.
    6. Rename `data` to `data.before-room-<date-time>`, make an empty `data` owned by root with mode 755, and rename `<image>.new` to the image.
    7. Install the unit (`@IMAGE@` and `@DATA@` filled in, as install.sh's `fill_in` does), then `daemon-reload`, `enable` and `start` it.
    8. Check that `status` is now `room`. If not, undo: stop and disable the unit, remove the empty `data`, rename the old folder back, and remove the image.
    9. Print where the old folder is and how to delete it.

  A failure at steps 1 to 5 only removes `<image>.new`.
- The unit:
  - `Type=oneshot`, `RemainAfterExit=yes`, `Before=docker.service`, `WantedBy=multi-user.target`.
  - `ExecStart` runs `e2fsck -p` on the image and mounts it (`loop,noatime`) only when e2fsck's exit code is below 4. It never uses `-y`.
  - `ExecStop` unmounts it.
- In `compose.yaml`, the app gets `DATA_ROOM`, `DATA_ROOM_MARKER` and `MEMORY_DATA_FOLDER`, and
  `tmpfs: - /memory-data:size=${APP_MEMORY_DATA_SIZE:-200m},uid=${PI_USER_ID:-1000},gid=${PI_GROUP_ID:-1000},mode=0700`.

- [ ] **Step 1: Write the failing tests:**
  - `test_status_says_none_room_elsewhere_or_unmounted`
  - `test_makes_a_room_of_the_set_size_and_moves_the_data_in`: the old files, SQLite `-wal` and `-shm` included, are in the image. The old folder is kept, renamed.
  - `test_a_card_too_small_gets_a_smaller_room`
  - `test_below_the_smallest_room_it_stops_and_changes_nothing`
  - `test_a_sparse_or_short_image_is_refused`: the `stat` stand-in reports fewer blocks.
  - `test_a_failed_copy_check_leaves_the_data_folder_as_it_was`
  - `test_an_existing_image_is_never_made_again`
  - `test_a_failed_first_mount_puts_the_old_folder_back`
  - `test_grows_when_the_size_goes_up_and_refuses_to_shrink`
  - `test_a_data_folder_on_another_disk_is_left_alone`
  - `test_a_dry_run_changes_nothing`
  - `test_works_with_a_space_in_the_folder_name`
  - `test_the_unit_repairs_only_safely_and_mounts_before_docker`: read the template and check `-p`, no `-y`, `Before=docker.service`, and that the blanks are quoted.
  - In `test_pi_compose_file.py`: `test_the_app_gets_the_room_settings_and_a_memory_folder`. The existing check that every `${NAME:-default}` matches `.env.example` covers the defaults.
- [ ] **Step 2: Run** `uv run pytest tests/unit/test_pi_data_room_script.py tests/unit/test_pi_compose_file.py`. Expected: FAIL.
- [ ] **Step 3: Implement** the script, the unit, the settings and the compose lines. Reuse the `setting` and `fill_in` helpers' approach from install.sh, copied into the script (install.sh's functions aren't sourced).
- [ ] **Step 4: Run** the same tests. Expected: PASS.
- [ ] **Step 5: Commit**: "Add data_room.sh to give Desk Talk its own fixed space on the SD card".

### Task 6: install.sh and update.sh use it (after Task 5)

**Files:**
- Modify: `raspberry_pi/install.sh` (new `set_up_data_room` after `start_containers` in `main`; `sudo_to_set_up` also asks when `status` isn't `room` or `elsewhere`; `set_env DATA_ROOM_ON yes|no` on every run)
- Test: `tests/unit/test_pi_install_script.py` (the fake Pi copies `data_room.sh` and the new unit; new stand-ins as in Task 5), `tests/unit/test_pi_update_script.py`

**Interfaces:**
- Consumes: `data_room.sh status|make [--dry-run]` from Task 5.
- `set_up_data_room` does this:
  - It asks `status`.
  - On `none` or `unmounted`, if it's allowed sudo (not an update run without `SETUP_SUDO`), it runs `docker compose stop app camera`, then `data_room.sh make`, then `docker compose up -d --no-build`. If it isn't allowed sudo, it adds a `later` line starting "IMPORTANT:".
  - On `room`, it runs `make` (which only grows, or does nothing) with no stop.
  - It writes `DATA_ROOM_ON`.
  - A dry run passes `--dry-run`.

- [ ] **Step 1: Write the failing tests:**
  - `test_a_first_install_makes_the_room_and_restarts_the_app_on_it`
  - `test_an_update_with_the_room_in_place_changes_nothing`: no `sudo`, no `compose stop`, `DATA_ROOM_ON=yes` kept.
  - `test_an_update_without_the_room_asks_for_sudo_once`
  - `test_without_sudo_it_says_what_to_run_later`
  - `test_a_data_folder_elsewhere_sets_data_room_on_no`
  - `test_a_dry_run_says_it_would_make_the_room`
  - `update.sh` passes through (in `test_pi_update_script.py`).
- [ ] **Step 2: Run** `uv run pytest tests/unit/test_pi_install_script.py tests/unit/test_pi_update_script.py`. Expected: the new tests FAIL, and the old ones pass.
- [ ] **Step 3: Implement** the steps above, and update the header comment's list of what it sets up.
- [ ] **Step 4: Run** the same tests. Expected: PASS.
- [ ] **Step 5: Commit**: "Have install.sh give Desk Talk its own space and keep it on updates".

### Task 7: Developer page labels

**Files:**
- Modify: `dev_ui/src/api.ts` (the `checks` record type gains `'card_rest' | 'saving'`), `dev_ui/src/health.ts` (labels, for example `['card_rest', 'free space on the rest of the card']` and `['saving', 'saving history']`; `card` becomes `'free space where history is saved'`)
- Test: `dev_ui/src/health.test.ts`, `dev_ui/src/tabs/System.test.tsx`

- [ ] **Step 1: Write the failing test** `health.test.ts`: the rows include the two new checks with their labels and levels.
- [ ] **Step 2: Run** `npm test` in `dev_ui/`. Expected: FAIL.
- [ ] **Step 3: Implement** the labels and the type.
- [ ] **Step 4: Run** `npm test` and `npm run build` in `dev_ui/`. Expected: PASS, and the build succeeds.
- [ ] **Step 5: Commit**: "Show the saving and rest-of-card checks on the developer page".

### Task 8: Docs

**Files:**
- Modify: `raspberry_pi/README.md` (new section "Desk Talk's own space on the SD card", covering what it is, how to change its size, growing it, the old folder, and the two checks; plus "If Desk Talk's space is missing", with **On the Pi** commands: `systemctl status desktalk-data-room`, `sudo e2fsck -f ~/desk-talk/data.img`, `sudo systemctl start desktalk-data-room`, `cd ~/desk-talk/raspberry_pi && docker compose restart app`)
- Modify: `docs/PI_CHECKS.md`:
  - In the full-card test, filling the card now gives the rest-of-card warning, and history keeps being saved.
  - Its undo step checks that `~/desktalk-fill` is gone (`ls ~/desktalk-fill` says "No such file").
  - A new hand test, "Desk Talk's space is missing": stop the unit, restart the app, hear the save-nothing warning, start the unit, restart the app.
- Modify: `docs/ARCHITECTURE.md` (one paragraph on the three saving modes, in its failure table)

- [ ] **Step 1: Write** the sections, using the names in the shared table.
- [ ] **Step 2: Check** that every command and name in them exists in Tasks 4 to 6 (`grep` for each `.env` name in `raspberry_pi/.env.example` once Task 5 has landed).
- [ ] **Step 3: Commit**: "Explain Desk Talk's own space on the SD card".

### Task 9: Full checks, PR, review

- [ ] **Step 1: Run** these, and show their output:
  - `uv run pytest -m "not slow"`
  - `uv run pytest -m "not slow and not docker and not e2e" --cov`
  - `uv run ruff check <changed files>`
  - `uv run pyright`
  - `uv run vulture`
  - `npm test` and `npm run build` in `dev_ui/`

  Expected: all pass, with coverage at or above `fail_under`. Detection didn't change, so skip `slow`. `docker` and `e2e` don't apply.
- [ ] **Step 2:** Push `data-room` and open a PR into `pi-reliability`.
- [ ] **Step 3:** Start a subagent to run `/code-review high <PR number>`, then a second subagent to fix what it finds. Re-run Step 1, and push to the same PR.
- [ ] **Step 4:** Give the person the **On the Pi** steps:
  - `cd ~/desk-talk && git fetch && git checkout data-room`
  - `bash raspberry_pi/install.sh --dry-run`
  - `bash raspberry_pi/install.sh`
  - `df -h ~ && ls ~/desk-talk`, which should show `data.img` and `data.before-room-*`
  - the two hand tests in `docs/PI_CHECKS.md`

  Don't say the Pi part works until they report back.
