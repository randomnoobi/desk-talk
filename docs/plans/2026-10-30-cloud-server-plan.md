# Cloud Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to carry out this plan, following "How to work" in `AGENTS.md`. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A self-hosted server in Docker that links a person's Pi to their Google account, collects its non-sensitive events, and writes them a weekly LLM report on a website.

**Architecture:** Four containers from `cloud/compose.yaml`: `postgres`, `api` (FastAPI), `reports` (the weekly job, same image as `api`) and `web` (React served by nginx, which passes `/api`, `/auth` and `/v1` to `api`). `desktalk` gets a `CloudUploader` background job that reads its event log and sends filtered events. One filter in `desktalk_shared` runs on both sides.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy Core with psycopg, Alembic, Postgres 17, httpx, the `anthropic` SDK, argon2-cffi, React 18 with Vite and TypeScript (same versions as `dev_ui/`), nginx, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-10-30-cloud-server-design.md`. Read it first: it has the send table, routes, database and report rules. This plan doesn't repeat them.

**When:** start a new branch from `main` after PR #11 is merged, because Task 2 changes `desktalk/core/demo/firer.py`, which PR #11 also changes.

## Global Constraints

- `AGENTS.md` applies: test first, config not code, plain words, `Clock` for time, interfaces first, a contract suite for each interface, unique test file names, no network in fast tests.
- `desktalk_cloud` never imports `desktalk/`, and `desktalk/` never imports `desktalk_cloud`. They share only `desktalk_shared`.
- `shared/desktalk_shared/cloud_api.py` is standard library only and works on Python 3.11.
- `desktalk` must behave the same with no link, with the server off, and with the server failing.
- No secret goes in YAML, logs or the repo. Secrets go in `cloud/.env`, with their names in `cloud/.env.example`.
- Numbers go in `cloud/settings.yaml` with plain-language comments, wording and prompts in `cloud/prompts.yaml`.
- Tasks 3, 5, 6 and 7 don't touch each other's files. Only Task 9 connects the parts in `bootstrap.py` and `main.py`. Task 3 writes every section of `cloud/settings.yaml`, `config.py` and `ports.py` up front, so later tasks don't edit them. Names vulture can't see from Tasks 3 to 8 go in `tests/vulture_allow.py` in Task 9, so no two subagents edit that file at once.
- Real AI calls, real Google login and a real Pi are never needed by automated tests.

## Order of work

1. Task 1 alone, since everything uses it.
2. Tasks 2 and 3 at the same time.
3. Tasks 4, 5, 6, 7 and 8 at the same time, all using Task 3's interfaces.
4. Task 9, then Task 10.

## Review Focus

1. **The same code used twice at the same moment:** exactly one Pi gets a token. Pinned in Tasks 3 and 4.
2. **The computer shuts down in the middle of a report:** the `pending` row is picked up on the next start and finishes, and no second row appears. Pinned in Task 6.
3. **A Pi offline for longer than `event_log.keep_days`:** the saved place points to a day that was deleted, and the sender carries on from the oldest day left. Pinned in Task 2.
4. **Clocks going forward or back, and a bad time zone:** the report comes due once at the right local time. A time zone the server doesn't know falls back to UTC with a warning, and doesn't stop the job for other Pis. Pinned in Task 6.
5. **Secrets in logs:** no token, pairing code, session cookie, API key or chat text appears in logs at any level, including when Google or the AI company fails. Pinned in Tasks 3, 5 and 7.

---

### Task 1: The shared filter and the empty package

**Files:**
- Create: `shared/desktalk_shared/cloud_api.py`, `cloud/server/pyproject.toml`, `cloud/server/desktalk_cloud/__init__.py`
- Modify: root `pyproject.toml` (workspace member, coverage source, vulture paths, ruff first-party), `pyrightconfig.json` (include `cloud/server`), `uv.lock`, `tests/unit/test_layers.py`
- Test: `tests/unit/test_shared_cloud_api.py`

**Interfaces:**
- Produces:
  - `to_cloud_event(event: Mapping[str, Any]) -> dict[str, Any] | None`: the send table as code, `None` when it stays home. Running it twice gives the same result.
  - `is_demo(event) -> bool`: `extra`'s `demo` is true, or `rule` starts with `demo.`.
  - `goals_event(ts, *, wake, bed, goals: Mapping[str, float]) -> dict` and `daily_totals_event(ts, day: str, totals: Mapping[str, Any]) -> dict`. `daily_totals`' id is `daily-<day>`.
  - `HOW_WORDS: frozenset[str]`, `MAX_ID_CHARS`, and dataclasses `ClaimReply(device_id, token)` and `IngestReply(accepted, repeats, refused)`, with `to_dict` and `from_dict`.

- [ ] **Step 1:** Write failing tests, with one test per row of the spec's send table and at least:
```python
def test_todo_text_never_passes():
    event = {'id': 'a', 'ts': 1.0, 'type': 'habit', 'habit': 'tasks', 'action': 'done',
             'say': 'Done: english essay', 'task': 'english essay'}
    assert to_cloud_event(event) == {'id': 'a', 'ts': 1.0, 'type': 'habit', 'habit': 'tasks', 'action': 'done'}

def test_tasks_nudge_loses_its_evidence():
    event = {'id': 'a', 'ts': 1.0, 'type': 'nudge', 'habit': 'tasks', 'rule': 'tasks.open', 'agent': 'paper',
             'evidence': 'starting with "english essay"', 'text': 'Oh no'}
    sent = to_cloud_event(event)
    assert 'evidence' not in sent and 'text' not in sent

def test_picture_fields_never_pass():
    for name in ('picture', 'jpeg', 'image', 'photo', 'frame'):
        assert name not in to_cloud_event({'id': 'a', 'ts': 1.0, 'type': 'habit', 'habit': 'breaks',
                                           'action': 'break', name: 'abc'})
```
Also: `detection`, `agent_joined`, `agent_left`, `compaction` and unknown types give `None`. A demo event gives `None`. A `how` not in `HOW_WORDS` is dropped. `label` is dropped. `True`, NaN and infinity don't count as numbers. An event with no `id` or `ts` gives `None`. Before writing `HOW_WORDS`, read the words the app uses in `habit_reader.py`, `tracker.py` and `firer.py`. `test_how_words_match_the_app` asserts it equals that list.
- [ ] **Step 2:** Add a `test_layers.py` case: no file in `cloud/server` imports `desktalk`, and no file in `desktalk` imports `desktalk_cloud`. `cloud_api.py` uses only the standard library, checked like the other shared files.
- [ ] **Step 3:** Run `uv run pytest tests/unit/test_shared_cloud_api.py tests/unit/test_layers.py`. Expected: FAIL.
- [ ] **Step 4:** Write `cloud_api.py`, and the package skeleton with its dependencies from the Tech Stack. Run `uv sync --all-packages`.
- [ ] **Step 5:** Run the same tests. Expected: PASS.
- [ ] **Step 6:** Commit: "Add the filter that decides what may go to the cloud server".

### Task 2: The sender in `desktalk`

**Files:**
- Create: `desktalk/core/cloud/__init__.py`, `desktalk/core/cloud/uploader.py`, `desktalk/adapters/cloud/__init__.py`, `desktalk/adapters/cloud/http_link.py`, `desktalk/web/routers/cloud.py`, `scripts/link_cloud.py`, `tests/contracts/cloud_link_contract.py`
- Modify: `desktalk/ports.py`, `desktalk/domain/events.py` or `desktalk/domain/errors.py` (the two errors), `desktalk/bootstrap.py`, `desktalk/config/app.py` (`CloudSettings`), `config/settings.yaml`, `config/prompts.yaml`, `desktalk/config/prompts.py`, `desktalk/web/app.py` (include the router), `desktalk/core/demo/firer.py`, `desktalk/core/habits/tracker.py`, `desktalk/core/chat/workers/lens.py`, `web_app/` (one status line in Settings), `tests/characterization/cases.py`, `tests/fakes/parts.py`, `tests/vulture_allow.py`
- Test: `tests/unit/test_core_cloud_uploader.py`, `tests/unit/test_adapters_cloud_link.py`, `tests/unit/test_scripts_link_cloud.py`, `tests/integration/test_app_cloud.py`, `tests/unit/test_core_demo_marks.py`

**Interfaces:**
- Consumes: Task 1's `to_cloud_event`, `is_demo`, `goals_event`, `daily_totals_event`, `IngestReply`.
- Produces:
  - In `ports.py`: `CloudLink.send(events: Sequence[Mapping[str, Any]], *, caught_up: bool) -> IngestReply`, raising `CloudUnavailable` (no answer, 5xx, 429) or `CloudNotLinked` (401).
  - `CloudUploader(link_factory, events: EventStore, store: KeyValueStore, preferences, goals: Mapping[str, float], settings: CloudSettings, clock, words)`, a `PeriodicWorker` named `cloud uploader`, with `status() -> dict`. `bootstrap.py` makes `goals` from `config.protocols`, and `preferences` gives wake and bed times.
  - Saved under `cloud.link_key` in the `KeyValueStore`: `{server, device_id, token, place_ts, recent_ids, stopped}`.
  - `CloudSettings`: `link_key`, `send_every_sec`, `idle_check_every_sec`, `batch_size`, `overlap_sec`, `timeout_sec`, `retry_start_sec`, `retry_max_sec`, `totals_every_min`, `goals_every_hours`.
  - `GET /api/cloud/status`: `{linked, stopped, last_sent_ts, waiting}`. Never the token.
  - `uv run python scripts/link_cloud.py --server <url> --code <code> [--name <name>]`.

- [ ] **Step 1:** Write failing uploader tests with `FakeClock`, a fake `CloudLink` and an in-memory store:
  - `test_no_link_calls_nothing_and_waits_long`: `tick` returns `idle_check_every_sec`, and the link factory is never called.
  - `test_sends_oldest_first_and_moves_place`.
  - `test_starts_from_the_link_moment`: events before `place_ts` are never sent.
  - `test_failure_keeps_place_and_waits_longer`: on `CloudUnavailable`, the place doesn't move, and waits grow with `Backoff`. It doesn't raise.
  - `test_not_linked_stops_until_linked_again`: on `CloudNotLinked`, `stopped` is saved, nothing more is sent, and `status()` says so. A new link clears it.
  - `test_refused_events_do_not_hold_up_the_rest`.
  - `test_caught_up_only_on_the_batch_that_empties_the_backlog`.
  - `test_event_written_late_within_overlap_is_still_sent` and `test_recent_ids_stop_resends`.
  - `test_place_on_a_deleted_day_carries_on` (Review Focus 3).
  - `test_goals_sent_at_link_on_change_and_daily` and `test_totals_sent_when_changed_at_most_every_totals_every_min`. Totals read `habits/<day>` keys through `day_key`, using `desk_sec`, `hourly_activity`, `water_ml`, `sips`, `breaks`, `longest_sit_sec`, `phone_free_sec`, `phone_on_desk_sec`, `sunlight_minutes` and `nudges_sent`.
- [ ] **Step 2:** Write failing tests for the rest:
  - `http_link` with a local `http.server` thread: `Authorization: Bearer` is sent, and each status maps to the right result. A shared `CloudLinkContract` also runs on the fake.
  - `test_link_script_saves_token_and_place`, and `test_link_script_wrong_code_says_so_and_saves_nothing`.
  - In `test_core_demo_marks.py`: `_sip` and `_camera_covered` events carry `demo: true`, and `is_demo` is true for each `DemoFirer` scenario's events.
  - In `test_app_cloud.py`: `test_server_off_changes_nothing`: with `DeskRig` and a link that always fails, chat, habits and the event log behave normally. `test_status_route_never_shows_the_token`.
  - Add the route's characterization case.
- [ ] **Step 3:** Run them. Expected: FAIL.
- [ ] **Step 4:** Write the code. `http_link` uses `urllib` from the standard library, as the Pi's `net.py` does. To mark demo events, `HabitLog.log` passes `demo=True` through to `extra`, and `say_problem` gains a `demo: bool = False` keyword: update the fakes and contract suites. The uploader gets its next wait from `tick`. Add it to `background_jobs` in `bootstrap.py`, and pass only the interfaces it needs.
- [ ] **Step 5:** Run the new tests, then `uv run pytest tests/contracts tests/characterization tests/unit/test_core_demo_marks.py`. Expected: PASS.
- [ ] **Step 6:** Run `uv run pytest -m e2e` since `web_app/` changed. Expected: PASS.
- [ ] **Step 7:** Commit: "Send filtered events to a linked cloud server in the background".

### Task 3: The server's core: Pis, tokens and the three routes

**Files:**
- Create in `cloud/server/desktalk_cloud/`: `config.py`, `domain.py`, `ports.py`, `clock.py`, `tokens.py`, `rate_limit.py`, `store_memory.py`, `ingest.py`, `app.py`, `deps.py`, `routers/devices_v1.py`, `routers/events_v1.py`
- Create: `cloud/settings.yaml`, `cloud/prompts.yaml`, `cloud/.env.example`, `tests/contracts/cloud_store_contract.py`, `tests/fakes/cloud.py`
- Test: `tests/contracts/test_cloud_store_contract.py`, `tests/unit/test_cloud_ingest.py`, `tests/unit/test_cloud_routes_v1.py`, `tests/unit/test_cloud_config.py`

**Interfaces:**
- Consumes: Task 1.
- Produces, used by Tasks 4 to 9:
  - `load_config(root, environ) -> CloudConfig`, with typed sections `server`, `pairing`, `ingest`, `reports`, `chat`, `retention`, `limits`, and `secrets` from the environment. It stops with a plain message if `SESSION_SECRET` is missing or short.
  - `create_app(parts: CloudParts, routers: Sequence[APIRouter]) -> FastAPI`. `CloudParts` holds `config`, `store`, `clock`, `google`, `chat_forwarder`, `claim_limiter`, `chat_limiter`.
  - `Store` in `ports.py`, with these methods. Tasks 3 to 6 use exactly these names:
    - Users: `upsert_user(google_sub, email, name, now) -> User`, `get_user(id)`, `delete_user(id)`, `list_users()`
    - Admins: `get_admin(username)`, `create_admin_if_missing(username, password_hash)`
    - Codes: `add_code(code_hash, user_id, device_id | None, time_zone, expires_at)`, `claim_code(code_hash, name, token_hash, now) -> Device | None` (one transaction, one winner)
    - Pis: `get_device(id)`, `list_devices(user_id)`, `list_all_devices()`, `rename_device(id, name)`, `remove_device(id, now)`, `delete_device(id)`, `device_for_token(token_hash, now) -> Device | None`
    - Events: `save_batch(device_id, events, totals, state_changes, caught_up, now) -> tuple[int, int]`, `events_after(device_id, since, after_id, limit)`, `events_between(device_id, start, end)`, `latest_event(device_id, type)`, `object_states(device_id)`, `totals_between(device_id, first_day, last_day)`, `delete_events_before(ts) -> int`
    - Reports: `create_report(device_id, week_start, now) -> Report | None`, `get_report(device_id, week_start)`, `get_report_by_id(id)`, `list_reports(device_id)`, `reports_to_write(now)`, `finish_report(id, status, markdown, model, error, now)`, `retry_report(id, error, next_try_at)`, `list_failed_reports()`, `reset_report(id, now)`
  - `GoogleLogin`, `ReportWriter` and `ChatForwarder` in `ports.py`, with fakes in `tests/fakes/cloud.py`:
    - `GoogleLogin.login_url(state, redirect_uri) -> str`, `exchange(code, redirect_uri) -> GoogleUser`
    - `ReportWriter.write(prompt: str, data: str) -> str`, raising `ReportFailed`
    - `ChatForwarder.complete(messages, max_tokens, temperature) -> dict`, raising `ChatFailed`
  - The full `reports` table includes `tries` and `next_try_at`. `pairing_codes` includes `device_id` (for "Link again") and `time_zone`.
  - `deps.py`: `device_from_token`, `owner_or_device(device_id)`, and `current_user`, which reads `session['user_id']`.
  - `tokens.py`: `new_token()`, `new_code()`, `hash_secret(text)`.

- [ ] **Step 1:** Write the failing store contract, run against `store_memory.py`: `test_two_claims_one_winner` (with threads), `test_expired_or_used_code_fails_alike`, `test_repeat_events_change_nothing`, `test_older_event_never_overwrites_newer_state`, `test_newer_totals_replace_older`, `test_events_after_never_skips_same_time`, `test_removed_device_token_fails`, `test_link_again_keeps_device_id_and_replaces_token`, `test_delete_user_removes_everything`, `test_one_report_row_per_week`.
- [ ] **Step 2:** Write failing route tests with `TestClient`:
```python
def test_post_events_counts_accepted_repeats_refused(client, token): ...
def test_one_bad_event_never_fails_the_batch(client, token): ...
def test_batch_over_limit_gets_413(client, token): ...
def test_server_filters_again(client, token):
    post_events(client, token, [{'id': 'a', 'ts': 1.0, 'type': 'habit', 'habit': 'tasks', 'action': 'done', 'say': 'essay'}])
    assert 'say' not in get_events(client, token)['events'][0]
def test_other_persons_device_gets_404(client, token, other_token): ...
def test_removed_pi_gets_401(client, token): ...
def test_paging_with_same_time_events_returns_all(client, token): ...
def test_state_has_goals_last_seen_camera_and_objects(client, token): ...
def test_claim_wrong_and_expired_code_same_reply(client): ...
def test_claim_attempts_are_limited(client): ...
def test_token_and_code_never_logged(client, caplog): ...
```
Also config tests: a missing `SESSION_SECRET` stops with its name in the message, and `.env.example` lists every secret name.
- [ ] **Step 3:** Run them. Expected: FAIL.
- [ ] **Step 4:** Write the code. `ingest.py` runs `to_cloud_event` on each event, sorts results into events, totals and state changes, and calls `save_batch`. The token is the standard library's `secrets.token_urlsafe` with a prefix, hashed with SHA-256, which is fine for a long random token. `create_app` adds Starlette's `SessionMiddleware`. Keep all sizes in `cloud/settings.yaml`, with the defaults the spec names, and include the settings Tasks 5 to 7 name.
- [ ] **Step 5:** Run them. Expected: PASS. Then run `uv run ruff check cloud shared/desktalk_shared/cloud_api.py`, `uv run pyright`, `uv run vulture`.
- [ ] **Step 6:** Commit: "Add the cloud server's core: linking a Pi, tokens and the event routes".

### Task 4: The Postgres store

**Files:**
- Create: `cloud/server/desktalk_cloud/store_postgres.py`, `cloud/server/alembic.ini`, `cloud/server/desktalk_cloud/migrations/` (first migration with the spec's tables)
- Modify: root `pyproject.toml` (coverage `omit` for `store_postgres.py` and `migrations/`, with a comment that the `docker` contract run covers them)
- Test: `tests/docker/test_cloud_postgres_store.py`

**Interfaces:**
- Consumes: Task 3's `Store` and its contract suite.
- Produces: `PostgresStore(database_url)`, `run_migrations(database_url)`.

- [ ] **Step 1:** Write the failing test: a class that subclasses the contract suite against a throwaway `postgres:17-alpine` container, under the `docker` marker. It adds `test_migrations_run_twice_safely`.
- [ ] **Step 2:** Run `uv run pytest -m docker tests/docker/test_cloud_postgres_store.py`. Expected: FAIL.
- [ ] **Step 3:** Write the store and migration. Use `ON CONFLICT DO NOTHING` for events, a conditional update for state and a single conditional `UPDATE ... RETURNING` for `claim_code`. Add the index and unique rules from the spec.
- [ ] **Step 4:** Run it again. Expected: PASS, including the two-claims test on real Postgres.
- [ ] **Step 5:** Commit: "Store the cloud server's data in Postgres".

### Task 5: Logging in, the website's data and admin

**Files:**
- Create in `cloud/server/desktalk_cloud/`: `google_login.py`, `routers/auth.py`, `routers/site.py`, `routers/admin.py`, `admins.py`
- Test: `tests/unit/test_cloud_auth_routes.py`, `tests/unit/test_cloud_site_routes.py`, `tests/unit/test_cloud_admin_routes.py`, `tests/unit/test_cloud_google_login.py`, `tests/contracts/test_cloud_google_contract.py`

**Interfaces:**
- Consumes: Task 3's parts, fakes and `Store`.
- Produces: `/auth/google/login`, `/auth/google/callback`, `/auth/logout`, `/auth/admin/login`. `GET /api/me`, `DELETE /api/me`, `GET /api/devices`, `POST /api/devices/code {time_zone, device_id?} → {code, expires_at, server_url, command}`, `PATCH` and `DELETE /api/devices/{id}`, `DELETE /api/devices/{id}/data`, `GET /api/reports?device_id=`, `GET /api/reports/{id}`. `GET /api/admin/overview`, `POST /api/admin/reports/{id}/rerun`. `ensure_admin(config, store)`.

- [ ] **Step 1:** Write failing tests:
  - `test_google_login_creates_user_and_cookie`, `test_wrong_state_refused`, `test_google_failure_shows_plain_error_and_logs_no_secret`, `test_unverified_email_refused`, `test_no_google_settings_says_how_to_set_up`.
  - `test_other_persons_report_is_404`, `test_code_for_other_persons_device_is_404`, `test_code_reply_uses_public_url`, `test_bad_time_zone_refused`, `test_delete_me_removes_all`.
  - `test_admin_password_is_hashed_and_login_works`, `test_no_admin_settings_means_no_admin_login`, `test_admin_login_attempts_are_limited`, `test_overview_has_no_events_or_report_text`, `test_person_cannot_reach_admin_routes`, `test_rerun_resets_the_report`.
  - The `httpx.MockTransport` tests for `google_login.py`.
- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3:** Write the code. Google's addresses go in `cloud/settings.yaml`. `exchange` calls Google's token address, then its userinfo address. The session uses Starlette's `SessionMiddleware`, which Task 3's `create_app` adds, with `same_site=lax` and `https_only` from settings. The admin signs in under a separate session key. Passwords use argon2.
- [ ] **Step 4:** Run them. Expected: PASS.
- [ ] **Step 5:** Commit: "Add Google login, the website's routes and the admin login".

### Task 6: The weekly report

**Files:**
- Create in `cloud/server/desktalk_cloud/`: `report_data.py`, `report_times.py`, `report_job.py`, `llm_anthropic.py`, `llm_openai.py`, `retention.py`
- Modify: `cloud/prompts.yaml` (the report's prompt and data notes)
- Test: `tests/unit/test_cloud_report_data.py`, `tests/unit/test_cloud_report_times.py`, `tests/unit/test_cloud_report_job.py`, `tests/unit/test_cloud_llm_writers.py`, `tests/contracts/test_cloud_report_writer_contract.py`, `tests/unit/test_cloud_retention.py`

**Interfaces:**
- Consumes: Task 3's `Store` and `ReportWriter`.
- Produces:
  - `latest_due(now, zone, weekday, hour) -> float`, `week_of(due, zone) -> (start_ts, end_ts, week_start_day)`. A week is the 7 days up to the due moment.
  - `build_week(device, events, totals, goals, states, last_camera, now, week, words) -> str`: `data/weekly_report_test/build_weekly_input.py`'s output from stored data, without to-dos or message wording.
  - `ReportJob(store, writer, clock, settings, prompts).tick(now) -> float`, and `RetentionJob`.

- [ ] **Step 1:** Copy `data/weekly_report_test/prompt.md` into `cloud/prompts.yaml`, with "to-dos" taken out of the Weekly trends list.
- [ ] **Step 2:** Write failing tests:
  - Times: `test_due_is_sunday_20_local`, `test_week_across_clocks_changing`, `test_due_once_per_week`, and `test_unknown_zone_uses_utc_and_warns`.
  - Data: `test_has_coverage_goals_totals_nudges_snapshot_events`, `test_days_with_no_data_say_so`, `test_outcome_uses_rule_outcome_when_there_is_one`, `test_next_sign_estimate_matches_the_old_script` (same inputs and answers as `next_sign`, with made-up events), `test_no_todo_section`, `test_last_heard_note_when_pi_had_not_caught_up`.
  - Job: `test_writes_when_due_and_caught_up`, `test_waits_for_catch_up_then_writes_after_wait_days`, `test_no_data_after_wait_no_llm_call`, `test_written_late_after_sleep`, `test_only_latest_week_written_after_long_sleep`, `test_failure_retries_then_failed`, `test_pending_row_finished_after_restart`, `test_one_bad_pi_does_not_stop_others`, `test_device_linked_after_due_gets_no_report_yet`, `test_no_key_marks_failed_with_plain_message`.
  - Writers: both pass a shared contract using fake clients. `test_prompt_rules_and_data_markers_reach_the_writer`.
- [ ] **Step 3:** Run them. Expected: FAIL.
- [ ] **Step 4:** Write the code. `llm_anthropic.py` uses the `anthropic` SDK with `reports.model` (default `claude-sonnet-5-5`). Load the `claude-api` skill before writing it. `llm_openai.py` uses `httpx` against `reports.url`. Keep the data inside `<data>` tags, as the tested prompt expects.
- [ ] **Step 5:** Run them. Expected: PASS.
- [ ] **Step 6:** Build one week's data from the user's real test week through `build_week`, by a throwaway script that isn't committed. Give the prompt and data to a Sonnet subagent and check the report's main claims against the data. Expected: 300 to 450 words and no wrong facts. If it's worse than `data/weekly_report_test/output_sonnet.md`, fix the data notes before moving on.
- [ ] **Step 7:** Commit: "Write each Pi's weekly report with an LLM".

### Task 7: The chat route

**Files:**
- Create in `cloud/server/desktalk_cloud/`: `chat_forwarder.py`, `routers/chat_v1.py`
- Test: `tests/unit/test_cloud_chat_route.py`, `tests/unit/test_cloud_chat_forwarder.py`

**Interfaces:**
- Consumes: Task 3's `ChatForwarder`, `device_from_token` and `chat_limiter`.
- Produces: `POST /v1/chat/completions`, the OpenAI reply shape. It accepts what `OpenAICompatibleChatModel` sends (`messages`, `max_tokens`, `temperature`, plus ignored fields such as `model` and `extra_body`). It always uses `chat.model` and never streams.

- [ ] **Step 1:** Write failing tests: `test_answers_in_openai_shape` (using a real `openai` client against `TestClient`'s transport, so the shape is proven), `test_any_image_refused` (an `image_url` part, and `data:image/` in a text part), `test_no_key_gets_503`, `test_limit_per_pi_gets_429`, `test_needs_a_token`, `test_model_from_settings_not_request`, `test_chat_text_appears_in_no_log_or_table` (with `caplog` at DEBUG, on success and when the AI company returns an error containing the text), `test_upstream_error_gets_a_plain_502`.
- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3:** Write the code, using `httpx` to `chat.url` with `CHAT_API_KEY`. Log only the status and time taken.
- [ ] **Step 4:** Run them. Expected: PASS.
- [ ] **Step 5:** Commit: "Let a Pi use the cloud server's AI key for chat, text only".

### Task 8: The website

**Files:**
- Create: `cloud/web/` (Vite, React and TypeScript, same versions as `dev_ui/package.json`, plus `react-markdown`), with `src/api.ts`, `src/pages/{Login,Pis,OnePi,Reports,OneReport,Admin}.tsx`
- Modify: `.github/workflows/tests.yml` (the `page` job also runs `cloud/web`)
- Test: `cloud/web/src/**/*.test.tsx`

**Interfaces:**
- Consumes: the routes in Tasks 3 and 5, as written there.
- Produces: `npm run build` puts static files in `cloud/web/dist`. A report's "new" mark is kept per browser in `localStorage`, wrapped in `try/catch`.

- [ ] **Step 1:** Write failing tests with a fake `fetch`: `test_login_page_has_google_button`, `test_setup_message_when_google_is_not_set_up`, `test_add_pi_shows_code_and_command_and_sends_time_zone`, `test_warns_when_server_url_is_localhost`, `test_link_again_uses_that_device`, `test_remove_and_delete_are_separate_and_ask_first`, `test_new_report_gets_a_mark_then_loses_it`, `test_report_markdown_shows_no_raw_html`, `test_failed_and_no_data_reports_say_so`, `test_admin_page_has_no_events`.
- [ ] **Step 2:** Run `npm test` in `cloud/web`. Expected: FAIL.
- [ ] **Step 3:** Write the pages. Keep them plain, and work at phone width. Don't put numbers in the page that the API can give.
- [ ] **Step 4:** Run `npm test` and `npm run build` in `cloud/web`. Expected: PASS.
- [ ] **Step 5:** Commit: "Add the cloud server's website".

### Task 9: Connect it all and run it in Docker

**Files:**
- Create in `cloud/server/desktalk_cloud/`: `bootstrap.py`, `main.py` (`uvicorn` entry), `reports_main.py`
- Create: `cloud/server/Dockerfile`, `cloud/web/Dockerfile`, `cloud/web/nginx.conf`, `cloud/compose.yaml`, `cloud/README.md`
- Test: `tests/unit/test_cloud_bootstrap.py`, `tests/docker/test_cloud_stack.py`, `tests/docker/cloud_fake_ai.py`, `tests/e2e/test_cloud_site.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `cd cloud && docker compose up -d --build`. The website is at `http://localhost:8080`, with `PUBLIC_URL` for the Pi and `LOGIN_URL` for Google in `cloud/.env`. Only `web`'s port is published; `api` and `postgres` aren't reachable from outside.

- [ ] **Step 1:** Write failing tests:
  - `test_cloud_bootstrap.py`: `build` uses the real parts from settings, and every router is included. `test_every_route_needs_the_right_login` walks all routes and checks that only `claim`, `/auth/*` and health answer with no login.
  - `test_cloud_stack.py` (`docker`): the stack starts healthy, with migrations run. `test_upload_then_read_back` claims with a code made through the store, posts events, and reads them through `/v1` on `web`'s port. `test_report_from_fake_ai_appears` uses a compose override with `reports.provider: openai_compatible` pointed at `cloud_fake_ai.py`. `test_postgres_and_api_ports_are_closed`. `test_data_survives_a_restart`.
  - `test_cloud_site.py` (`e2e`): with the fake `GoogleLogin` and `web/dist`, log in, add a Pi, claim it through the API, see it listed, read a report.
- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3:** Write the parts. Follow the detector's `Dockerfile` for `uv` and a non-root user. Pin image versions. `reports` and `api` use one image with different commands. `nginx.conf` limits body size to match `ingest.max_body_kb`, and sets the real client address. `api` runs migrations before starting.
- [ ] **Step 4:** Write `cloud/README.md`: filling in `.env`, making a Google client ID, starting it, adding a Pi, your own API keys, and that everything is plain HTTP on the home network for now.
- [ ] **Step 5:** Run the tests, including `uv run pytest -m docker tests/docker/test_cloud_stack.py` and `uv run pytest -m e2e tests/e2e/test_cloud_site.py`. Expected: PASS.
- [ ] **Step 6:** Commit: "Run the cloud server in Docker with one command".

### Task 10: Try it for real, update the docs and finish

**Files:**
- Modify: `AGENTS.md` (a short "Cloud server" section), `README.md` (the cloud server is optional), `docs/ARCHITECTURE.md`, `docs/API.md`, `docs/plans/2026-10-30-pi-standalone-refactor-plan.md` (only if names changed)

- [ ] **Step 1:** With the stack running on this Mac and `desktalk` running here too, link it with `scripts/link_cloud.py` and a code made through the API. Expected: real events appear in `GET /v1/devices/{id}/events`. Check with SQL that no row contains to-do text, a `say` field or a picture field.
- [ ] **Step 2:** Stop the stack while `desktalk` keeps running, then start it again. Expected: `desktalk` carries on, and the events from the gap arrive.
- [ ] **Step 3:** Write the docs, including the Congressional App Challenge wording as a note to the user. It isn't in this repo.
- [ ] **Step 4:** Run everything and show the output: `uv run pytest -m "not slow"`, `uv run pytest -m docker`, `uv run pytest -m e2e`, `uv run pytest -m "not slow and not docker and not e2e" --cov`, `uv run ruff check <changed files>`, `uv run pyright`, `uv run vulture`, and `npm test` and `npm run build` in `cloud/web`.
- [ ] **Step 5:** Open the PR, then have a subagent review it with `/code-review high <PR number>`, and another fix what it finds. Re-run the tests and push to the same PR.
- [ ] **Step 6:** Tell the user what only they can check: a real Google login, a real report with their own API key, and a real Pi once the refactor is done.
