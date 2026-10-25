# Developer Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

Date: 2026-10-24. Status: **draft, waiting for your OK.** No code has changed yet.

**Goal:** A developer page at `http://localhost:5000/dev/` where Sam can run the tests, watch pictures turn into object states, see how the Pi and every part of the app are doing, look inside the database, and see what's stored, all without asking an agent.

**Architecture:** A new Vite + TypeScript + React app in `dev_ui/`. `npm run build` puts it in `dev_ui/dist/`, and the main server shows it at `/dev/`. While working on it, `npm run dev` serves it from Vite and passes `/api` calls to the running server. The server gets one new router, `desktalk/web/routers/dev.py` (`/api/dev/...`), which only answers requests from this computer. Behind it are four new parts, each behind an interface in `ports.py`: a test runner, a read-only SQLite browser, a data-folder viewer, and a smoothing trace kept by the object state tracker. Everything else the page shows (`/api/health`, `/api/log/...`) already exists and is reused as it is.

**Tech Stack:** Python 3.12, FastAPI (the existing server), SQLite (`sqlite3`, standard library), pytest. Page: Vite 5, TypeScript 5, React 18, uPlot (charts), Vitest + Testing Library (page tests), the `yaml` npm package (so `vite.config.ts` reads the port from `config/settings.yaml`). Node 22 and npm 10 are already on this Mac.

**Spec:** Sam's request of 2026-10-24, copied under "What you asked for" below.

## What you asked for

1. one button test
2. each unit test
3. observability (successfully showing observation state event transitions, raspberry pi stats, smoothing data, other possible metrics that show the system is operational)
4. mini database UI that can check database tables and run simple queries
5. local storage (what's being stored in local storage)
6. use vite (typescript)
7. tell me what stack you used for the current user UI

## Answer to 7: the stack of the current app page

- **The page (`web_app/`) has no framework and no build step:** plain HTML (`index.html`), one CSS file (`style.css`, about 680 lines) and one plain JavaScript file (`app.js`, about 1,500 lines), plus SVG drawings in `web_app/art/` and the Bricolage Grotesque font. The phone camera page (`phone_camera/`) is the same kind of thing.
- **How it talks to the server:** `fetch()` for JSON (`/api/live`, `/api/log/...`, `/api/settings`...), **Server-Sent Events** for the chat (`GET /api/events`), and **MJPEG** for the live picture (`/api/video_feed...`).
- **What it keeps in the browser:** `localStorage`, through a small `storage.load/save` wrapper in `app.js`. The keys are `desktalk_chats_v2` (chat history), `desktalk_todo_v1` (Paper's to-do list) and `desktalk_habits_v2` (habit counters). The phone page keeps `desktalk_extra_turn` and `desktalk_motion_sign`.
- **The server:** on `main` it's **Flask**. Since the refactor (PR #3 on `origin/main`, and PR #7 `cleanup`) it's **FastAPI + uvicorn**, serving `web_app/` as static files. GitHub Pages also publishes `web_app/` in demo mode.

The developer page is a separate app in `dev_ui/`, so the user page stays plain JavaScript and doesn't get a build step.

## Before you start: which code this builds on

The object states and the smoothing (items 3 and 4) only exist in **PR #6 (`object-states`)**. The routers, `FromHere`, `test_layers.py` and the e2e tests this plan uses only exist in **PR #7 (`cleanup`)**. PR #7 already says PR #6 must be rebased onto it.

So: **merge PR #7, rebase PR #6 (and PR #8) onto it, merge them, then branch `dev-ui` off `main`.** The file paths below are the ones after that, e.g. `desktalk/config/app.py` (not `desktalk/config.py`). The object state files keep their PR #6 paths: `desktalk/core/object_states/{smoothing,tracker}.py` and `desktalk/adapters/storage/object_states_db.py`. If a name moved in the rebase, use the new name; the Interfaces blocks say what each piece needs.

## Global Constraints

- Every number, path, command and threshold goes in `config/settings.yaml` (with a plain comment) and a typed field in `desktalk/config/app.py`. The page gets its numbers from `GET /api/dev/settings` and keeps no copies.
- Every `/api/dev/...` route answers **only requests from this computer** (`FromHere`): anything else gets a 403. Requests whose `Origin` header is set and isn't this server's own (`http://localhost:<web.port>`, `http://127.0.0.1:<web.port>`) or the Vite dev server's (`http://localhost:<dev.vite_port>`) also get a 403, so a web page open in your browser can't start runs. The main server lets any origin ask (CORS `*`); this check is on top of that.
- With `dev.enabled: false` the router isn't added and `/dev/` is a 404.
- The page never changes anything except: starting and stopping a test run, and deleting one localStorage key after you confirm. The database is opened read-only. The data folder is read-only.
- Commands only come from `config/settings.yaml` (`dev.checks`, `dev.test_command`). A request only picks a check by name or a test by an id that was collected. No shell: `subprocess` gets a list.
- One test run at a time.
- Parts talk through `ports.py`. Each new interface gets a fake in `tests/fakes/` and a contract suite in `tests/contracts/`. `tests/unit/test_layers.py` must still pass: the web layer imports only interfaces, plain data, settings and the app.
- Python: `uv run ...` only. Page: `npm` in `dev_ui/`, with `package-lock.json` committed. Pin exact versions (no `^`).
- Plain words in code, comments, the page's text and commit messages ("pictures", "the Pi").
- The server's messages for these routes ("A run is already going.", the refusals, "not built yet") go in `config/prompts.yaml` under `dev:`; any blank they fill in also goes in `BLANKS` in `desktalk/config/prompts.py`. Labels inside the page itself (tab names, buttons) stay in `dev_ui/`: it's a tool for you, not the app's characters.
- Tests never need the network, an API key, a camera, a Pi or Docker (the opt-in `e2e` test in Task 13 aside).
- Coverage (`--cov`) may only go up, `uv run pyright` shows 0 errors, and `uv run vulture` prints nothing. The pytest plugin's hook function goes in `tests/vulture_allow.py`, because pytest calls it by name.

## Review Focus

1. **A request from another device on the Wi-Fi, or from a web page in your browser, tries to start a run or a query:** it gets a 403 and nothing runs. Tested in Task 6.
2. **A second "Run" while a run is going, or a test that hangs:** the second one gets a 409, "A run is already going". A run longer than `dev.run_timeout_min` is stopped and shows "timed out". Tested in Task 2 (runner) and Task 6 (route).
3. **A query that writes (`DELETE`, `INSERT`, `ATTACH`, two statements) or never ends:** it's refused, or stopped after `dev.sql_timeout_sec`, with a plain message, and the file is unchanged. Tested in Task 3.
4. **A file path outside the data folder** (`../../.env`, `../certs/pair-key.txt`, an absolute path, a symlink out): 400, never read. Tested in Task 4.
5. **A label with a slash, like `pen/pencil`, or a label never seen:** the smoothing trace works for `pen/pencil` (it's a query parameter, not part of the path), and an unknown label gives an empty list, not an error. Tested in Task 5 and Task 6.

---

## File map

```
config/settings.yaml                        + dev: section, + object_states.trace_pictures
desktalk/config/app.py                      + DevSettings, DevCheck, DevHealthLimits; ObjectStatesSettings.trace_pictures
desktalk/domain/dev.py                      new: TestItem, TestOutcome, RunStatus, SmoothingStep, TableInfo, QueryResult, DataFile
desktalk/ports.py                           + TestRunner, DatabaseBrowser, DataFiles, SmoothingTraceView
desktalk/adapters/dev/test_runner.py        new: PytestRunner (runs the checks and tests in a child process)
desktalk/adapters/dev/pytest_report.py      new: pytest plugin, one JSON line per finished test
desktalk/adapters/dev/sqlite_browser.py     new: SqliteBrowser (read-only)
desktalk/adapters/dev/data_files.py         new: DataFolder (list + preview, inside data/ only)
desktalk/core/object_states/smoothing.py    + MajorityWindow.votes()
desktalk/core/object_states/tracker.py      + the trace: SmoothingTraceView
desktalk/bootstrap.py                       builds the dev parts when dev.enabled
desktalk/web/deps.py                        + DevOnly dependency (this computer + origin check)
desktalk/web/routers/dev.py                 new: /api/dev/...
desktalk/web/routers/__init__.py            + dev.router when enabled
desktalk/web/app.py                         + /dev/ mount before the web_app mount
dev_ui/                                     new: the page (Vite + React + TypeScript)
tests/...                                   per task below
```

---

### Task 1: Settings, plain data and interfaces for the developer tools

**Files:**
- Modify: `config/settings.yaml`, `config/prompts.yaml` (the `dev:` messages), `desktalk/config/prompts.py`, `desktalk/config/app.py`, `desktalk/ports.py`, `desktalk/domain/__init__.py`
- Create: `desktalk/domain/dev.py`, `tests/fakes/dev.py`, `tests/contracts/dev_contracts.py`, `tests/contracts/test_dev_real_parts.py` (one empty class each for Tasks 2, 3 and 4 to fill in)
- Modify: `tests/fakes/__init__.py`, `tests/contracts/test_fakes_pass_contracts.py`
- Test: `tests/unit/test_config_dev.py`

**Interfaces:**
- Produces, in `desktalk/domain/dev.py` (frozen dataclasses):
  - `TestItem(id: str, file: str, markers: tuple[str, ...])`: `id` is pytest's node id, e.g. `tests/unit/test_x.py::test_y`.
  - `TestOutcome(id: str, outcome: str, duration_sec: float, message: str)`: `outcome` is `passed`, `failed`, `skipped` or `error`. `message` is the short failure text, or `''`.
  - `RunStatus(run_id: str, what: str, state: str, started: float, finished: float | None, steps: tuple[StepStatus, ...], outcomes: tuple[TestOutcome, ...], output: str, output_end: int)`: `state` is `running`, `passed`, `failed`, `stopped` or `timed out`. `output` is the text after the offset asked for, and `output_end` is the offset to ask from next time.
  - `StepStatus(name: str, state: str, exit_code: int | None)`.
  - `SmoothingStep(ts: float, conf: float | None, box_seen: bool, votes_yes: int, votes_total: int, seen: bool, center: tuple[float, float] | None, phase: str, heading: str | None, waiting_sec: float | None)`
  - `TableInfo(name: str, columns: tuple[str, ...], rows: int, sql: str)`, `QueryResult(columns: tuple[str, ...], rows: tuple[tuple, ...], truncated: bool, took_ms: float)`
  - `DataFile(path: str, bytes: int, modified: float, is_folder: bool)`, with `path` relative to the data folder.
- Produces, in `ports.py` (Protocols):
  - `TestRunner`: `collect() -> list[TestItem]`, `start_check() -> str`, `start_tests(ids: list[str]) -> str`, `status(run_id: str, output_from: int) -> RunStatus`, `stop(run_id: str) -> None`. `start_*` raises `RunAlreadyGoing` (new, in `desktalk/domain/dev.py`) while a run is going, and `start_tests` raises `ValueError` for an id not in the last `collect()`.
  - `DatabaseBrowser`: `databases() -> list[str]`, `tables(db: str) -> list[TableInfo]`, `query(db: str, sql: str) -> QueryResult`. It raises `QueryRefused(message)` (new) for anything that isn't one read-only statement, or that runs too long.
  - `DataFiles`: `list() -> list[DataFile]`, `preview(path: str) -> str`. It raises `ValueError` for a path outside the folder or a folder, and `FileNotFoundError` when nothing is there.
  - `SmoothingTraceView`: `traced_labels() -> list[str]`, `trace(label: str) -> list[SmoothingStep]` (oldest first, `[]` for an unknown label).
- Produces, in `desktalk/config/app.py`: `DevSettings` with the fields below, `DevCheck(name: str, command: list[str])`, `DevHealthLimits`, `AppConfig.dev`, and `ObjectStatesSettings.trace_pictures: int`.

- [ ] **Step 1: Write the failing settings test** `tests/unit/test_config_dev.py`:
  - `test_dev_settings_load`: `load_config` on the settings in git gives `config.dev.enabled is True`, `config.dev.checks[0].name == 'Fast tests'`, `config.dev.sql_max_rows == 500` and `config.object_states.trace_pictures == 90`.
  - `test_dev_check_needs_a_command`: a `checks` entry with `command: []` raises `ConfigError`.
  - `test_run_timeout_must_be_positive`: `run_timeout_min: 0` raises `ConfigError`.
- [ ] **Step 2: Run** `uv run pytest tests/unit/test_config_dev.py -v`. Expected: FAIL (no `dev` section).
- [ ] **Step 3: Add the settings.** Add to `config/settings.yaml` (comments as written):

```yaml
# ── Developer page (http://localhost:5000/dev/, only from this computer) ──
dev:
  enabled: true                 # false = no developer page and no /api/dev routes
  ui_folder: dev_ui/dist        # where `npm run build` in dev_ui/ puts the page
  vite_port: 5173               # `npm run dev` serves the page here while you work on it
  poll_sec: 1                   # how often the page asks for new numbers
  checks:                       # the "Run everything" button: these run one after the other
    - name: Fast tests
      command: [uv, run, pytest, -m, "not slow and not docker and not e2e", -o, "addopts=", -q]
    - name: Lint
      command: [uv, run, ruff, check, .]
    - name: Types
      command: [uv, run, pyright]
  test_command: [uv, run, pytest, -o, "addopts=", -q]    # one test or file: its id goes at the end
  collect_markers: "not docker and not e2e"   # which tests the list shows
  run_timeout_min: 15           # a run going longer than this is stopped
  output_keep_kb: 2000          # how much of a run's printout the page can scroll back through
  sql_max_rows: 500             # a query shows at most this many rows
  sql_timeout_sec: 5            # a query running longer than this is stopped
  file_preview_kb: 64           # how much of a file the storage tab shows
  health:                       # when the system tab turns a number amber or red
    pi_cpu_warn_c: 70
    pi_cpu_bad_c: 80
    pi_disk_warn_mb: 1000
    detect_warn_ms: 500
```

  Add `trace_pictures: 90  # the developer page shows each object's last this many pictures of smoothing` under `object_states:`. Then add the typed fields: `run_timeout_min`, `poll_sec` and `sql_timeout_sec` must be greater than 0, and `checks` and every `command` can't be empty.
- [ ] **Step 4: Add the plain data, interfaces and errors** from the Interfaces block. Export them from `desktalk/domain/__init__.py`.
- [ ] **Step 5: Add the fakes and contracts.** Fakes in `tests/fakes/dev.py`:
  - `FakeTestRunner(items, results)`: finishes a run as soon as `status()` is called.
  - `FakeDatabaseBrowser(tables: dict[str, list[TableInfo]], answers: dict[str, QueryResult])`.
  - `FakeDataFiles(files: dict[str, str])`.

  Contract suites in `tests/contracts/dev_contracts.py`, one class per interface. Each checks what the Interfaces block promises: `RunAlreadyGoing` on a second start, `ValueError` for an unknown test id, `QueryRefused` for `DELETE FROM x`, `ValueError` for `../x`, and `[]` for an unknown label. Run them on the fakes from `test_fakes_pass_contracts.py`.
- [ ] **Step 6: Run** `uv run pytest tests/unit/test_config_dev.py tests/contracts -v`. Expected: PASS.
- [ ] **Step 7: Commit:** `git commit -m "Add the settings, plain data and interfaces for the developer page"`

---

### Task 2: The test runner

**Files:**
- Create: `desktalk/adapters/dev/__init__.py`, `desktalk/adapters/dev/test_runner.py`, `desktalk/adapters/dev/pytest_report.py`
- Test: `tests/unit/test_adapters_dev_test_runner.py`, `tests/contracts/test_dev_real_parts.py` (the real-parts contract classes; Tasks 3 and 4 add theirs here too, one class each)

**Interfaces:**
- Consumes: `TestRunner`, `TestItem`, `TestOutcome`, `RunStatus`, `StepStatus`, `RunAlreadyGoing` and `DevSettings` (Task 1), and `Clock` (ports).
- Produces: `PytestRunner(settings: DevSettings, project: Path, clock: Clock)`, which implements `TestRunner`.

How it works:
- **`collect()`** runs `test_command + ['--collect-only', '-q', '-m', collect_markers]` and keeps the lines with `::`. It runs a second `--collect-only -q -m slow` to tag the slow tests with `markers=('slow',)`.
- **Each run** is one child process (`subprocess.Popen`, list args, `cwd=project`, stdout and stderr together, text). One plain thread reads its output into a buffer capped at `output_keep_kb`; the oldest text goes first.
- **Results per test:** add `-p desktalk.adapters.dev.pytest_report` to every pytest command, and set the environment variable `DESKTALK_DEV_REPORT=<temp file>`. The plugin's `pytest_runtest_logreport(report)` adds one JSON line per finished test: `{id, outcome, duration_sec, message}`, where `message` is the last line of `report.longreprtext`. The plugin must not import `desktalk` or `pytest`. `status()` reads the new lines, so the page sees each test as it finishes.
- **`start_check()`** runs `dev.checks` in order, one at a time. It keeps going after a failed step, so you see all three results. `state` is `passed` only if every step exited 0.
- **The time limit:** every `status()` call compares `clock.monotonic()` with when the run started, so a `FakeClock` can move it past `run_timeout_min` in a test.
- **Stopping:** `stop()` and the timeout end the process (`terminate`, then `kill` after 5 s; that 5 is `supervisor.stop_wait_sec`) and set `state` to `stopped` or `timed out`.

- [ ] **Step 1: Write the failing tests.** They use a small pytest project the test writes into `tmp_path`: one passing, one failing and one slow-marked test, with `settings.test_command = [sys.executable, '-m', 'pytest', '-q']`, so `uv` isn't needed.
  - `test_collect_lists_every_test_with_its_marks`: 3 items, and the slow one has `markers == ('slow',)`.
  - `test_running_one_file_reports_each_test`: `start_tests([file])`, poll `status` until it's no longer `running`. Then `state == 'failed'`, the outcomes are `{passed: 1, failed: 1}`, the failed one's `message` names the assert, and `'1 failed'` is in the output.
  - `test_output_comes_in_pieces`: the second `status(run_id, first.output_end)` returns only new text.
  - `test_a_second_run_waits_its_turn`: `start_tests` while running raises `RunAlreadyGoing`.
  - `test_unknown_test_id_is_refused`: `start_tests(['tests/x.py::nope; rm -rf /'])` raises `ValueError`, and no process starts.
  - `test_a_run_that_takes_too_long_is_stopped`: a test that sleeps, `run_timeout_min` set to 1/60, a `FakeClock` moved past the limit. Then `state == 'timed out'` and the process is gone.
  - `test_the_check_runs_every_step`: three checks (`sys.executable -c 'exit(0)'`, `-c 'exit(1)'`, `-c 'exit(0)'`). All three steps run, the states are `passed, failed, passed`, and the run is `failed`.
  - The `TestRunner` contract class (in `tests/contracts/test_dev_real_parts.py`) runs on `PytestRunner`.
- [ ] **Step 2: Run** `uv run pytest tests/unit/test_adapters_dev_test_runner.py -v`. Expected: FAIL (no module).
- [ ] **Step 3: Implement `PytestRunner`** and `pytest_report.py` as described above.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit:** `git commit -m "Add a test runner for the developer page"`

---

### Task 3: The read-only database browser

**Files:**
- Create: `desktalk/adapters/dev/sqlite_browser.py`
- Test: `tests/unit/test_adapters_dev_sqlite_browser.py`, plus its contract class in `tests/contracts/test_dev_real_parts.py`

**Interfaces:**
- Consumes: `DatabaseBrowser`, `TableInfo`, `QueryResult` and `QueryRefused` (Task 1).
- Produces: `SqliteBrowser(files: dict[str, Path], max_rows: int, timeout_sec: float, clock: Clock)`. Bootstrap passes `{'object_states': config.resolve(config.object_states.database)}`.

How it works: every call opens `sqlite3.connect(f'file:{path}?mode=ro', uri=True)` and runs `PRAGMA query_only = ON`. The time limit is a `set_progress_handler` that stops the query once `timeout_sec` has passed by the clock. Rows come from `fetchmany(max_rows + 1)`, and `truncated` is true when that extra row came back. Before running, a query is refused unless `sqlite3.complete_statement(sql)` is true and there is only one statement (`Connection.execute` already refuses two). Nothing else is filtered: the read-only connection is what keeps the file safe. A database file that isn't there yet shows up in `databases()` with 0 tables, not an error.

- [ ] **Step 1: Write the failing tests** on a temp file built with the real `SqliteStateStore` and three rows:
  - `test_tables_show_columns_and_row_counts`: `events` has 11 columns and 3 rows, and its `sql` contains `append-only`.
  - `test_a_select_returns_rows`: `SELECT object, type FROM events ORDER BY ts` gives 3 rows and the columns `('object', 'type')`.
  - `test_rows_are_capped`: with `max_rows=2`, you get 2 rows and `truncated is True`.
  - Parametrized `test_writing_is_refused` over `DELETE FROM events`, `INSERT INTO events ...`, `DROP TABLE events`, `ATTACH 'x.db' AS x`, `SELECT 1; DELETE FROM events`: each raises `QueryRefused`, and the file's bytes are unchanged.
  - `test_a_slow_query_is_stopped`: built with the real clock (`desktalk_shared.clock.SystemClock`) and `timeout_sec=0.2`. A recursive CTE that never ends raises `QueryRefused` and names `sql_timeout_sec`.
  - `test_a_missing_database_has_no_tables`.
  - The contract class runs on `SqliteBrowser`.
- [ ] **Step 2: Run** `uv run pytest tests/unit/test_adapters_dev_sqlite_browser.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement `SqliteBrowser`.**
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit:** `git commit -m "Add a read-only database browser for the developer page"`

---

### Task 4: The data folder viewer

**Files:**
- Create: `desktalk/adapters/dev/data_files.py`
- Test: `tests/unit/test_adapters_dev_data_files.py`, plus its contract class in `tests/contracts/test_dev_real_parts.py`

**Interfaces:**
- Consumes: `DataFiles` and `DataFile` (Task 1).
- Produces: `DataFolder(folder: Path, preview_bytes: int)`. Bootstrap passes the app's data folder (the one `data_path()` resolves against) and `dev.file_preview_kb * 1024`.

How it works:
- `list()` walks the folder: event log days, `object_states.sqlite`, the JSON stores, logs.
- `preview()` resolves the path, and refuses it unless `resolved.is_relative_to(folder.resolve())`. That one check covers `..`, absolute paths and symlinks pointing out. It reads at most `preview_bytes`, as UTF-8 with `errors='replace'`. A `.sqlite` file isn't read: the message points to the database tab.

- [ ] **Step 1: Write the failing tests:**
  - `test_list_shows_files_with_sizes`.
  - `test_preview_reads_the_start_of_a_file`: a 200 KB file with a 64 KB limit returns 64 KB.
  - Parametrized `test_paths_outside_are_refused` over `../.env`, `/etc/passwd`, and a symlink in the folder pointing to `tmp_path/secret.txt`: each raises `ValueError`.
  - `test_missing_file`: raises `FileNotFoundError`.
  - The contract class runs on `DataFolder`.
- [ ] **Step 2: Run** `uv run pytest tests/unit/test_adapters_dev_data_files.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement `DataFolder`.**
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit:** `git commit -m "Add a data folder viewer for the developer page"`

---

### Task 5: The smoothing trace in the object state tracker

**Files:**
- Modify: `desktalk/core/object_states/smoothing.py`, `desktalk/core/object_states/tracker.py`
- Test: `tests/unit/test_core_object_states_smoothing.py`, `tests/unit/test_core_object_states_tracker.py` (add tests to both)

**Interfaces:**
- Consumes: `SmoothingStep` and `SmoothingTraceView` (Task 1), and `ObjectStatesSettings.trace_pictures`.
- Produces:
  - `MajorityWindow.votes() -> tuple[int, int]`: (yes votes, pictures in the window).
  - `ObjectStateTracker.traced_labels()` and `ObjectStateTracker.trace(label)`, so the tracker now implements `SmoothingTraceView` as well as `ObjectStatesView`.

How it works:
- In `on_detection`, after `step()`, append one `SmoothingStep` per label to `self._trace[label]`, a `deque(maxlen=settings.trace_pictures)`, under the existing lock.
  - `conf` is the picked box's confidence, or None.
  - `box_seen` is the hysteresis answer, and `votes_yes`/`votes_total` come from `votes()`.
  - `seen` is the majority answer, and `center` the median.
  - `phase` and `heading` come from the new state.
  - `waiting_sec` is `ts - pending_since`, or None.
- `trace()` returns a copy, oldest first.

- [ ] **Step 1: Write the failing tests:**
  - `test_votes_count_the_window` (smoothing): add True, False, True to a window of 5, and `votes()` gives `(2, 3)`.
  - `test_trace_follows_each_picture` (tracker): 4 pictures of a `cup` at 0.9, then 2 without. Gives 6 steps, the last one with `conf is None`, `box_seen is False`, `votes_yes == 4`, `votes_total == 5` and `heading == 'ABSENT'`.
  - `test_trace_keeps_only_the_last_pictures`: with `trace_pictures=3`, 10 pictures leave 3 steps.
  - `test_trace_of_a_label_with_a_slash`: `pen/pencil` is traced, and appears in `traced_labels()`.
  - `test_trace_of_an_unknown_label_is_empty`.
- [ ] **Step 2: Run** `uv run pytest tests/unit/test_core_object_states_smoothing.py tests/unit/test_core_object_states_tracker.py -v`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement** `votes()` and the trace.
- [ ] **Step 4: Run** the same command. Expected: PASS, the old tests included.
- [ ] **Step 5: Commit:** `git commit -m "Keep each object's last pictures of smoothing for the developer page"`

---

### Task 6: Wire it up, the /api/dev routes, and the /dev/ page

**Files:**
- Modify: `desktalk/bootstrap.py`, `desktalk/web/deps.py`, `desktalk/web/routers/__init__.py`, `desktalk/web/app.py`
- Create: `desktalk/web/routers/dev.py`, `tests/integration/test_web_dev.py`
- Modify: `tests/characterization/cases.py`, plus the new snapshots in `tests/characterization/snapshots/`

**Interfaces:**
- Consumes: Tasks 1 to 5.
- Produces:
  - New `DeskTalk` attributes `test_runner: TestRunner | None`, `databases: DatabaseBrowser | None` and `data_files: DataFiles | None`, all None when `dev.enabled` is false. `object_states` is now typed as also implementing `SmoothingTraceView`.
  - The routes below (the page in Tasks 7 to 12 relies on these exact paths and shapes):

| Route | Reply |
|---|---|
| `GET /api/dev/settings` | `{poll_sec, checks: [names], health: {...}, trace_pictures, sql_max_rows, data_folder, object_states: {on_confidence, off_confidence, appear_sec, gone_sec, window_pictures, vote_share}}` |
| `GET /api/dev/states?since=<ts>` | `{objects: {label: {state, since}}, watched_since, changes: [{ts, label, change}]}` from `object_states.snapshot()` and `changes(since)` |
| `GET /api/dev/smoothing` | `{labels: [...]}` |
| `GET /api/dev/smoothing/trace?label=<label>` | `{label, steps: [SmoothingStep as dict]}` |
| `GET /api/dev/tests` | `{tests: [TestItem as dict]}` |
| `POST /api/dev/runs` with `{"check": true}` or `{"tests": [ids]}` | `{run_id}`. 409 `{"error": "A run is already going."}`; 400 for an unknown id |
| `GET /api/dev/runs/{run_id}?from=<offset>` | `RunStatus` as dict; 404 for an unknown run |
| `POST /api/dev/runs/{run_id}/stop` | `{ok: true}` |
| `GET /api/dev/db` | `{databases: [...]}` |
| `GET /api/dev/db/{name}/tables` | `{tables: [TableInfo as dict]}` |
| `POST /api/dev/db/{name}/query` with `{"sql": "..."}` | `QueryResult` as dict; 400 `{"error": <QueryRefused message>}` |
| `GET /api/dev/files` | `{files: [DataFile as dict]}` |
| `GET /api/dev/files/preview?path=<path>` | `{path, text}`; 400 outside the folder, 404 missing |

How it works:
- `DevOnly` in `deps.py` combines the `FromHere` check with the origin check from Global Constraints. `APIRouter(dependencies=[Depends(dev_only)])`.
- The router is only in `MAIN_SERVER` when `config.dev.enabled` is true, so `create_app` adds it conditionally. It is never on the phone server.
- `app.py` mounts `/dev` as `PageFiles(directory=config.resolve(config.dev.ui_folder), html=True)` before the `web_app` mount. If that folder isn't there, `GET /dev/` answers a plain HTML line: "The developer page isn't built yet. In dev_ui/, run: npm install && npm run build".
- Bootstrap builds `PytestRunner(config.dev, config.root, clock)`, `SqliteBrowser(...)` and `DataFolder(...)` when the developer page is on.

- [ ] **Step 1: Write the failing tests** in `tests/integration/test_web_dev.py`. They use the characterization client with the `busy` desk, fake dev parts from `tests/fakes/dev.py`, and the real `SqliteBrowser` on the desk's state file.
  - `test_states_show_objects_and_changes`: `cup` is in `objects`, and `changes` includes `appeared`.
  - `test_smoothing_trace_for_a_label_with_a_slash`: `?label=pen%2Fpencil` gives 200 (the pen picture is added to the desk), and `?label=nothing` gives 200 with `steps == []`.
  - `test_run_and_poll`: POST `{"check": true}`, then GET the run until it isn't `running`. A second POST while running gives 409.
  - `test_unknown_test_id_is_400`.
  - `test_query_and_refusal`: a SELECT gives 200 with rows, and `DELETE FROM events` gives 400 with an error.
  - `test_file_outside_is_400`: `path=../.env`.
  - Parametrized `test_other_computers_get_403` over every `/api/dev` route, with `from_ip='192.168.1.50'`.
  - `test_other_web_pages_get_403`: from `127.0.0.1` with `Origin: https://example.com`, a POST to `/api/dev/runs` gives 403 and no run starts. The same request with `Origin: http://localhost:<dev.vite_port>` gives 200.
  - `test_dev_off_means_no_routes`: with `dev.enabled: false`, `/api/dev/settings` and `/dev/` are both 404.
  - `test_page_not_built_says_how`: `ui_folder` points to an empty temp folder, and `/dev/` is 200 with `npm run build` in the text.
  - Add one `Case` per route to `cases.py`. `test_route_coverage.py` fails until they're there.
- [ ] **Step 2: Run** `uv run pytest tests/integration/test_web_dev.py tests/characterization -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the wiring, `DevOnly`, the router and the mount.
- [ ] **Step 4: Run** the same command, then `DESKTALK_RECORD_SNAPSHOTS=1 uv run pytest tests/characterization` to save the new shapes. `git status` must show only new `dev-*` snapshot files, with no old ones changed. Then run once more without the variable. Expected: PASS. Also run `uv run pytest tests/unit/test_layers.py`. Expected: PASS.
- [ ] **Step 5: Commit:** `git commit -m "Add the developer page's routes and serve it at /dev/"`

---

### Task 7: The page's skeleton: Vite, React, TypeScript, the API client, the tabs

**Files:**
- Create in `dev_ui/`: `package.json`, `package-lock.json`, `tsconfig.json`, `vite.config.ts`, `index.html`, `src/main.tsx`, `src/App.tsx`, `src/api.ts`, `src/usePoll.ts`, `src/style.css`, `src/tabs/{Checks,Live,System,Database,Storage}.tsx` (placeholders, each `export default function X(props: {settings: DevSettings}): JSX.Element`), `src/test-setup.ts`, `src/api.test.ts`, `src/usePoll.test.tsx`, `README.md`
- Modify: `.gitignore` (add `dev_ui/node_modules/` and `dev_ui/dist/`)

**Interfaces:**
- Consumes: the routes and shapes in Task 6's table. This task can start right after Task 1, written against that table.
- Produces:
  - `src/api.ts`: `getJSON<T>(path: string): Promise<T>` and `postJSON<T>(path: string, body: unknown): Promise<T>`. Both throw `ApiError(status: number, message: string)`, taking the message from `{"error"}` replies. It also exports:
    - one TypeScript type per reply: `DevSettings`, `States`, `SmoothingStep`, `RunStatus`, `TestItem`, `TableInfo`, `QueryResult`, `DataFile`, `Health` (the `/api/health` shape) and `LogStatus` (the `/api/log/status` shape);
    - one function per route, which the tabs use by these names: `getDevSettings()`, `getStates(since)`, `getTracedLabels()`, `getTrace(label)` (it URL-encodes the label), `getTests()`, `startCheck()`, `startTests(ids)`, `getRun(id, from)`, `stopRun(id)`, `getDatabases()`, `getTables(db)`, `runQuery(db, sql)`, `getFiles()`, `getPreview(path)`, `getHealth()` and `getLogStatus()`.
  - `src/usePoll.ts`: `usePoll<T>(load: () => Promise<T>, everyMs: number): {data: T | null, error: string | null, lastOk: number | null}`. It keeps the last good data when a call fails.
  - `App.tsx`: a tab bar (Checks, Live, System, Database, Storage) that loads `/api/dev/settings` once and passes `settings: DevSettings` to every tab as a prop. When the server can't be reached, it shows a "Can't reach Desk Talk, trying again" bar and keeps polling.

Setup:
- Vite 5 with `@vitejs/plugin-react`, and `base: '/dev/'`.
- `vite.config.ts` reads `web.port` and `dev.vite_port` from `../config/settings.yaml` (and from `settings.local.yaml` when that file is there) with the `yaml` package. It sets `server.port`, and `server.proxy['/api']` to `http://127.0.0.1:<web.port>`.
- Scripts: `dev`, `build` (`tsc --noEmit && vite build`), `test` (`vitest run`).
- `tsconfig` has `strict: true`.
- Style: plain CSS with the Bricolage Grotesque font from `../web_app/fonts/`, so it looks like it belongs to Desk Talk. No component library.

- [ ] **Step 1: Write the failing page tests:**
  - `api.test.ts`: a 400 reply `{"error": "nope"}` makes `getJSON` throw `ApiError` with `status 400` and `message 'nope'`, and `getTrace('pen/pencil')` asks for `/api/dev/smoothing/trace?label=pen%2Fpencil`.
  - `usePoll.test.tsx`: with fake timers, a failing second call keeps the first `data` and sets `error`.
- [ ] **Step 2: Run** `npm install && npm test` in `dev_ui/`. Expected: FAIL.
- [ ] **Step 3: Implement** the skeleton.
- [ ] **Step 4: Run** `npm test && npm run build`. Expected: PASS, and `dev_ui/dist/index.html` exists. With the server running, `http://localhost:5000/dev/` shows the five tabs.
- [ ] **Step 5: Commit:** `git commit -m "Add the developer page's skeleton with Vite, React and TypeScript"`

---

Tasks 8 to 12 each fill in one tab. Each one edits only its own files, so they can run at the same time once Tasks 6 and 7 are done.

### Task 8: Checks tab: the one button, and each test

**Files:** `dev_ui/src/tabs/Checks.tsx`, `dev_ui/src/testTree.ts`, `dev_ui/src/testTree.test.ts`, `dev_ui/src/tabs/Checks.test.tsx`

**Interfaces:**
- Consumes: `getTests()`, `startCheck()`, `startTests(ids)`, `getRun(id, from)` and `stopRun(id)` from `api.ts`, and `settings.checks` and `settings.poll_sec`.
- Produces: `buildTree(items: TestItem[]): TreeNode[]`, where `TreeNode = {name, path, children, testIds}`. The levels are folder (`unit`, `integration`, `contracts`, `characterization`, `slow`), then file, then test.

What it shows:
- At the top, one big **Run everything** button, with one chip per `settings.checks` name: waiting, running, passed or failed.
- Below that, the test tree with a search box and a **Run** button on every folder, file and test (a folder or file sends its test ids). Slow tests get a "slow" tag.
- During a run: a live count (passed, failed, skipped), each test turning green or red as it finishes, the printout in a scrolling box, and a **Stop** button.
- After a run: the failed tests listed first, each with its message, and a "Run again" button that runs only those.
- The Run buttons are greyed out while a run is going. A 409 shows "A run is already going".

- [ ] **Step 1: Write the failing tests:**
  - `testTree.test.ts`: `tests/unit/test_a.py::test_x`, `tests/unit/test_a.py::test_y` and `tests/integration/test_b.py::test_z` give two folders, and `unit` has 1 file with 2 `testIds`.
  - `Checks.test.tsx`, with a mocked `api.ts`:
    - clicking **Run everything** calls `startCheck` once and greys out the button;
    - a status with one failed outcome lists it with its message;
    - "Run again" calls `startTests` with just that id.
- [ ] **Step 2: Run** `npm test`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `npm test`. Expected: PASS. Then, with the server running, click **Run everything** and see the three steps finish.
- [ ] **Step 5: Commit:** `git commit -m "Add the checks tab: one button for everything, and each test"`

---

### Task 9: Live tab: object states, transitions and smoothing

**Files:** `dev_ui/src/tabs/Live.tsx`, `dev_ui/src/SmoothingChart.tsx`, `dev_ui/src/timeline.ts`, `dev_ui/src/timeline.test.ts`, `dev_ui/src/tabs/Live.test.tsx`

**Interfaces:**
- Consumes: `getStates(since)`, `getTracedLabels()` and `getTrace(label)`, plus `settings.poll_sec`, `settings.trace_pictures` and `settings.object_states` (the confidence lines, the waits and the vote).
- Produces: `flipsPerMinute(changes, label, now): number` and `mergeChanges(old, new)` (no duplicates by `ts+label+change`).

What it shows:
- **Now:** one card per object, with its state (PRESENT green, ABSENT grey, UNKNOWN outline) and how long it's been that way.
- **Transitions:** a list, newest first: time, object, `appeared`/`removed`/`moved`, before → after. A label filter. A "flickering" badge on any object with more than 4 flips in the last minute.
- **Smoothing** for the chosen object, as a uPlot chart of the last `trace_pictures` pictures:
  - the box confidence per picture, with two flat lines at `on_confidence` and `off_confidence`;
  - a band for the hysteresis answer, and a band for the vote (`votes_yes/votes_total`, with a mark at `vote_share`);
  - the state underneath;
  - while it's waiting, the countdown `waiting_sec` out of `appear_sec`/`gone_sec`.

  This is the view that would have shown the flickering cup right away.

- [ ] **Step 1: Write the failing tests:**
  - `timeline.test.ts`: five changes for `cup` in 60 s gives `flipsPerMinute == 5`, and merging overlapping lists keeps each change once.
  - `Live.test.tsx`, with a mocked `api.ts`:
    - a states reply with `cup: PRESENT` shows a PRESENT card;
    - picking `pen/pencil` calls `getTrace('pen/pencil')`;
    - an empty trace shows "No pictures yet for this object".
- [ ] **Step 2: Run** `npm test`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `npm test`. Expected: PASS. Then, with the Pi running, put the bottle down and see `appeared` with the smoothing chart filling in.
- [ ] **Step 5: Commit:** `git commit -m "Add the live tab: object states, transitions and smoothing"`

---

### Task 10: System tab: the Pi and every part's health

**Files:** `dev_ui/src/tabs/System.tsx`, `dev_ui/src/health.ts`, `dev_ui/src/health.test.ts`

**Interfaces:**
- Consumes: `GET /api/health` and `GET /api/log/status` (both exist already, unchanged), and `settings.health`.
- Produces: `healthRows(health: Health, logStatus: LogStatus, limits: DevSettings['health'], now: number): Row[]`, where `Row = {group, name, value, level: 'ok' | 'warn' | 'bad' | 'off'}`.

What it shows: rows grouped as below. A row turns amber at `warn` and red at `bad`. Pictures per second and events per second are worked out from the change between two polls.

| Group | Rows |
|---|---|
| Pi | connected; pictures per second; last picture age (bad past `camera.disconnect_after_sec`); detector (ok, busy, or off with the reason); detect ms; model; CPU °C (warn/bad at `pi_cpu_warn_c`/`pi_cpu_bad_c`); slowed down (`throttled` other than `0x0` = bad); disk free (warn under `pi_disk_warn_mb`); photos stored; last cleanup; clock off by |
| Detection on this computer | pictures analysed; detect ms (warn over `detect_warn_ms`); problem |
| Event stream | per consumer: handled, waiting, dropped (warn above 0), errors (bad above 0) |
| Background jobs | per job: state, failures, last error |
| Timed jobs | name, runs, next in, last error |
| Event log | today's counts, folder |
| AI | ready, which one, problem |

When the camera isn't the Pi, the Pi rows show "off: the camera is <source>".

- [ ] **Step 1: Write the failing tests** in `health.test.ts`: `cpu_temp_c: 82` gives `bad`; `throttled: '0x50005'` gives `bad`; `dropped: 3` gives `warn`; `pi: null` gives Pi rows with `level 'off'`; `disk_free_mb: 500` gives `warn`.
- [ ] **Step 2: Run** `npm test`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `npm test`. Expected: PASS. With the Pi running, the Pi group is filled in.
- [ ] **Step 5: Commit:** `git commit -m "Add the system tab: the Pi and every part's health"`

---

### Task 11: Database tab

**Files:** `dev_ui/src/tabs/Database.tsx`, `dev_ui/src/tabs/Database.test.tsx`

**Interfaces:**
- Consumes: `getDatabases()`, `getTables(db)` and `runQuery(db, sql)`, plus `settings.sql_max_rows`.

What it shows:
- On the left: the databases, and their tables with row counts. Clicking a table fills the box with `SELECT * FROM <table> ORDER BY rowid DESC LIMIT 50` and runs it.
- A **CREATE** line for the chosen table (from `TableInfo.sql`).
- A query box (Ctrl/Cmd+Enter runs it) with three ready-made queries for `object_states`:
  - the latest changes;
  - flips per object in the last 10 minutes;
  - each object's newest state.
- The result table, the time it took, and "showing the first N rows" when `truncated`. A refused query shows its message in red.
- "Read-only: this page can't change the database" under the box.

- [ ] **Step 1: Write the failing tests**, with a mocked `api.ts`:
  - clicking table `events` runs the `SELECT *` query;
  - a refused query shows its message;
  - a `truncated` result shows the row cap.
- [ ] **Step 2: Run** `npm test`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `npm test`. Expected: PASS.
- [ ] **Step 5: Commit:** `git commit -m "Add the database tab"`

---

### Task 12: Storage tab: the browser's localStorage and the data folder

**Files:** `dev_ui/src/tabs/Storage.tsx`, `dev_ui/src/browserStorage.ts`, `dev_ui/src/browserStorage.test.ts`

**Interfaces:**
- Consumes: `getFiles()` and `getPreview(path)`.
- Produces: `readBrowserStorage(store: Storage): {key, bytes, value, json: unknown | null, usedBy: string}[]`. `usedBy` names the part that uses the key: `desktalk_chats_v2` is "Chat history", `desktalk_todo_v1` is "Paper's to-do list", `desktalk_habits_v2` is "Habit counters", and any other `desktalk_*` key is "Desk Talk (unknown key)". It returns `[]` when reading throws.

What it shows:
- **In this browser:** each localStorage key with its size, what uses it, and its value (pretty JSON when it parses). Each key has a **Delete** button that asks "Delete <key>? The app page loses <usedBy>." first. This works because `/dev/` and the app page are both on `localhost:5000`, the same origin, so they share localStorage. Under `npm run dev` (port 5173) it's a different origin, so the tab says "Open this page at http://localhost:5000/dev/ to see the app's storage". The phone page's keys live on the phone, so the tab says so.
- **On this computer:** the data folder as a tree with sizes and dates. Clicking a file shows its preview. `.sqlite` files link to the database tab.
- **On the Pi:** photos stored, disk free and last cleanup, from `/api/health`.

- [ ] **Step 1: Write the failing tests** in `browserStorage.test.ts`: a fake `Storage` with `desktalk_todo_v1` gives `usedBy` "Paper's to-do list" and parsed JSON; a bad JSON value gives `json: null`; a `Storage` whose `getItem` throws gives `[]`.
- [ ] **Step 2: Run** `npm test`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `npm test`. Expected: PASS.
- [ ] **Step 5: Commit:** `git commit -m "Add the storage tab: localStorage and the data folder"`

---

### Task 13: Browser test, CI and docs

**Files:**
- Create: `tests/e2e/test_dev_page.py`
- Modify: `.github/workflows/tests.yml`, `README.md`, `docs/ARCHITECTURE.md`, `AGENTS.md`, `dev_ui/README.md`

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Write the browser test** `tests/e2e/test_dev_page.py`, using the existing e2e setup (`tests/e2e/conftest.py`). The setup builds `dev_ui` first if `dist/` is missing, and skips with a message if `npm` isn't there.
  - The five tabs load.
  - The Live tab shows the `cup` card after the fake camera shows a cup for `appear_sec + 1` seconds.
  - The Database tab lists `events`.
  - The Checks tab's **Run everything** finishes, with `dev.checks` set in the test config to three quick `python -c` commands.
- [ ] **Step 2: Run** `uv run pytest -m e2e tests/e2e/test_dev_page.py -v`. Expected: PASS.
- [ ] **Step 3: Add a CI job** in `tests.yml`: `actions/setup-node` with Node 22, then `npm ci`, `npm test` and `npm run build` in `dev_ui/`.
- [ ] **Step 4: Write the docs:**
  - `README.md`: a short "Developer page" section saying how to build it, where it is, and that it only answers this computer.
  - `docs/ARCHITECTURE.md`: a row for `dev_ui/` and `routers/dev.py`.
  - `AGENTS.md`: a line saying page code is `npm test` in `dev_ui/` and that new `/api/dev` routes need characterization cases.
  - `dev_ui/README.md`: `npm run dev`, `npm run build`, `npm test`.
- [ ] **Step 5: Run everything:** `uv run pytest -m "not slow and not docker and not e2e" --cov` (coverage not below `fail_under`), `uv run pytest -m e2e`, `uv run ruff check <changed files>`, `uv run pyright` (0 errors), `uv run vulture` (prints nothing), then `npm test && npm run build` in `dev_ui/`. Expected: all pass. Paste the output into the PR.
- [ ] **Step 6: Commit:** `git commit -m "Add a browser test, a CI job and docs for the developer page"`

---

## Order and what can run at the same time

```
Task 1 ──┬── Task 2 (runner) ───────┐
         ├── Task 3 (database) ─────┤
         ├── Task 4 (files) ────────┼── Task 6 (routes) ──┬── Task 8  (checks tab)  ──┐
         ├── Task 5 (trace) ────────┘                     ├── Task 9  (live tab)    ──┤
         └── Task 7 (page skeleton) ──────────────────────├── Task 10 (system tab)  ──┼── Task 13
                                                          ├── Task 11 (database tab) ─┤
                                                          └── Task 12 (storage tab)  ─┘
```

Tasks 2 to 5 and 7 touch different files, so five subagents can run them at once. Each one adds its own class to `tests/contracts/test_dev_real_parts.py`. To keep the "never two subagents on one file" rule, Task 1 creates that file with an empty class for each part, and each task fills in only its own class. Tasks 8 to 12 touch only their own tab files.

## Not in this plan

- Changing settings from the page: you still edit `config/settings.yaml`.
- Showing the phone page's localStorage: it lives on the phone.
- Logging in: this computer only, instead.
- Restarting the server or the Pi from the page.
