# Desk fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fewer false sips and phantom objects, a full object trace on the developer page, Mac notifications, a sensitivity slider, to-dos with due dates, no posture checks, a plain event log, and the page fixes the reviewers found.

**Architecture:** One foundation task first (settings, wording, interfaces, fakes, the posture removal), because nearly every piece touches those shared files. Then seven pieces run at once, each owning its own files. Then one wiring task connects the new parts in `bootstrap.py`, adds the route cases and docs, and runs every check.

**Tech Stack:** Python 3.12 + FastAPI (uv), plain JavaScript app page (`web_app/`), React + TypeScript developer page (`dev_ui/`, Vitest), pytest.

**Spec:** [docs/superpowers/specs/2026-10-25-desk-fixes-design.md](../specs/2026-10-25-desk-fixes-design.md). Read it before your task; the section numbers below (§1 ...) are its sections.

## Global Constraints

- AGENTS.md applies in full. Read it first. In short: no hardcoded numbers, timings, thresholds, paths or model names (they go in `config/settings.yaml` or `config/protocols.yaml` with a plain comment, plus a typed field in `desktalk/config/app.py` or `desktalk/config/habits.py`); wording goes in `config/prompts.yaml` (new blanks also in `BLANKS` in `desktalk/config/prompts.py`); `uv run ...`, never pip; `logging`, never `print`.
- Parts ask only for the interfaces they use (`desktalk/ports.py`); only `desktalk/bootstrap.py` knows the real classes. `desktalk/core/` and `desktalk/domain/` never import OpenCV, numpy, web or AI libraries.
- Tests first: write the failing test, watch it fail, then the code. While working run only your own test files (`uv run pytest tests/unit/test_<name>.py`).
- Never edit a file another task owns (each task lists its files). If you find you must, stop and report it instead.
- Ask a `Clock` for the time; never `time.time()`.
- Plain, everyday words in code comments, settings comments, wording, and commit messages. Commit messages: one short plain sentence, ending with the line `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- The app page and the developer page keep no copies of settings: `web_app/` reads `GET /api/ui_settings`, `dev_ui/` reads `GET /api/dev/settings`.
- `config/settings.local.yaml` is the user's own file: never edit it.
- Before you report done: `uv run ruff check <files you changed>` shows no new errors, and `uv run pyright <files you changed>` shows 0 errors. Paste the output in your report.

## Review Focus

1. **A covered camera while the cup is lifted.** The cup goes up, then the camera is covered, then uncovered with the cup back on the desk: no sip (the covered gap wins). Test in Task 2.
2. **A lifted cup that never comes back** (taken away to the kitchen): no sip, and no sip later when a different cup appears. Test in Task 2.
3. **The sensitivity preference is missing, out of range or not a number** (an old `data/preferences.json`, a hand edit): the middle step is used, nothing crashes. Tests in Task 1 (`_clean`) and Task 3.
4. **`osascript` missing, slow or failing** (not a Mac, notifications blocked): the chat message still goes out at once, and the failure is only logged. Test in Task 5.
5. **Due dates that are bad, empty or in the past**, and to-do lists from an old page (no `due` field): a bad date is dropped (the to-do stays, with no date); old lists still load. Tests in Task 6.

---

### Task 1: Foundation (settings, wording, interfaces, fakes, posture removal)

Runs alone, before everything else. Everything after it builds on what it adds.

**Files:**
- Modify: `config/settings.yaml`, `desktalk/config/app.py`, `config/protocols.yaml`, `desktalk/config/habits.py`, `config/prompts.yaml`, `desktalk/config/prompts.py`, `config/agents.yaml`
- Modify: `desktalk/ports.py`, `desktalk/domain/` (new plain data, below), `tests/fakes/` (fakes for every changed or new interface), `tests/contracts/` (suites for new interfaces)
- Modify: `desktalk/core/habits/preferences.py`, `desktalk/web/schemas.py` (the `sensitivity` preference only)
- Delete: `desktalk/core/chat/workers/vision_habits.py` and its tests; remove `VisionHabits` from `desktalk/core/chat/workers/__init__.py`, `bootstrap.py` (the import, the field, the job, the docstring lines) and anything only it used: `VisionHabitsView` in `ports.py`, its fake, `vision_habits:` settings and fields, the `vision:` picture prompt's posture / nail parts and the slouch lines in `prompts.yaml`, the vision brief in `desktalk/core/desk/character_memory.py` and its readers (`live_context.py`, `prompts.py` `{signals}`), the `vision` part of `GET /api/habits/today` in `desktalk/web/routers/habits.py`, and `tests/vulture_allow.py` lines for removed names. Keep `llm.vision_model` (lost items use it).
- Test: `tests/unit/test_config.py`, `tests/unit/test_core_habits_preferences.py`, the contract suites

**Interfaces (Produces): everything below, exactly these names.**

Settings (`config/settings.yaml` → typed fields). Values are starting values; comments in plain words:
- `agents.can_join: [computer, cup, phone, snack, paper]` (`Words`): agent ids that may join the chat.
- `agents.sensitivity_steps`: a list of 5 `{name, min_confidence, join_after_sec}`, from picky to eager: `Picky 0.65 10`, `Careful 0.55 5`, `Normal 0.45 3`, `Easy 0.40 2.5`, `Eager 0.35 2`. `agents.sensitivity_default: 2` (index of Normal). Remove `agents.join_after_sec` and `agents.min_confidence` (the steps replace them); update every reader to the new names (only `Roster`, `HabitReader` and the `ui_settings` route read them: check with grep).
- `agents.on_desk_after_sec: 3` and `agents.on_desk_min_confidence: 0.45`: the warnings' own "on the desk" list (§4 "Watch out"), not changed by the slider.
- `notifications:` `enabled: true`, `warnings: true`, `reminders: true`, `sips: true`, `time_limit_sec: 5`, `command: osascript`.
- `dev.trace_photos_kept: 6` (per kind of object), `dev.trace_photo_every_sec: 120`, `dev.trace_photos_folder: data/trace_photos`, `dev.trace_photo_quality: 80`, `dev.trace_events_kept: 50` (per kind of object).
- `web.camera_note_after_sec: 5` (§11 flaky camera note), `web.saved_note_sec: 4`, `web.retry_start_sec: 3`, and under `web.list_sizes` every count §11 names: `on_desk_shown: 8`, `camera_issues_shown: 2`, `risk_lines_shown: 6`, `side_photos_shown: 6`, `quote_chars: 90`, `toast_chars: 140`, `mention_max_chars: 12`, `counter_label_chars: 60`. Remove `list_sizes.log_detections` and any timeline numbers once Task 8 removes their readers (leave a note for Task 8; Task 1 adds, Task 8 removes).
- `protocols.yaml` `hydration`: `sip_lift_share: 0.08` (the drink's box must rise more than this share of the picture height), `sip_back_share: 0.15` (put back within this share of the picture from where it rested), `sip_cover_share: 0.6` (when this share or more of what was on the desk vanishes in the same picture, it's a covered camera).
- `protocols.yaml` `tasks`: `remind_after_min: 120` (was 40), `due_soon_days: 1`, `late_repeat_hours: 24`.
- `habit_tracker.todos_key: todos` (the store key for the to-do list).

Wording (`config/prompts.yaml`, typed in `desktalk/config/prompts.py`):
- `habits.tasks_due`: `tomorrow`, `today`, `late` texts with blanks `{task}` and `{days}` (e.g. "Remember, your {task} is due tomorrow.", "Your {task} is due today.", "Your {task} was due {days} day{plural} ago. Done? Tick it off."), each with `evidence` and `tip` like `tasks_open`.
- `habits.tasks_open`: text names one to-do (`{task}`), not "{count} open tasks, like {first}".
- `habits.logged.task_done`: "Nice, \"{task}\" is done!".
- `trackers`: the AI's to-do tag help says the date form: `[ADD_TODO:Essay|2026-10-30]`.
- `ui.starter_todo` (moved from `web_app/app.js` line ~169) and `ui.starter_counters: ["Picked up phone"]`.
- `notifications.sip_title`, and anything else Task 5 shows.
- §11 plain words: the sentences the page shows instead of "34px", "12% closer in 5 frames" (`desk_rules` / `desk_checks` lines in `prompts.yaml` ~757-762).

`config/agents.yaml`: `paper.name: Book`; remove `breaks` from `watch.habits`, `light` from `lamp.habits`; Computer's `about:` drops "posture".

Interfaces (`desktalk/ports.py`), each with a fake in `tests/fakes/` and a contract suite:
- `DeskScene`: add `set_on_desk(objects: Mapping[str, SeenObject], now: float) -> None` and `on_desk() -> dict[str, SeenObject]` ("everything on the desk right now, by agent id, whether or not it's in the chat"). `members()` keeps meaning "in the chat".
- New `DesktopNotifications(Protocol)`: `show(title: str, text: str) -> None` (never raises, never blocks).
- New plain data `ChatJoin` in `desktalk/domain/agents.py`: `agent: str, label: str, kind: str ('joined'|'left'|'refused'), ts: float, confidence: float, seen_for_sec: float, on_desk: bool | None, step: str` (sensitivity step name; `refused` = seen long enough but not on `can_join`).
- New `ChatJoins(Protocol)`: `joins(label: str) -> list[ChatJoin]` (oldest first).
- New plain data `TracePhoto` in `desktalk/domain/pictures.py`: `id: str, label: str, ts: float, why: str ('appeared'|'joined'|'every'), confidence: float, box: tuple[float, float, float, float], size: tuple[int, int]`.
- New `TracePhotos(Protocol)`: `photos(label: str) -> list[TracePhoto]`, `jpeg(photo_id: str) -> bytes | None`.
- New plain data `Todo` in `desktalk/domain/habits.py`: `text: str, done: bool = False, due: str | None = None` (`YYYY-MM-DD`).
- New `TodoList(Protocol)`: `items() -> list[Todo]`, `replace(items: Sequence[Todo], now: float) -> list[Todo]` (returns what's kept; publishes a `HabitEvent(habit='tasks', action='done')` for each to-do that turned done), `has_list() -> bool` (false until the first save).
- `DeskStatus.open_todos() -> list[Todo]` (was `list[str]`), and `HabitsView.nudges(now, open_todos: Sequence[Todo])`; update the fakes, and the two callers' type hints only (Task 5 and 6 own the files' logic).

`DeskTalk` (in `bootstrap.py`): add `chat_joins: ChatJoins | None = None`, `trace_photos: TracePhotos | None = None`, `todos: TodoList | None = None` at the end (with the other defaults), so the route tasks can set fakes on a built app in their tests (`app.todos = FakeTodoList()`). Task 9 fills them with the real parts.

Preferences: `PreferencesFile._clean('sensitivity', value)` keeps an int clamped to `0..len(steps)-1`; a missing or bad value gives `agents.sensitivity_default` (pass the step count and default into `PreferencesFile`). `PreferenceChanges` in `schemas.py` accepts `sensitivity: int | None`.

- [ ] **Step 1: Make a branch.** `git switch -c desk-fixes`.
- [ ] **Step 2: Write failing tests.** In `tests/unit/test_config.py`: `test_sensitivity_steps_load` (5 steps, names in order, default 2), `test_notifications_section_loads`, `test_hydration_sip_shares_load`. In `tests/unit/test_core_habits_preferences.py`: `test_sensitivity_defaults_to_middle`, `test_sensitivity_clamped` (`update({'sensitivity': 9})['sensitivity'] == 4`, `-3 → 0`), `test_sensitivity_bad_value_raises_value_error` (`'abc'`). Contract suites for `DesktopNotifications`, `ChatJoins`, `TracePhotos`, `TodoList` run against the fakes (the real classes join in their tasks).
- [ ] **Step 3: Run them, see them fail.** `uv run pytest tests/unit/test_config.py tests/unit/test_core_habits_preferences.py tests/contracts -q`
- [ ] **Step 4: Add the settings, wording, interfaces, plain data, fakes and the preference.**
- [ ] **Step 5: Remove posture and nail biting** (the list under Files). `uv run vulture` and `uv run pyright` must be clean after.
- [ ] **Step 6: Run the fast suite once** (this task touches shared files): `uv run pytest -m "not slow and not docker and not e2e" -q`. Expected: all pass except tests that needed the removed parts, which you delete with them.
- [ ] **Step 7: Commit.** "Add the new settings and interfaces, and remove the posture checks"

---

Tasks 2 to 8 start together once Task 1 is committed. Each owns only its listed files.

### Task 2: Sips are lifted and put back (§1)

**Files:**
- Modify: `desktalk/core/habits/habit_reader.py` (`HabitReader._sips`, `read`, new helpers)
- Test: `tests/unit/test_core_habits_tracker.py` (or a new `tests/unit/test_core_habits_sips.py`)

**Interfaces:** Consumes `protocols.hydration.sip_lift_share`, `sip_back_share`, `sip_cover_share`, `sip_gone_min_sec`, `sip_gone_max_sec`. Produces nothing new: sips still come out as `log_sip(..., 'camera', ...)`.

How it works (the part the tests don't pin): per drink key (`drink:cup` ...), keep its resting box (the box while it sat still) and its latest box. "Lifted" = the box's center rose by more than `sip_lift_share` of the picture height above its resting center (in pixel y, up is smaller). A sip is counted when a lifted drink is next seen settled within `sip_back_share` of its resting spot (center distance as a share of the picture's diagonal), and the time from leaving rest to settling is within `[sip_gone_min_sec, sip_gone_max_sec]`. A drink that vanishes after being seen lifted counts when it comes back settled. A drink that vanishes without being lifted never counts. "Covered": in the picture where the drink first went missing, at least `sip_cover_share` of the keys present in the picture before went missing too (or the picture had no detections at all) → that gap can't make a sip. Use the event's boxes (`event.detections`, best box per label) and `event.size`.

- [ ] **Step 1: Write failing tests** (feed `DetectionEvent`s through `HabitTracker` with `FakeClock` and a picture of 640×480, cup box 40×60, three pictures a second):
  - `test_lift_and_put_back_is_a_sip`: cup rests 10 s, rises 80 px over 1 s, held 4 s up, comes back to rest → `day.sips == 1`.
  - `test_hand_over_cup_is_not_a_sip`: cup rests, vanishes 5 s in place, comes back to the same spot → `0`.
  - `test_covered_camera_is_not_a_sip`: cup, laptop and phone all vanish in the same picture for 5 s, then all come back → `0`.
  - `test_lifted_then_lost_then_back_is_a_sip`: cup rises 80 px, vanishes 6 s, comes back to rest → `1`.
  - `test_lifted_then_camera_covered_is_not_a_sip` (Review Focus 1): cup rises, then everything vanishes 5 s, then back → `0`.
  - `test_lifted_never_back_is_no_sip` (Review Focus 2): cup rises and vanishes; 5 min later a cup appears elsewhere → `0`.
  - `test_put_back_too_late_is_no_sip`: lifted and back after 3 min (`sip_gone_max_sec` 120) → `0`.
  - `test_small_wobble_is_no_sip`: the cup's box moves 10 px up and back → `0`.
- [ ] **Step 2: Run, see them fail.** `uv run pytest tests/unit/test_core_habits_sips.py -q`
- [ ] **Step 3: Implement** in `habit_reader.py`. Keep the old tests passing; change those that pinned "gone and back = sip" to the new rule.
- [ ] **Step 4: Run, see them pass.** Same command, plus `tests/unit/test_core_habits_tracker.py`.
- [ ] **Step 5: Commit.** "Count a sip only when the drink is lifted and put back"

### Task 3: Who joins the chat, sensitivity, and the warnings' own list (§4, §6)

**Files:**
- Modify: `desktalk/core/agents/roster.py`, `desktalk/core/desk/scene.py`, `desktalk/core/desk/whole_desk.py`, `desktalk/core/vision/desk_monitor.py`, `desktalk/adapters/picture/painter.py`
- Test: `tests/unit/test_core_agents_roster.py`, `tests/unit/test_core_vision_desk_monitor.py`, `tests/unit/test_adapters_picture_painter.py`

**Interfaces:**
- Consumes: `agents.can_join`, `agents.sensitivity_steps`, `agents.sensitivity_default`, `agents.on_desk_after_sec`, `agents.on_desk_min_confidence`, `Preferences.get('sensitivity')`, `DeskScene.set_on_desk/on_desk`, `ChatJoin`.
- Produces: `Roster(bus, profiles, scene, agents, presence, prefs: Preferences, *, on_desk=None, clock: Clock)` implementing `RosterView`, `RosterList` and `ChatJoins` (`joins(label)`: the last `dev.trace_events_kept` per label; pass that number in as `joins_kept: int`).

Behaviour:
- Two presence trackers. **Chat:** only detections whose profile id is in `can_join`, at the current step's `min_confidence` (or the profile's own `min_confidence` if higher) and `join_after_sec`; leave after `agents.leave_after_sec` as now. The step is read from `prefs.get('sensitivity')` on every picture (so a change works at once); a bad value uses `sensitivity_default`. `PresenceTracker.appear_sec` may be changed in place (it's a plain attribute). **On the desk:** every profile (including `default` ones, never ignored words), at `on_desk_min_confidence` and `on_desk_after_sec`, inside the marked desk as today; written with `scene.set_on_desk(...)` (fresh within `presence.disappear_after_sec`, like members).
- A detection seen long enough but not on `can_join` records one `ChatJoin(kind='refused')` per stretch of sightings (not per picture). Joins and leaves record `ChatJoin` with `seen_for_sec`, `confidence`, `on_desk` (the outline check's answer, `None` when no outline) and the step name.
- No more AI-invented personalities for objects that can't join: nothing to change if `invent_personality` only fires on join (check `message_generator.on_roster`); if it fires elsewhere, report it.
- `DeskMonitor.on_detection` uses `self._desk.on_desk()` instead of `members()`. Speaker rule in `_safety_alerts`: the fragile target if it's in `members()`, else the intruder if it's in `members()`, else the other one if in `members()`, else `LENS`. Edge dangers: the object if in `members()`, else `LENS`. `MonitorDesk` gains nothing new (DeskScene already has both).
- `DeskPainter` draws boxes for `on_desk()` (so the live view shows what the warnings see).

- [ ] **Step 1: Write failing tests:**
  - `test_glasses_never_join`: glasses at 0.9 for 30 s → no `AgentJoined`; `joins('glasses')[-1].kind == 'refused'`.
  - `test_unknown_object_never_joins` ("remote").
  - `test_cup_joins_after_step_wait`: step Normal: joins after 3 s, not at 2.9 s; step Picky: not at 9 s, joins after 10 s.
  - `test_slider_change_works_at_once`: `FakePreferences` changed from 2 to 0 mid-run → the next object needs 10 s.
  - `test_weak_box_counts_at_eager_not_picky`: cup at 0.4 joins at Eager, never at Picky.
  - `test_profile_own_min_confidence_wins_when_stricter`.
  - `test_bad_sensitivity_uses_default` (Review Focus 3): prefs gives `'x'` / `None` / `99` → Normal's numbers.
  - `test_on_desk_list_has_non_chat_objects`: scissors seen 3 s → in `scene.on_desk()`, not in `members()`.
  - `test_join_records_reason`: `joins('cup')[-1]` has `kind='joined'`, the confidence, `seen_for_sec >= 3`, step `Normal`.
  - Desk monitor: `test_scissors_near_phone_still_warns` with phone not in the chat → an `Alert` with `agent == LENS`; `test_fragile_member_speaks` (phone in chat, knife not → phone speaks).
  - Painter: `test_paints_on_desk_objects`.
- [ ] **Step 2: Run, see them fail.** `uv run pytest tests/unit/test_core_agents_roster.py tests/unit/test_core_vision_desk_monitor.py tests/unit/test_adapters_picture_painter.py -q`
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run, see them pass.**
- [ ] **Step 5: Commit.** "Let only the objects that matter join the chat, with a sensitivity setting"

Report for Task 9: the exact constructor call `bootstrap.py` must make.

### Task 4: The object's trace on the developer page (§2)

**Files:**
- Create: `desktalk/core/object_states/trace_photos.py` (`TracePhotoTaker`), `desktalk/adapters/storage/trace_photo_files.py` (`TracePhotoFiles`, the files on disk)
- Modify: `desktalk/web/routers/dev.py`, `dev_ui/src/api.ts`, `dev_ui/src/tabs/Live.tsx`, new `dev_ui/src/ObjectTrace.tsx` (+ `.test.tsx`), `dev_ui/src/style.css`
- Test: `tests/unit/test_core_object_states_trace_photos.py`, `tests/unit/test_adapters_storage_trace_photo_files.py`, `tests/unit/test_web_dev_trace.py` (route tests with fakes; the characterization case is Task 9's), `dev_ui` Vitest

**Interfaces:**
- Consumes: `ChatJoins.joins(label)`, `TracedObjectStates.changes(since)` / `trace(label)`, `LivePictures.plain()`, `TracePhotos`, `dev.trace_*` settings, `Clock`.
- Produces:
  - `TracePhotoFiles(folder: Path, kept: int)`: a `KeyValueStore`-like file keeper: `add(photo: TracePhoto, jpeg: bytes) -> None` (writes `<folder>/<label slug>/<id>.jpg` and a small JSON index per label, deleting the oldest beyond `kept`), `photos(label) -> list[TracePhoto]`, `jpeg(photo_id) -> bytes | None`. Implements `TracePhotos`. Gets the real-class contract test.
  - `TracePhotoTaker(bus: EventBus, pictures: LivePictures, files: TracePhotoFiles-as-a-small-Protocol, settings: DevSettings, clock: Clock)`: subscribes to `DetectionEvent`, `ObjectStateChanged` and `AgentJoined`. On `ObjectStateChanged(change='appeared')` → a photo (`why='appeared'`); on `AgentJoined` → `why='joined'`; while an object is PRESENT, one `why='every'` per `trace_photo_every_sec`. The box and confidence come from the newest `DetectionEvent`'s best box for that label; the JPEG is `pictures.plain()`. Never raises out of a handler (log and go on).
  - Route `GET /api/dev/objects/{label}` → `{label, timeline: [{ts, source: 'state'|'chat', kind, confidence, seen_for_sec, on_desk, step, before, after}], photos: [{id, ts, why, confidence, box, size}]}` (timeline oldest first, mixing `object_states.changes(...)` filtered to the label and `roster.joins(label)`; `label` is the detector word; chat joins are looked up through `profiles.for_label(label)` → its id).
  - Route `GET /api/dev/objects/photo/{photo_id}` → `image/jpeg`, 404 when gone.
  - `DeskTalk` gets fields `trace_photos: TracePhotos | None` (None when `dev.enabled` is off) and `chat_joins: ChatJoins`; Task 9 wires them. Until then, your route tests build the router with fakes.
- dev UI: clicking an object's card on the Live tab opens its trace panel (the existing "Show its smoothing" button goes; the whole card is a button). The panel shows the timeline (time, what happened, and why in plain words: "seen for 3.2 s, 0.81 sure, inside the desk, sensitivity Normal"; "refused: not on the chat list"), the smoothing chart (existing `Smoothing`), and the photos: each `<img>` with an SVG box overlay drawn from `box` and `size`, and its time, reason and confidence. Numbers like how many timeline rows come from `GET /api/dev/settings` (add `trace_events_kept`, `trace_photos_kept` there).

- [ ] **Step 1: Write failing tests:**
  - `test_files_keep_only_newest` (kept 2, add 3 → the oldest file is gone from disk and the index).
  - `test_photo_on_appear_and_join`, `test_every_photo_while_present` (FakeClock moves 2 × `trace_photo_every_sec` → 2 `every` photos), `test_no_picture_no_photo` (`plain()` is None → nothing saved, nothing raised).
  - Route: `test_object_trace_mixes_states_and_joins` (one appeared + one joined + one refused, in time order), `test_object_photo_404`.
  - Vitest: `ObjectTrace.test.tsx`: renders timeline rows and a box overlay with the right `x/y/width/height` in the SVG's `viewBox`; clicking a card in `Live.test.tsx` opens the panel.
- [ ] **Step 2: Run, see them fail.** `uv run pytest tests/unit/test_core_object_states_trace_photos.py tests/unit/test_adapters_storage_trace_photo_files.py tests/unit/test_web_dev_trace.py -q` and `cd dev_ui && npm test`
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run, see them pass**, plus `cd dev_ui && npm run build`.
- [ ] **Step 5: Commit.** "Show each object's whole trace on the developer page, with pictures"

### Task 5: Mac notifications (§5)

**Files:**
- Create: `desktalk/adapters/notify/__init__.py`, `desktalk/adapters/notify/mac.py` (`MacNotifications`, `NoNotifications`, `make_notifications(settings, platform: str)`), `desktalk/core/chat/desktop_alerts.py` (`DesktopAlerts`)
- Modify: `desktalk/core/habits/reminders.py`, `desktalk/core/chat/workers/lens.py`, `desktalk/core/chat/message_generator.py` (`on_habit`, `on_alert`), `desktalk/core/chat/alert_voice.py` (only if the warning flag is best set there)
- Test: `tests/unit/test_adapters_notify_mac.py`, `tests/unit/test_core_chat_desktop_alerts.py`, the existing reminders / lens / message generator tests

**Interfaces:**
- Consumes: `DesktopNotifications`, `Notifier`, `Preferences.quiet_now(now)`, `Profiles`, `Clock`, `NotificationsSettings`.
- Produces:
  - Messages that should pop up carry `notify: 'warning' | 'reminder' | 'sip'`: `reminders.py` adds `'notify': 'reminder'`; Lens adds `'notify': 'warning'` when `kind == 'problem'`; `on_alert` adds `'notify': 'warning'` to the message from `message_for_alert`; `on_habit` adds `'notify': 'sip'` when `ev.habit == 'hydration' and ev.action == 'sip' and ev.extra.get('how') == 'camera'`. Join/leave, greetings, small talk and replies never carry it.
  - `DesktopAlerts(inner: Notifier, desktop: DesktopNotifications, prefs: Preferences, profiles: Profiles, settings: NotificationsSettings, clock: Clock)` implements `Notifier`: `send(message)` returns `inner.send(message)` first, then, if `message.get('notify')` is on (the main switch and that kind's switch) and not `prefs.quiet_now(now)`, calls `desktop.show(name, text)` where `name` is the character's profile name (Lens: `ui.lens_card` name from prompts) and `text` is the message's text with action tags stripped.
  - `MacNotifications(settings, runner=subprocess.run)`: `show()` hands the work to one background thread (a small queue; when the queue is full, drop and log), which runs `[settings.command, '-e', 'display notification <text> with title <title>']` with AppleScript strings escaped (backslash and double quote), `timeout=settings.time_limit_sec`; any error is logged once per kind of error. `make_notifications(settings, sys.platform)` returns `NoNotifications` (logs once "Notifications only work on a Mac") when the platform isn't `darwin` or `enabled` is off.
- Task 9 wires it: `feed = DesktopAlerts(hub, make_notifications(...), preferences, profiles, config.notifications, clock)` and passes `feed` as the notifier to `MessageGenerator`, `HabitReminders` and `Lens` (the web still subscribes to `hub`).

- [ ] **Step 1: Write failing tests:**
  - `test_reminder_message_pops_up` (FakeDesktop records `('Cup', 'Time for water')`), `test_join_note_does_not`, `test_quiet_hours_mute_everything` (warnings too), `test_kind_switch_off`, `test_main_switch_off`, `test_chat_message_sent_even_if_desktop_raises`.
  - `test_osascript_command_escapes_quotes` (text `say "hi" \ there` → a valid AppleScript string), `test_slow_osascript_does_not_block` (fake runner sleeps on the worker thread; `show()` returns at once), `test_runner_error_is_logged_not_raised` (Review Focus 4), `test_not_a_mac_gives_no_notifications`.
  - `test_camera_sip_message_flagged`, `test_button_sip_not_flagged`, `test_alert_message_flagged`, `test_lens_problem_flagged_fixed_not`, `test_nudge_message_flagged`.
- [ ] **Step 2: Run, see them fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run, see them pass** (plus the existing reminders, lens and message generator tests).
- [ ] **Step 5: Commit.** "Show warnings, reminders and sips as Mac notifications"

### Task 6: To-dos the server keeps, with due dates (§7)

**Files:**
- Create: `desktalk/core/habits/todos.py` (`TodoFile`)
- Modify: `desktalk/core/habits/habit_rules.py` (`task_nudges`, `RuleState.open_todos`, `RuleState.today`), `desktalk/core/habits/tracker.py` (builds `RuleState` in `nudges()`: pass the local day and the rotation index), `desktalk/core/habits/desk_status.py` (`open_todos`; the constructor takes a `TodoList` instead of reading the widgets), `desktalk/core/chat/tracker_actions.py` (`[ADD_TODO:text|date]`, `normalize_todo` keeps `due`), `desktalk/web/routers/chat.py` (`/api/chat_todos`, `/api/chat_todos_sync`, new `GET /api/todos`), `desktalk/web/schemas.py` (the to-do shapes with `due`)
- Test: `tests/unit/test_core_habits_todos.py`, `tests/unit/test_core_habits_habit_rules.py`, `tests/unit/test_core_chat_tracker_actions.py`, `tests/unit/test_core_habits_desk_status.py`, route tests in `tests/unit/test_web_todos.py`

**Interfaces:**
- Consumes: `Todo`, `TodoList`, `KeyValueStore`, `EventBus`, `Clock`, `habit_tracker.todos_key`, `chat.todo_max_items`, `chat.todo_max_chars`, `protocols.tasks.*`, `habits.tasks_due` / `tasks_open` / `logged.task_done` wording.
- Produces:
  - `TodoFile(store: KeyValueStore, bus: EventBus, clock: Clock, *, key: str, max_items: int, max_chars: int, done_words: str)` implements `TodoList`. Saved as `{'items': [{text, done, due}]}`. `replace()` cleans each item (text cut to `max_chars`, `due` kept only if it parses as `YYYY-MM-DD`, else `None`), keeps at most `max_items`, saves, and publishes `HabitEvent(ts=now, habit='tasks', action='done', say=done_words.format(task=text), extra={'task': text})` for each item that was open before and is done now (matched by text). Gets the real-class contract test.
  - `GET /api/todos` → `{has_list: bool, items: [{text, done, due}]}`. `POST /api/chat_todos` and `/api/chat_todos_sync` also call `app.todos.replace(...)` with the list's items (they still call `generator.save_widget` for the AI). `DeskTalk.todos: TodoList` (Task 9 wires it; `DeskStatusService` gets the `TodoList` instead of reading widgets).
  - `DeskStatusService.open_todos() -> list[Todo]` = the open items of the `TodoList`.
  - `task_nudges(s, cfg, now, words, local_day)`: per open to-do with a due date, one nudge key `tasks.due.<text>.<kind>` where kind is `tomorrow` (due in `due_soon_days` days), `today`, or `late` (past due; `repeat_after_sec = late_repeat_hours * 3600`); `tomorrow`/`today` have a repeat long enough to fire once that day (`24 * 3600`). The date math uses the desk's local day (pass `clock.local_day(now)` in through `RuleState` as `today: str`). Without due dates: one `tasks.open` nudge every `remind_after_min`, naming the open to-do after the one named last time (keep the index in `RuleState`/the tracker's `_last_fire`; rotate in list order).
  - `tracker_actions`: `[ADD_TODO:Essay|2026-10-30]` → `{'action': 'add_todo', 'text': 'Essay', 'due': '2026-10-30'}`; a bad date → no `due`. `ACTION_TAG` still strips it.
- [ ] **Step 1: Write failing tests:**
  - `test_due_tomorrow_reminds_once`, `test_due_today_reminds`, `test_late_reminds_daily` (FakeClock over 3 days → 3 late nudges, one a day), `test_no_due_rotates_names` (3 open to-dos, 3 check-ins name a, b, c), `test_done_todo_no_reminder`, `test_check_in_every_two_hours` (none at 119 min, one at 120).
  - `test_bad_due_dropped_todo_kept` (`'next friday'`, `''`, `'2026-02-30'` → `due is None`), `test_old_list_without_due_loads` (Review Focus 5), `test_ticking_off_publishes_task_done` (one `HabitEvent` with `action='done'`, none when an already-done item is re-sent), `test_list_survives_restart` (new `TodoFile` on the same `InMemoryKeyValueStore`).
  - `test_add_todo_tag_with_date`, `test_add_todo_tag_bad_date`.
  - Route: `test_get_todos_before_any_save` (`has_list` false), `test_sync_saves_and_get_returns`.
- [ ] **Step 2: Run, see them fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run, see them pass.**
- [ ] **Step 5: Commit.** "Keep the to-do list on the server and remind about due dates"

### Task 7: The app page (§3 wording, §4 Book, §6 slider, §7 dates, §8, §9, §10 page, §11)

**Files:**
- Modify: `web_app/app.js`, `web_app/index.html`, `web_app/style.css`
- Test: the e2e tests in `tests/e2e/` that cover these pages (update the ones that break; add `test_sensitivity_slider_saves`, `test_todo_due_date_shown`, `test_event_log_shows_only_habits`); run with `uv run pytest -m e2e` at the end of the task

**Interfaces (Consumes, from other tasks; build against these shapes):**
- `GET /api/ui_settings`: the new `list_sizes`, `web.camera_note_after_sec`, `web.saved_note_sec`, `web.retry_start_sec`, `starter_todo`, `starter_counters`, `sensitivity_steps: [{name, min_confidence, join_after_sec}]` (Task 8 adds them to the route).
- `GET/PUT /api/preferences`: `sensitivity: int`.
- `GET /api/todos` (Task 6). To-do items carry `due: 'YYYY-MM-DD' | null`; the chat's `add_todo` action may carry `due`.
- `GET /api/log/events?day=&types=` returns only the shown types (Task 8); filters are `habit`, `nudge`, `alert,camera`.
- `GET /api/live` (Task 8) no longer sends the fields the page doesn't read; `GET /api/snapshots?limit=N`.

What changes (see the spec for each):
- Settings page: the sensitivity slider (5 steps, the step's name and its two numbers in plain words: "needs to be fairly sure (65%) and seen for 10 seconds"); quiet hours as time pickers; the §11 Settings bugs (unsaved fields survive a zone change, save messages say what failed, hours checked, "Saved" clears after `saved_note_sec`, "Warn at" can't go closer than "Alert at", "Clear all chats" confirms and remembers when, so `loadRecentMessages` skips older messages); real `<label>`s; no `config/settings.yaml` or `python server.py` wording; the Pi command in one place only.
- Book's panel: a date box next to "Add a task"; each to-do shows its due date; late ones stand out. On start, load `GET /api/todos`; use the server's list when `has_list`, else send the browser's copy once.
- Water panel: no "+ Glass" (panel and Habits page); one plain line about the lift-and-put-back rule. Computer panel: no "min slouching". Remove the light tracker panel.
- Habits page: no nail biting card, no Camera log, starter counters from `ui.starter_counters`; counters notice a new day; "desk warnings"; sunlight buttons "Clear sky, 10 min" ...; afternoon light on the card; plain words (no `/255`, lux, "Protocols", evidence lines).
- Event log page: only the events list with filters Everything / Habits / Reminders / Warnings and the day chips (all `keep_days`, "Today" = null, the server's today). No camera numbers, Compact now, timeline, raw printout, joins. One request per refresh.
- Chat view (§11): rename the local `lens` in `renderRisks`; don't rebuild a tracker panel or the Habits page while one of its inputs has focus or text, and skip when unchanged; re-fetch recent messages when the stream reconnects or the offline note goes away; retry `ui_settings`/`status` every `retry_start_sec` (doubling) and switch to live when it answers; keep the QR when the reply's `qr_svg` is null; typing dots stay until that reply; the camera note only after `camera_note_after_sec`; pause polling while `document.hidden`; load the live video only while the camera panel is open; update the on-desk and chat lists only when changed; side photos with `?limit=`; Captures shows "Couldn't load photos" + retry, refreshes while open, honest intro, dates; plain words throughout; hardcoded counts from `ui.list_sizes`; accessibility items (buttons for photos and `data-open` links, named bars and counter buttons, `aria-expanded`).
- Fallback agents list (line ~1331): Paper → Book, no "posture".

- [ ] **Step 1: Write the failing e2e tests** above and run them: `uv run pytest -m e2e tests/e2e -q` (they fail).
- [ ] **Step 2: Make the changes**, page by page.
- [ ] **Step 3: Run the e2e tests until they pass.** (They need Tasks 6 and 8 for the server side: if those aren't merged yet in your copy, run them in Task 9 and say so.)
- [ ] **Step 4: Commit.** "Tidy the app page: plain words, the sensitivity slider, due dates, and the page fixes"

### Task 8: The event log, live and photo routes (§10, §11 speed)

**Files:**
- Modify: `desktalk/web/routers/log.py`, `desktalk/web/routers/live.py`, `desktalk/web/routers/video.py`, `desktalk/web/routers/habits.py`, `desktalk/web/routers/settings.py` (`/api/ui_settings`), `desktalk/adapters/storage/event_log.py` (only if `events()` needs a habit filter), `config/settings.yaml` + `desktalk/config/app.py` only to remove settings whose last reader you remove
- Test: `tests/unit/test_web_*.py` for these routers (route tests with fakes)

**Interfaces (Produces):**
- `GET /api/log/events?day=&types=&limit=`: `types` defaults to `habit,nudge,alert,camera` and may only name those four (anything else is ignored). `habit` events from the camera's light readings stay out (`habit in ('hydration', 'breaks', 'food', 'tasks', 'light', 'phone_free')` and `action != ''`; check what `light` habit events exist and keep only sunlight). Alerts get a plain `subtitle` (no raw `feature`; `near` name only when there is one).
- `GET /api/log/days` returns up to `event_log.keep_days` days and `today` (`clock.local_day(now)`).
- Remove `GET /api/log/presence`, `GET /api/log/detections`, `POST /api/log/compact`, `GET /api/habit_log` and their characterization cases, `docs/API.md` rows, `list_sizes.log_detections`, timeline settings, and update `tests/vulture_allow.py`. Keep `GET /api/log/status` (the developer page uses it). Remove `EventStore` methods only if nothing else uses them (`presence()` / `recent_detections()` / `counts()`: grep, including `dev.py`; if you remove one, remove it from `ports.py`, the fake and the contract suite too, since Task 1 is done).
- `GET /api/live`: only `camera`, `agents`, `objects[].class/confidence`, `extras`, `safety.explanations` (plain sentences from `prompts.yaml`), `camera_issues`, `ai`, `phone`, `pi_command`, `qr_svg` (as today). First grep `dev_ui/src/` for `/api/live`; if the developer page reads a field, move that field to a new `/api/dev/...` route instead (in that case tell Task 4's owner in your report; the route goes in `dev.py`, which Task 4 owns, so give Task 9 the exact code).
- `GET /api/snapshots?limit=N` (newest first; no `limit` = all, as today).
- `GET /api/ui_settings`: add the Task 1 `list_sizes`, `web.camera_note_after_sec`, `saved_note_sec`, `retry_start_sec`, `starter_todo`, `starter_counters`, `sensitivity_steps`; remove what the page no longer reads (`pi.compact_every_min` etc.: grep `web_app/app.js` after Task 7's list).

- [ ] **Step 1: Write failing route tests:** `test_log_events_only_shown_types` (a log with `agent_joined`, `detection`, `object_state`, `habit` sip → only the sip), `test_log_events_ignores_other_types_asked`, `test_log_days_has_today_and_keep_days`, `test_alert_subtitle_plain`, `test_live_reply_has_only_page_fields` (exact key set), `test_snapshots_limit`, `test_ui_settings_has_sensitivity_steps`, `test_removed_routes_are_404`.
- [ ] **Step 2: Run, see them fail.**
- [ ] **Step 3: Implement and remove.**
- [ ] **Step 4: Run, see them pass.**
- [ ] **Step 5: Commit.** "Trim the event log and live routes to what the app page shows"

---

### Task 9: Wire it together and check everything

Runs after Tasks 2 to 8. One worker (the lead).

**Files:**
- Modify: `desktalk/bootstrap.py` (build `Roster` with prefs and clock; `scene.set_on_desk` users; `TracePhotoFiles` + `TracePhotoTaker` when `dev.enabled`; `DesktopAlerts` around `hub`; `TodoFile`; `DeskStatusService` with the `TodoList`; new `DeskTalk` fields `chat_joins`, `trace_photos`, `todos`; `PreferencesFile` with the step count and default; the docstrings), `tests/integration/app_kit.py` if it builds the app by hand, `tests/characterization/cases.py` (cases for `/api/dev/objects/{label}`, `/api/dev/objects/photo/{id}`, `/api/todos`, and the changed ones), `docs/API.md`, `docs/ARCHITECTURE.md`, `README.md` where it mentions posture, the glass button, Paper, or the old sip rule, `tests/vulture_allow.py`
- Test: `tests/integration/`, the whole suite

- [ ] **Step 1: Write a failing integration test** `tests/integration/test_desk_fixes.py`: with the app built by `build(..., synchronous_events=True)` and fakes, glasses never join while cup does; a camera sip logs one sip and the `FakeDesktop` gets a "Sip" notification; a to-do due tomorrow reminds through the hub and the desktop; ticking it off shows in `/api/log/events`.
- [ ] **Step 2: Wire `bootstrap.py`** until it passes.
- [ ] **Step 3: Characterization cases and docs.**
- [ ] **Step 4: Full checks, showing the output of each:**
  - `uv run pytest -m "not slow and not docker and not e2e" --cov` (coverage at or above `fail_under`)
  - `uv run pytest -m slow` (detection changed)
  - `uv run pytest -m e2e`
  - `uv run ruff check <every changed file>`
  - `uv run pyright` → 0 errors
  - `uv run vulture` → nothing
  - `cd dev_ui && npm test && npm run build`
- [ ] **Step 5: Commit.** "Connect the new parts and update the docs"
- [ ] **Step 6: Open the PR, run `/code-review high <PR>` in a subagent, fix what it finds in another subagent, re-run the checks, push** (AGENTS.md "When the code is done").
