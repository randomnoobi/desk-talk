# Phase 3 briefs

Part of [the refactor plan](2026-10-21-refactor-plan.md). The rules from
[the Phase 2 briefs](2026-10-21-phase2-briefs.md#rules-for-every-agent) still apply:
- test-first;
- your own worktree;
- plain words;
- no `time.time()`, `print`, module globals or hardcoded numbers;
- show test and ruff output;
- one-sentence commits ending with the Co-Authored-By line.

What's different now: Phase 3 may edit any new-code file (`desktalk/core/`, `desktalk/adapters/`,
`desktalk/ports.py`, `config/settings.yaml`, `desktalk/config.py`, the fakes and contracts) when wiring needs it.
Keep old modules untouched until Phase 4 deletes them.

Everything each part needs is in [2026-10-21-phase3-notes.md](2026-10-21-phase3-notes.md). Read all of it first.

## 3a. Bootstrap: build the whole app from the new parts

**Owns:**
- `desktalk/bootstrap.py`
- the gaps listed below
- `tests/integration/test_app_*.py` (new; the names must not clash with the old integration tests)

**Build:**
- `desktalk/bootstrap.py`:
  - `build(config, *, clock, ...)` returns a `DeskTalk` container: a dataclass with every part as a named, typed
    field. It is the only place that knows the real classes.
  - Keyword overrides for tests: the detector, the OpenAI client factory, the camera source, the data folder and
    the config store. The defaults build the real things.
  - `DeskTalk.start()` registers every `PeriodicWorker` with the `Supervisor`, with the right `start_after`. The
    workers: producer, scheduler (log compact / prune, habit save), reminders, idle chat, lost items, Lens, daily
    recap, vision habits, Ollama keep-alive.
  - `DeskTalk.stop(timeout)`, in this order:
    1. stop the supervisor;
    2. `bus.wait_idle`;
    3. `habits.save(force=True)`;
    4. `event_log.compact`;
    5. `bus.close`.
  - Logging is set up from `config.logging`.
- Gaps to fill (all in the notes):
  - `PiFrameSource.latest_upload()` for D2's `PiUploads`;
  - a Pi picture that's older than one already shown never replaces it in the live view;
  - Lens says when Pi detection is off and why (new setting `lens.wait_before_saying_sec.pi_detector`);
  - Lens's lost-video text names the new camera program;
  - D2's `workers/prompts.py` and `workers/text.py` use D1's code instead of copies;
  - F's `ProfileLookup` replaced by `Profiles`;
  - the `needs.py` interfaces moved into `ports.py` if more than one part uses them.
- Whole-app tests (`tests/integration/test_app_*.py`):
  - built with `build(...)`, fakes for the detector and the AI, and a `FakeClock`;
  - drive the workers by calling `tick()`, not with threads;
  - port what `tests/integration/test_app_flow.py` and `test_reminders_and_lens.py` check: pictures → agents
    join and leave → chat messages on the hub; alerts; habits; reminders; Lens; the event log;
  - Pi pictures in each detector state (`ok` / `busy` / `off`);
  - one test with real threads: `start()` then `stop()`, cleanly and quickly.

## 3b. FastAPI web layer and server.py (after 3a)

**Owns:**
- `desktalk/web/app.py`, `desktalk/web/routers/`, `desktalk/web/schemas.py` and the other new files under
  `desktalk/web/`
- `server.py`
- `tests/characterization/client.py` and `tests/characterization/conftest.py`
- new tests under `tests/integration/test_web_*.py`

**Build:**
- `create_app(desktalk: DeskTalk) -> FastAPI`, with routers by area: pi, phone, live/video, chat, habits, log,
  rules/settings, health.
  - Pydantic request models.
  - Replies exactly as the characterization snapshots say; see the "Must stay exactly as today" list in the notes.
  - `/api/pi/frame` takes upload format 2 (`decode_upload`) and format 1 (headers). Above
    `web.pi_upload_max_mb` → 413.
  - `/api/pi/config` adds the detector settings.
  - `/api/health` shows the Supervisor's workers, the event stream stats, the Pi's status and the AI problem.
- The phone's HTTPS server: a second uvicorn server with the same certificate as today, and `parse_gravity`
  ported.
- `server.py`:
  - builds with `bootstrap.build(load_config())`, runs uvicorn;
  - FastAPI lifespan: `desktalk.start()` on start, `desktalk.stop()` on shutdown;
  - prints the same start-up lines as today, with the new Pi command
    (`python3 -m raspberry_pi.camera --server ... --key ...`).
- Switch `tests/characterization/client.py` and the desk builder in its `conftest.py` to the FastAPI app built
  by bootstrap with fakes.
  - All 133 cases must pass.
  - Fix the three odd routes on purpose (empty upload → 400, bad `limit` → 400, drop the phone `/static` route)
    and re-record only those snapshots.
- Port `tests/integration/test_web_api.py` and `test_pi_to_server.py`. The camera program's uploader must talk to
  a real local uvicorn server, with format 2 and `ok` / `busy` / `off`.
