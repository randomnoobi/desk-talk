# Object State Machine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

Date: 2026-10-22, updated 2026-10-23 with Sam's answers, and moved onto the `refactor` code layout the same day.
Status: **draft, waiting for your OK.** No code has changed yet.

**Goal:** Turn the detector's jumpy guesses into a calm state for each object on the desk (`UNKNOWN`, `PRESENT`,
`ABSENT`), with an event when it really changes (`appeared`, `removed`, `moved`), saved in SQLite. The person's
state becomes the on/off switch for reminders, idle chat and vision checks. Then switch on the rule engine's
hydration rule, which reads these states.

**Architecture:** Two pure pieces (smoothing, and a `step()` function that never reads the clock) in
`desktalk/core/object_states/`, and one tracker that listens to detection events, runs them, publishes
`ObjectStateChanged` events and writes them to an append-only SQLite table. The tracker implements
`ObjectStatesView` from `desktalk/ports.py`, so the rule engine and `DeskStatus` read the states through that
interface. The SQLite file sits behind a new `ObjectStateStore` interface, in `desktalk/adapters/storage/`.
Everything is built and connected in `desktalk/bootstrap.py`.

**Tech Stack:** Python 3.12, uv, pydantic (the config models), `sqlite3` and `statistics` (standard library),
pytest, ruff.

**Spec:** this file's own first half ("How it works", sections 1–5), agreed with Sam on 2026-10-23, the sketch
(Observation → State machine → Events, saved to SQLite), and [the rule engine plan](2026-10-23-rule-engine-plan.md)
for what reads the states.

## In short

Today the detector says something new 3 times a second: "bottle 0.52", "bottle 0.48", "nothing", "bottle 0.51". That's too jumpy to act on. This plan adds a layer that turns those jumpy guesses into a calm answer for each object:

- **A state:** `UNKNOWN`, `PRESENT` or `ABSENT`.
- **An event when the state really changes:** `appeared`, `removed` or `moved`.
- **A record in SQLite**, so the app remembers after a restart.
- **The person becomes the on/off switch.** When you're at the desk, reminders and the other "talking to you" features run. When you're away, they pause.

Two smaller changes come with it:

- The chat waits **2 minutes** (instead of 45 s) before "Cup left the chat".
- The chat roster keeps its own logic for now.

And one last task: **switch on the hydration rule** (Task 6 of the rule engine plan), which was waiting for this.

## How a picture becomes an event

```
 DetectionEvent           1. Smoothing                       2. State machine          3. Saving
 (3 pictures a second)    ──────────────                     ────────────────          ─────────
 bottle 0.52   ──▶  hysteresis ──▶ vote over last 5  ──▶  step(state, input)  ──▶  ObjectStateChanged on the event bus
                    (yes / no)     pictures (yes / no)      waits before            + SQLite (ObjectStateStore)
                                                            switching               + snapshot every 60 s
                                                                 │
                                                                 ▼
                                    ObjectStatesView: snapshot() and changes(since)
                                    ├─▶ DeskStatus.user_active(): the person's state = the on/off switch
                                    └─▶ RuleRunner: the habit rules (hydration first)
```

Each part is a separate piece of code with its own tests.

---

## 1. Smoothing: from confidence numbers to a steady yes / no

Two filters, one after the other.

**Hysteresis (two lines instead of one).** With a single line at 0.5, the values `0.52, 0.48, 0.51, 0.47, 0.53` would flip yes, no, yes, no, yes. Instead:

- "Seen" turns **on** when confidence reaches **0.5**.
- Once on, it stays on until confidence drops below **0.4**.
- A picture where the object isn't found counts as 0.

So that wobbly row stays "yes" the whole time.

**Majority vote over the last 5 pictures.** One missed picture shouldn't matter. The answer is "yes" if more than half of the last 5 pictures said yes. Until 5 pictures have come in, it's more than half of the ones so far. Both the 5 and the "half" are settings.

The same 5 pictures also give the object's **position**: the median center of the pictures where it was seen. A median, not an average, so that:

- one bad box can't pull the position off;
- when you slide a bottle across the desk, the position jumps once instead of creeping. That gives one `moved`, not several.

Positions are measured as a share of the picture (0 to 1 across, 0 to 1 down). The Pi's 1280×720 pictures and a phone's 640×480 pictures then agree on where the middle is.

## 2. The state machine

Each object has one of three states:

| State | Meaning |
|---|---|
| `UNKNOWN` | Just started, or not sure yet. |
| `PRESENT` | On the desk. |
| `ABSENT` | Not on the desk. |

**The rule for switching:** the smoothed answer has to disagree with the current state for long enough in a row. For objects that's **more than 3 seconds**, with separate settings for arriving and leaving. One picture that agrees again resets the wait.

| From | What has to happen | To | Event |
|---|---|---|---|
| UNKNOWN | seen for more than 3 s | PRESENT | `appeared` |
| UNKNOWN | not seen for more than 3 s | ABSENT | none |
| PRESENT | not seen for more than 3 s | ABSENT | `removed` |
| PRESENT | center moved more than 20% of the picture | PRESENT | `moved` |
| ABSENT | seen for more than 3 s | PRESENT | `appeared` |

All of this lives in one function, `step(state, input, rules) → (new_state, transitions)`. It never reads the clock: every time comes from the picture. That makes it easy to test with made-up times.

### The bottle timeline from the whiteboard, step by step

Before this starts, the bottle has been PRESENT for a while. Pictures are 1/3 of a second apart.

| Picture | Detector says | Hysteresis | Last 5 votes | Answer | Time "gone" | State | Event |
|---|---|---|---|---|---|---|---|
| 1 | bottle 0.6 | yes | 5 of 5 | yes | | PRESENT | |
| 2 | bottle 0.5 | yes | 5 of 5 | yes | | PRESENT | |
| 3 | nothing | no | 4 of 5 | yes | | PRESENT | |
| 4 | bottle 0.55 | yes | 4 of 5 | yes | | PRESENT | |
| 5 | nothing | no | 3 of 5 | yes | | PRESENT | |
| 6 | nothing | no | 2 of 5 | **no** | 0 s | PRESENT | |
| 7–14 | nothing | no | 0–1 of 5 | no | 0.3–2.7 s | PRESENT | |
| 15 | nothing | no | 0 of 5 | no | 3.0 s | PRESENT | |
| 16 | nothing | no | 0 of 5 | no | 3.3 s | **ABSENT** | **`removed`** |

This matches the sketch: the short gap at picture 3 changes nothing, and the bottle is removed at picture 16.

## 3. The person: the on/off switch

The person goes through the same machine, with three differences:

1. **Movement counts as "seen".** The camera looks down at the desk, so often it only sees your hands or arms, and YOLO doesn't say "person". Every `DetectionEvent` already carries `moved`: Lens saw the picture change a lot within the last `camera.moved_within_sec` (1.5 s, already a setting on `refactor`). For the person, "seen" is: a person box **or** `moved`. This is what the app already does today to decide if someone is at the desk. It can be switched off (`person_counts_movement`).
2. **It waits longer before deciding you've left: 90 seconds**, not 3. Looking away or leaning back for a moment shouldn't switch everything off. Arriving still takes only 3 s. Both have their own settings (`person_gone_sec`, `person_appear_sec`). 90 s matches the habit tracker's own "at the desk" window (`presence.user_active_window_sec`). The two stay separate settings, because that one drives desk time and breaks, not the switch.
3. **No `moved` events.** People move all the time.

**What the switch controls:**

| Feature | When you're away | Why |
|---|---|---|
| Habit reminders (nudges), old ones and rule ones | **paused** | Nobody to remind. Already paused today by an older check; that check now reads the switch. |
| Idle chat between agents | **paused** | Same as above. |
| Vision checks (posture, nail biting) | **paused** | Today they run even when nobody's there, which wastes AI calls. |
| Rule success checks ("did the nudge work?") | **keep running** | The rule runner never asks the switch (rule engine plan, Review Focus 2). |
| YOLO detection | **keeps running** | It's how the app sees you come back. |
| Safety alerts (cup near laptop, pets) | **keeps running** | A cat can knock a cup over while you're out. |
| Lost items | **keeps running** | It needs to see where your keys were left. |
| Event log, object states | **keeps running** | |

**How:** `DeskStatus.user_active(now)` (`desktalk/core/habits/desk_status.py`) already exists, and `HabitReminders.tick` and `IdleChat.tick` already call it. Today it asks "was a person or movement seen in the last 10 minutes (`reminders.someone_here_sec`)?". It will instead ask the `ObjectStatesView`: "is the person's state PRESENT?". `VisionHabits.tick` gets the same check (it already holds a `DeskStatus`). When the app has just started and the person is still UNKNOWN (or not seen at all yet), it counts as away until you've been seen for 3 s. That's fine, because reminders already wait 15 minutes after start (`reminders.warmup_min`).

## 4. Saving: SQLite, snapshots and restarts

**Every change goes two places.** First onto the event bus, as a new event class `ObjectStateChanged` (type `object_state`), so other parts can react to it. Then into a SQLite database at `data/object_states.sqlite`. The bus comes first so a database problem can't hide the event from the app.

**One table, `events`**, with the columns from the sketch:

| Column | Example | Notes |
|---|---|---|
| `ts` | 1792345678.1 | when it happened |
| `timezone` | +08:00 | the UTC offset at that moment |
| `device_id` | pi | which camera: pi, phone or webcam |
| `type` | removed | `appeared`, `removed`, `moved` or `snapshot` |
| `object` | bottle | the detector's word for it |
| `prev_state` / `new_state` | PRESENT / ABSENT | |
| `conf_avg` | 0.55 | average confidence of the pictures where it was seen, since the last state change |
| `bbox` | [412, 230, 470, 355] | last box seen, in pixels |
| `dwell_sec` | 94.3 | how long it was in the old state (for `moved`: how long at the old spot) |

**Append only.** The database refuses any change or delete to this table (two small SQLite triggers), so the history can't be rewritten by mistake. WAL mode is on, so reading and writing don't block each other.

**A snapshot every 60 seconds.** It's one `snapshot` row per object that isn't UNKNOWN. It does three jobs:

- **Restart.** On start, the app reads each object's newest row. If that row is less than 5 minutes old, the object starts in that state instead of UNKNOWN, so a bottle that was already there doesn't get a second `appeared`.
- **Faster questions.** "What was on the desk at 3 pm?" means taking the last snapshot before 3 pm plus a few events, not replaying the whole day.
- **Heartbeat.** A gap in the snapshots shows when the app wasn't running.

Snapshots go only to SQLite, not to the event bus, so the event log stays readable.

**`changes(since)` reads SQLite.** The rule engine asks for the last hour or so of changes once a minute. Reading them from the table (with an index on `ts`) means they survive a restart, and the tracker keeps no second copy in memory.

**When the camera started watching.** `DeskSnapshot.watched_since` (the rule engine's `no_event` needs it) is the time of the first picture of the current run. A gap between two pictures of `protocols.breaks.break_min` minutes or more starts a new run, the same rule the producer uses for `DeskState.session_start` (and so for the reminder warm-up). After a restart it starts again with the first picture: the states come back, the watching time doesn't.

**Raw observations** (every box from every picture) are already saved by the event log as JSONL in `data/events/<day>/detections.jsonl`. That already covers "raw observations → JSONL" from the sketch, so nothing new is needed there.

## 5. How long the chat waits before "Cup left the chat"

**From 45 s to 2 minutes** (`agents.leave_after_sec: 120`). Joining stays at 3 s.

Why 2 minutes: the habit rules already say a cup that leaves the desk and comes back **within 2 minutes is one sip** (`hydration.sip_gone_max_sec: 120` in `protocols.yaml`). If the chat waited less than that, every sip of water longer than 45 s would post "Cup left the chat" and then "Cup joined the chat". With 2 minutes:

- a sip, or a quick look at your phone, doesn't touch the chat;
- something taken away for real still leaves well before "where are my keys?" kicks in (10–15 minutes in `lost_items`).

This is only a number in `config/settings.yaml`. The roster code doesn't change.

## 6. How it fits the `refactor` code

I read the parts it plugs into on the `rule-engine` branch (which is `refactor` plus the rule engine):
`ports.py`, `domain/rules.py`, `domain/events.py`, `config.py`, `config/settings.yaml`, `bootstrap.py`,
`core/events/stream.py`, `core/vision/producer.py`, `core/vision/camera_health.py`, `core/agents/roster.py`,
`core/habits/desk_status.py`, `reminders.py`, `rule_runner.py`, `core/chat/workers/idle_chat.py` and
`vision_habits.py`, `runtime/scheduler.py`, `adapters/storage/`, `desk_state.py`, the fakes, the contracts, and the
integration kit (`tests/integration/app_kit.py`). Here's what that changed from the first draft.

**Fits as it is:**
- **The event bus.** The tracker is one more consumer. Like `Roster`, it subscribes itself in its constructor:
  `bus.subscribe('object-states', self.on_detection, types={DetectionEvent})`. `EventStream` gives it its own
  queue and thread, so a slow database write never holds up the camera.
- **The event log** subscribes to every event (`bus.subscribe('event-log', event_log.write)`), so
  `object_state` events land in `data/events/<day>/events.jsonl` with no extra code, once the class is in
  `EVENT_TYPES`.
- **`DetectionEvent`** carries everything needed: `ts`, `source` (used as `device_id`), `size`, `detections`
  (each a `Detection` with `label`, `confidence` and a `BBox` that has `center()`), and `moved`.
- **"Movement" is already a setting.** The first draft moved the 1.5 s out of `producer.py`. On `refactor` it's
  already `camera.moved_within_sec`, used by `CameraHealth.moved_recently()`, so that step is gone.
- **Timed jobs.** The snapshot is one more `scheduler.every(...)` in `build()`, like compacting the event log.
  The `Scheduler` is a `PeriodicWorker`, so the Supervisor runs it and a failing snapshot is logged and retried.
- **Shutdown.** `DeskTalk.stop()` already has `_shutdown_step(...)`. It gets "save the object states" (after the
  events still waiting are handled) and "close the object states database" (last).
- **Tests.** `DeskRig` builds the app with `data_folder=tmp_path / 'run'`, so the database lands there. Its
  `watch()` puts a person in every picture by default, so the existing reminder tests still see "someone at the desk"
  with the new switch.
- **The interface the rule engine reads** (`ObjectStatesView`: `snapshot()` and `changes(since)`, with
  `DeskSnapshot`, `ObjectSnapshot` and `StateChange` from `desktalk/domain/rules.py`) and its contract suite
  (`tests/contracts/object_states_contract.py`) are already in. The tracker implements it and runs the suite.

**Different on `refactor`:**
- The old `app.user_active()` is now **`DeskStatus.user_active()`**, and its 10-minute window is
  `reminders.someone_here_sec`. The person switch replaces it, and `someone_here_sec` goes away.
  `HabitTracker`'s own "at the desk" (`presence.user_active_window_sec`, desk time and breaks) stays as it is.
- Nothing may call `time.time()`. `step()` takes time from the picture; `save_snapshot(now)` and `restore(now)` take
  it as an argument, and `build()` passes `clock.now()`.
- Parts get only their own typed settings section (`config.object_states`, an `ObjectStatesSettings`), never a dict
  and never the whole config.
- The tracker talks to SQLite through an interface (`ObjectStateStore` in `ports.py`), so its tests use an in-memory
  fake, and the SQLite class and the fake pass one contract suite.
- Name clash to watch: `DetectionEvent.moved` means "the picture changed a lot", while our `moved` event means "the
  object changed place". The new event calls it `change='moved'`, so the two never share a field. (The comment on
  `DetectionEvent.moved` says "Lens saw the camera move", but `CameraHealth` sets it for any big change in the
  picture. Task 1 fixes the comment.)
- `DeskState.person_last_seen` and `last_motion_ts` have no reader left after Task 5. They stay for now (see "Not in
  this plan").

---

## Every number, in one place

Every number this plan uses is a setting in `config/settings.yaml`. To change one:

1. Edit the value (or put just that line in your own `config/settings.local.yaml`, same layout).
2. Restart `uv run python server.py`.

No code change is needed. The code never carries its own copy of a number. `ObjectStatesSettings` has no defaults,
so if a line goes missing from `settings.yaml`, the app stops at start with a `ConfigError` that names the file and
the setting, instead of quietly using a hidden value. Each class takes its section (or the numbers from it) in its
constructor; only `bootstrap.py` hands them out.

| Setting | Default | What it does | Higher means | Lower means |
|---|---|---|---|---|
| `object_states.on_confidence` | 0.5 | how sure a box must be to count as "seen" | fewer false sightings, may miss faint objects | more sightings, more mistakes |
| `object_states.off_confidence` | 0.4 | once seen, it stays seen down to this (must not be more than `on_confidence`) | flickers off more easily | holds on through weak pictures |
| `object_states.window_pictures` | 5 | how many recent pictures vote | steadier, slower to react | quicker, jumpier |
| `object_states.vote_share` | 0.5 | share of votes that must say yes (more than this) | harder to count as seen | easier to count as seen |
| `object_states.appear_sec` | 3 | how long an object must be seen before `appeared` | fewer "passing through" events | faster `appeared` |
| `object_states.gone_sec` | 3 | how long it must be missing before `removed` | fewer `removed` from brief pickups | faster `removed` |
| `object_states.move_fraction` | 0.2 | how far (share of the picture) counts as `moved` | only big moves count | small nudges count |
| `object_states.person_appear_sec` | 3 | how long you must be seen before the switch turns on | reminders resume later | reminders resume sooner |
| `object_states.person_gone_sec` | 90 | how long you must be missing before the switch turns off | short trips don't pause anything | pauses sooner when you leave |
| `object_states.snapshot_every_sec` | 60 | how often every state is saved | smaller database, coarser history | finer heartbeat and restart point |
| `object_states.restore_max_age_sec` | 300 | after a restart, how old a saved state may be and still be used | trusts older states | starts fresh more often |
| `camera.moved_within_sec` | 1.5 | movement this recent marks a picture as "moved" (already a setting) | hands count as "there" for longer | only very recent movement counts |
| `agents.leave_after_sec` | 120 (was 45) | how long before "Cup left the chat" | fewer leave / rejoin posts | chat follows the desk more closely |
| `protocols.breaks.break_min` | 5 (unchanged) | a gap in the pictures this long starts a new "watching" run (`watched_since`), as it already does for the warm-up | | |
| `reminders.someone_here_sec` | **removed** | replaced by the person switch | | |

The section in `config/settings.yaml`, after `presence:`:

```yaml
# ── Object states (steady present / absent per object, saved in SQLite) ──
object_states:
  on_confidence: 0.5          # a box at least this sure turns "seen" on
  off_confidence: 0.4         # once on, "seen" stays on until the box drops below this
  window_pictures: 5          # the last this many pictures vote on "seen"
  vote_share: 0.5             # more than this share of the votes must say yes
  appear_sec: 3               # seen longer than this → appeared
  gone_sec: 3                 # missing longer than this → removed
  move_fraction: 0.2          # center moved more than this share of the picture → moved
  no_move_labels: [person]    # things that move all the time: no "moved" events for them
  # You (the person) are the on/off switch for reminders, idle chat and vision checks.
  person_label: person        # the detector's word for you
  person_appear_sec: 3        # seen longer than this → you're at the desk, reminders on
  person_gone_sec: 90         # missing longer than this → you're away, reminders pause
  person_counts_movement: true  # movement in the picture counts as you (the camera often only sees hands)
  # Saving
  snapshot_every_sec: 60      # save every object's state this often (restart + heartbeat)
  restore_max_age_sec: 300    # after a restart, saved states older than this start over as UNKNOWN
  database: data/object_states.sqlite
```

Changed in `agents:`:

```yaml
  leave_after_sec: 120        # gone this long → "Cup left the chat" (same as the longest sip, so a sip never leaves)
```

## Global Constraints

- Everything in `AGENTS.md` holds: test first, interfaces first, plain words, `logging` never `print`.
- **No numbers in code, and no fallback defaults.** Every number comes from a typed settings section by name
  (`settings.appear_sec`, never `.get('appear_sec', 3)`). Classes take their numbers in the constructor and never
  load settings themselves. Only `bootstrap.py` reads `config` and hands the sections out.
- **Changing a number in `settings.yaml` must never break a test.** Unit tests build their own settings section
  (`ObjectStatesSettings.model_validate({...})` with the values in the table above, plus changes). Integration tests
  that wait for something read how long from `rig.config` (as Task 6 does).
- No `time.time()` and no `datetime.now()`: time comes from the picture, or from `Clock.now()` passed in by
  `bootstrap.py`.
- State names are the strings `UNKNOWN`, `PRESENT`, `ABSENT` (the same strings `ObjectSnapshot.state` uses). Row types
  are `appeared`, `removed`, `moved`, `snapshot`.
- Tests never need the network, a camera, a Pi, Docker or an API key. SQLite tests use `tmp_path`.
- Every new real version of an interface runs its contract suite: the tracker runs `ObjectStatesContract`, the SQLite
  store and its fake run the new `StateStoreContract`.
- `tests/characterization/` keeps passing: no route or reply changes.
- While working: `uv run pytest <the task's test files>`. At the very end: `uv run pytest -m "not slow"` and
  `uv run ruff check <every changed file>`, with the output shown.
- Nothing is built or committed until Sam OKs this plan. After that, one commit per task, one short plain sentence.
- No two subagents edit the same file (see "Order of work").

## Review Focus

1. **The app restarts while a bottle is on the desk.** Expected: no second `appeared`; the bottle is still PRESENT,
   with its old `since`. Tests: Task 3 `test_restart_restores_states_without_new_events`, Task 5
   `test_a_restart_keeps_the_states`.
2. **The camera is off for 5 minutes.** Expected: nothing changes while it's off; a missing bottle is `removed`
   about 3 s *after pictures come back*; `watched_since` starts again. Test: Task 3
   `test_camera_gap_does_not_remove_by_itself` and `test_watching_starts_again_after_a_long_gap`.
3. **Only your hands are in view.** Expected: movement keeps you PRESENT, so reminders keep running. Test: Task 3
   `test_hands_only_keep_the_person_present`.
4. **The database can't be written** (disk full, file locked). Expected: the `ObjectStateChanged` event still goes
   out, the error is logged, the states keep updating, and the next picture is handled normally. Test: Task 3
   `test_a_failing_store_does_not_hide_the_event`.
5. **Two bottles, or the camera's picture size changes (Pi ↔ phone).** Expected: no stray `moved`. Tests: Task 3
   `test_two_bottles_do_not_fire_moved` and `test_same_spot_in_a_bigger_picture_is_not_moved`.

Also covered: wobbling confidence (one `appeared`), stepping away for a minute (still PRESENT), leaving for 2 minutes
(reminders pause, safety alerts don't). Each has a test below.

## Files

| File | What it holds | Task |
|---|---|---|
| `desktalk/domain/object_states.py` (new) | `Phase`, `StateRecord` | 1 |
| `desktalk/domain/events.py` | `ObjectStateChanged` (`object_state`); fix the comment on `DetectionEvent.moved` | 1 |
| `desktalk/domain/__init__.py` | export the new names | 1 |
| `desktalk/ports.py` | `ObjectStateStore`; `DeskStatus.user_active` docstring | 1 |
| `desktalk/config.py` | `ObjectStatesSettings`, `AppConfig.object_states` | 1 |
| `config/settings.yaml` | the `object_states:` section | 1 |
| `tests/fakes/parts.py`, `tests/fakes/__init__.py` | `InMemoryStateStore` | 1 |
| `tests/contracts/state_store_contract.py` (new) | `StateStoreContract` | 1 |
| `tests/contracts/test_fakes_pass_contracts.py` | the fake runs it | 1 |
| `desktalk/core/object_states/__init__.py` (new) | exports | 2 |
| `desktalk/core/object_states/smoothing.py` (new) | `Hysteresis`, `MajorityWindow` | 2 |
| `desktalk/core/object_states/machine.py` (new) | `Rules`, `StepInput`, `ObjectState`, `Transition`, `step()` | 2 |
| `desktalk/core/object_states/tracker.py` (new) | `ObjectStateTracker` | 3 |
| `desktalk/adapters/storage/object_states_db.py` (new) | `SqliteStateStore` | 4 |
| `desktalk/adapters/storage/__init__.py` | export it | 4 |
| `desktalk/bootstrap.py` | build, restore, snapshot job, stop, the switch | 5 (and 7) |
| `desktalk/core/habits/desk_status.py` | `user_active()` reads the person's state | 5 |
| `desktalk/core/chat/workers/vision_habits.py` | skip the check when you're away | 5 |
| `desktalk/config.py`, `config/settings.yaml` | drop `reminders.someone_here_sec` | 5 |
| `docs/ARCHITECTURE.md` | rows in "The parts", "Interfaces" and "Events" | 5 (and 7) |
| `config/settings.yaml` | `agents.leave_after_sec: 120` | 6 |
| `desktalk/core/habits/reminders.py`, `config/protocols.yaml` | delete `NoRules`; hydration rule on, old hydration off | 7 |

---

### Task 1: Settings, data, interfaces and the store contract

**Files:**
- Create: `desktalk/domain/object_states.py`, `tests/contracts/state_store_contract.py`,
  `tests/unit/test_config_object_states.py`
- Modify: `desktalk/domain/events.py`, `desktalk/domain/__init__.py`, `desktalk/ports.py`, `desktalk/config.py`,
  `config/settings.yaml`, `tests/fakes/parts.py`, `tests/fakes/__init__.py`,
  `tests/contracts/test_fakes_pass_contracts.py`, `tests/unit/test_domain.py`

**Interfaces:**
- Produces:
  - `Phase(str, Enum)` with `UNKNOWN`, `PRESENT`, `ABSENT` (values equal the names), in `desktalk/domain/object_states.py`.
  - `StateRecord`, frozen dataclass: `ts: float`, `timezone: str`, `device_id: str`, `type: str`
    (`appeared | removed | moved | snapshot`), `label: str` (the `object` column), `prev_state: Phase`,
    `new_state: Phase`, `conf_avg: float | None`, `bbox: tuple[float, float, float, float] | None`,
    `dwell_sec: float | None`. A module constant `CHANGE_TYPES = ('appeared', 'removed', 'moved')`.
  - `ObjectStateChanged(Event)`: `TYPE = 'object_state'`, `KEYS = {'label': 'object'}`, fields `change: str`,
    `label: str`, `prev_state: str`, `new_state: str`, `device_id: str`, `conf_avg: float | None = None`,
    `bbox: tuple[float, ...] | None = None`, `dwell_sec: float | None = None`. Added to `EVENT_TYPES`.
  - `ObjectStateStore` (Protocol, `runtime_checkable`, in the "Events and storage" part of `ports.py`):
    - `append(self, records: Sequence[StateRecord]) -> None`
    - `latest_states(self, since: float) -> dict[str, StateRecord]`: per object, the newest `appeared`, `removed` or
      `snapshot` row with `ts >= since` (`moved` rows are skipped: they don't change the state, and their
      `dwell_sec` isn't the time in the state).
    - `changes(self, since: float) -> list[StateRecord]`: `appeared`, `removed` and `moved` rows with `ts > since`,
      oldest first (rows with the same `ts` in the order they were added).
    - `close(self) -> None`
  - `DeskStatus.user_active` docstring: "You're at the desk: the person's object state is PRESENT."
  - `ObjectStatesSettings(_Section)` with the fields in "Every number" (`no_move_labels: Words`, `person_label: str`,
    `person_counts_movement: bool`, `database: Path`; numbers typed `Fraction`, `PositiveInt`, `ZeroOrMore` or
    `PositiveNumber`), and `_in_order = (('off_confidence', 'on_confidence'),)`. `AppConfig.object_states`.
  - `InMemoryStateStore()` in `tests/fakes/parts.py`, exported from `tests/fakes`. It keeps a list and records
    `closed`.

- [ ] **Step 1: Write the failing tests.**
  - `StateStoreContract` (fixture `store`, an empty `ObjectStateStore`):
    `test_records_come_back_as_written` (every field, `bbox` and `None`s included),
    `test_changes_leave_out_snapshots_and_are_oldest_first`,
    `test_changes_since_is_strictly_after` (a row at exactly `since` is left out),
    `test_latest_states_skip_moved_rows` (appeared at 100, moved at 110 → the appeared row),
    `test_latest_states_leave_out_old_rows` (a row at 99 with `since=100` → `{}`).
  - `TestInMemoryStateStore(StateStoreContract)` in `test_fakes_pass_contracts.py`, and
    `('InMemoryStateStore', 'ObjectStateStore')` in the `isinstance` list.
  - `test_config_object_states.py` (loads a temp copy of `config/`, like `test_config_rules.py`):
    `test_object_states_load_as_written` (each value in the table above),
    `test_off_line_above_on_line_is_an_error` (`off_confidence: 0.6` → `ConfigError` naming both settings),
    `test_a_missing_setting_stops_the_start` (no `person_gone_sec` → `ConfigError` naming it).
  - `test_domain.py`: an `ObjectStateChanged` round-trips through `to_dict()` / `event_from_dict()`, and the dict
    says `"object": "bottle"`.
- [ ] **Step 2: Run** `uv run pytest tests/contracts/test_fakes_pass_contracts.py tests/unit/test_config_object_states.py tests/unit/test_domain.py -v`
  → fails (import errors).
- [ ] **Step 3: Add** the types, the interface, the settings section (YAML above) and the fake.
- [ ] **Step 4: Run** the same tests → pass. Also `uv run pytest tests/characterization -q` → still passes.
- [ ] **Step 5: Commit** "Add the object state settings, records and store interface".

### Task 2: Smoothing and the state machine

Pure code: no settings section, no clock, no I/O. Can run beside Task 1.

**Files:**
- Create: `desktalk/core/object_states/__init__.py`, `smoothing.py`, `machine.py`
- Test: `tests/unit/test_core_object_states_smoothing.py`, `tests/unit/test_core_object_states_machine.py`

**Interfaces:**
- Consumes: `Phase` from Task 1 (`from desktalk.domain.object_states import Phase`; see "Order of work").
- Produces:
  - `Hysteresis(on: float, off: float)` with `update(conf: float | None) -> bool`. Starts off. `None` counts as 0.
  - `MajorityWindow(size: int, vote_share: float)` with
    `add(seen: bool, center: tuple[float, float] | None, conf: float | None) -> tuple[bool, tuple[float, float] | None, float | None]`
    → `(majority_seen, median_center, average_conf)`. A `deque(maxlen=size)`. Yes votes must be more than
    `vote_share` of the pictures in the window. The center is the median of x and y separately
    (`statistics.median`). Center and conf only use the pictures where `seen` was true; `None` if there were none.
  - In `machine.py`, all frozen dataclasses:
    - `Rules(appear_sec: float, gone_sec: float, move_fraction: float, track_moves: bool)`
    - `StepInput(ts: float, seen: bool, center: tuple[float, float] | None, conf: float | None, bbox: tuple[float, float, float, float] | None)`.
      `seen` and `center` are smoothed; `conf` and `bbox` are this picture's.
    - `ObjectState(label: str, phase: Phase = Phase.UNKNOWN, since: float | None = None, pending_since: float | None = None, anchor: tuple[float, float] | None = None, anchor_since: float | None = None, conf_sum: float = 0.0, conf_n: int = 0, last_bbox: tuple[float, float, float, float] | None = None)`
    - `Transition(type: str, label: str, prev_state: Phase, new_state: Phase, ts: float, conf_avg: float | None, bbox: tuple[float, float, float, float] | None, dwell_sec: float | None)`
    - `step(state: ObjectState, inp: StepInput, rules: Rules) -> tuple[ObjectState, list[Transition]]`

**What `step()` does, in order:**
1. If the picture was seen, add its conf to `conf_sum` / `conf_n` (when conf isn't `None`) and keep its box in `last_bbox`.
2. If the answer agrees with the phase, clear `pending_since`. If it disagrees, set `pending_since` to this time (only if it isn't set yet).
3. If `inp.ts - pending_since` is more than the wait (`appear_sec` when heading to PRESENT, `gone_sec` when heading to ABSENT), switch phase using the table in section 2. Then set `since`, clear `pending_since` and reset `conf_sum` / `conf_n` (after the transition has taken its average). When switching to PRESENT, set the anchor to this position.
4. If it was PRESENT and still is, and `track_moves` is on: when the anchor is empty (just after a restart), set it and do nothing else. When the position is more than `move_fraction` away from the anchor (straight-line distance), fire `moved` and move the anchor.

- [ ] **Step 1: Write the failing tests.**
  Smoothing:
  - `test_hysteresis_holds_through_wobble_around_the_line`: `0.52, 0.48, 0.51, 0.47, 0.53` → all `True`.
  - `test_hysteresis_needs_the_high_line_to_turn_on`: `0.45, 0.48, 0.5, 0.39, None` → `False, False, True, False, False`.
  - `test_majority_window_ignores_one_missing_picture`: seen `T, T, F, T, F, F` with size 5, share 0.5 → `T, T, T, T, T, F`.
  - `test_vote_share_is_used`: size 5, share 0.7, seen `T, T, T, F, F` → `T, T, T, T, F` (3 of 4 is 0.75, more than 0.7; 3 of 5 is 0.6, not).
  - `test_majority_window_center_uses_seen_pictures_only`: size 3, share 0.5, add `(0.2, 0.2)`, then not seen, then `(0.4, 0.4)` → center `(0.3, 0.3)`, conf the average of the two.

  Machine (`RULES = Rules(appear_sec=3, gone_sec=3, move_fraction=0.2, track_moves=True)`):
  - `test_unknown_becomes_present_after_more_than_the_wait`: seen at 0, 1, 2, 3 s → still UNKNOWN (exactly 3 s isn't "more than"). At 3.1 s → PRESENT plus one `appeared`.
  - `test_unknown_becomes_absent_quietly`: not seen at 0, 2, 3.5 s → ABSENT, no transitions.
  - `test_present_to_absent_fires_removed_with_dwell`: PRESENT from 3.5 s, not seen from 10 s, then at 13.5 s → `removed` with `dwell_sec == 10.0`.
  - `test_one_seen_picture_resets_the_wait`: PRESENT, then not seen at 5 s, seen at 7 s, not seen at 8 s and 11 s → still PRESENT.
  - `test_moved_over_20_percent_only`: anchor at (0.5, 0.5). (0.65, 0.5) → nothing. (0.75, 0.5) → one `moved`, and the anchor is now (0.75, 0.5).
  - `test_absent_comes_back_with_appeared`: ABSENT, then seen for more than 3 s → `appeared` with `prev_state` ABSENT.
  - `test_person_rules_wait_longer_to_leave_and_never_move`: `Rules(appear_sec=3, gone_sec=90, move_fraction=0.2, track_moves=False)`. PRESENT, jump from x 0.1 to 0.9 → nothing. Not seen for 60 s → still PRESENT. Not seen for 91 s → `removed`.
  - `test_restored_state_sets_its_anchor_quietly`: `ObjectState('bottle', Phase.PRESENT, since=0)` with no anchor, seen at (0.9, 0.9) → no transitions, anchor set.
- [ ] **Step 2: Run** `uv run pytest tests/unit/test_core_object_states_smoothing.py tests/unit/test_core_object_states_machine.py -v` → fails (no module).
- [ ] **Step 3: Write** `smoothing.py` and `machine.py`.
- [ ] **Step 4: Run** → passes. `uv run ruff check desktalk/core/object_states tests/unit/test_core_object_states_*.py`.
- [ ] **Step 5: Commit** "Add the smoothing and the state machine for objects on the desk".

### Task 3: The tracker

**Files:**
- Create: `desktalk/core/object_states/tracker.py`
- Test: `tests/unit/test_core_object_states_tracker.py`

**Interfaces:**
- Consumes: Task 1's `ObjectStatesSettings`, `ObjectStateStore`, `StateRecord`, `Phase`, `ObjectStateChanged`,
  `InMemoryStateStore`; Task 2's smoothing, `Rules`, `StepInput`, `ObjectState`, `step()`; `EventBus`,
  `ObjectStatesView`, `DeskSnapshot`, `ObjectSnapshot`, `StateChange`, `DetectionEvent` (already on the branch);
  `ObjectStatesContract`.
- Produces: `ObjectStateTracker(settings: ObjectStatesSettings, bus: EventBus, store: ObjectStateStore, *, device_id: str, new_session_after_sec: float)`.
  It implements `ObjectStatesView`. It reads every number from `settings` in `__init__` and subscribes itself:
  `bus.subscribe('object-states', self.on_detection, types={DetectionEvent})`.
  - `on_detection(event: DetectionEvent) -> None`, for each picture:
    - `watched_since`: set on the first picture; set again when the gap since the last picture is
      `new_session_after_sec` or more.
    - Box centers become shares of `event.size`.
    - For each label, pick one box: the one nearest the anchor if there is one, otherwise the most confident.
    - For every label it knows about (this picture's labels plus the ones already tracked or restored), run
      hysteresis → majority window → `step()`. Each label has its own `Hysteresis` and `MajorityWindow`.
    - Objects use `Rules(settings.appear_sec, settings.gone_sec, settings.move_fraction, track_moves=label not in settings.no_move_labels)`.
    - `settings.person_label` uses `Rules(settings.person_appear_sec, settings.person_gone_sec, settings.move_fraction, track_moves=...)`.
      When `settings.person_counts_movement` is on, its hysteresis answer is OR-ed with `event.moved` before it goes
      into the majority window, and a picture with `moved` starts tracking the person even with no person box.
    - For each transition: publish `ObjectStateChanged(ts=..., change=..., label=..., prev_state=..., new_state=..., device_id=..., conf_avg=..., bbox=..., dwell_sec=...)`.
      After all of this picture's events are out, `store.append(records)` once, with the timezone
      `datetime.fromtimestamp(ts).astimezone().isoformat()[-6:]`. A store that raises is logged (with the
      traceback only the first time in a row, like `EventStream._deliver`) and never stops the tracker.
  - `snapshot() -> DeskSnapshot`: every tracked label (UNKNOWN included) as `ObjectSnapshot(label, phase.value, since)`,
    and `watched_since`. Labels never seen aren't in it.
  - `changes(since: float) -> list[StateChange]`: `store.changes(since)` as `StateChange(ts, label, change=type)`.
  - `save_snapshot(now: float) -> int`: one `snapshot` record per object that isn't UNKNOWN, with
    `prev_state = new_state` and `dwell_sec = now - since`. Returns how many.
  - `restore(now: float) -> int`: `store.latest_states(now - settings.restore_max_age_sec)`. Each PRESENT / ABSENT
    record becomes `ObjectState(label, phase, since=...)` with no anchor: `since = ts - dwell_sec` for a snapshot
    row, `ts` for appeared / removed. The smoothing windows start empty. Returns how many.
  - One lock covers the states, the smoothing and `watched_since`: `on_detection` runs on the event bus thread,
    `snapshot()` / `changes()` on the rule engine's and reminders' threads, `save_snapshot()` on the scheduler's.

Helpers in the test: `picture(ts, *dets, size=(640, 480), moved=False)` builds a `DetectionEvent`;
`o(label, conf, x=320, y=240)` a `Detection` with a 40×40 box around (x, y); `make_tracker(store=None, **changes)`
returns `(tracker, bus, store)` with a `SyncEventBus`, an `InMemoryStateStore` and `ObjectStatesSettings.model_validate`
of the table's values plus `changes`, `device_id='pi'`, `new_session_after_sec=300`; `feed(tracker, t0, rows, step=1/3)`
returns the next time; `changes(bus)` lists `(change, label)` from the published `ObjectStateChanged`s.

- [ ] **Step 1: Write the failing tests.**

```python
def test_bottle_timeline_from_the_whiteboard():
    tracker, bus, store = make_tracker()
    t = feed(tracker, t0=1000, rows=[[o('bottle', 0.6)]] * 15)     # settle in: PRESENT
    assert changes(bus) == [('appeared', 'bottle')]
    table = [[o('bottle', 0.6)], [o('bottle', 0.5)], [], [o('bottle', 0.55)]] + [[]] * 12
    states = feed_rows(tracker, t, table)          # the bottle's state after each row
    assert all(s == 'PRESENT' for s in states[:14])                 # pictures 1–14
    assert states[15] == 'ABSENT'                                   # picture 16
    assert changes(bus) == [('appeared', 'bottle'), ('removed', 'bottle')]
```

(Picture 15 is exactly on the 3-second line, so the test doesn't check it.)

  - `test_wobbling_confidence_gives_one_appeared`: 30 pictures cycling `0.52, 0.48, 0.51, 0.47, 0.53` → only `appeared`.
  - `test_events_go_to_the_bus_and_the_store`: after `appeared`, the bus has one `ObjectStateChanged` with
    `device_id == 'pi'`, and `store.changes(0)` has one row with `new_state == Phase.PRESENT` and a `timezone` like `+08:00`.
  - `test_a_failing_store_does_not_hide_the_event`: a store whose `append` raises `sqlite3.OperationalError` → the
    event is still on the bus, the error is in `caplog`, and the next pictures still change states.
  - `test_snapshot_writes_one_row_per_known_object`: bottle PRESENT, cup ABSENT, book UNKNOWN → `save_snapshot(now) == 2`.
  - `test_snapshot_lists_unknown_while_deciding`: one bottle picture → `snapshot().objects['bottle'].state == 'UNKNOWN'`; no person in it.
  - `test_restart_restores_states_without_new_events`: bottle PRESENT, `save_snapshot`. A new tracker on the same
    store gets `restore(now) == 1`, `since` equals the first tracker's, and 15 more bottle pictures → no new events.
  - `test_restore_ignores_old_rows`: snapshot at 1000, `restore(now=1301)` → `0`.
  - `test_changes_survive_a_restart`: `appeared` in the first tracker → a second tracker on the same store lists it in `changes(0)`.
  - `test_camera_gap_does_not_remove_by_itself`: bottle PRESENT, then the next (empty) picture comes at t=1300 →
    still PRESENT. More empty pictures → `removed` within 4.5 s of 1300.
  - `test_watching_starts_again_after_a_long_gap`: first picture at 1000 → `watched_since == 1000`; a picture at
    1200 → still 1000; a picture at 1600 (gap 400 ≥ 300) → 1600.
  - `test_same_spot_in_a_bigger_picture_is_not_moved`: the middle of 640×480, then the middle of 1280×720 → no `moved`.
  - `test_two_bottles_do_not_fire_moved`: bottle A on the left is PRESENT, then bottle B on the right shows up at higher confidence in every picture → no `moved` over 30 pictures.
  - `test_bottle_moved_across_the_desk`: PRESENT on the left, then 10 pictures on the right → exactly one `moved`.
  - `test_hands_only_keep_the_person_present`: person PRESENT, then 2 minutes of pictures with no person box but `moved=True` → still PRESENT.
  - `test_person_leaves_after_person_gone_sec`: person PRESENT, then pictures with no person and no movement → still PRESENT at `person_gone_sec - 30`, ABSENT with `removed` by `person_gone_sec + 2`.
  - `test_every_number_comes_from_the_settings`: `appear_sec=1`, `gone_sec=10`, `window_pictures=3`. A bottle appears after about 1 s (not 3), and is still PRESENT 8 s after it's gone.
  - `test_movement_can_be_switched_off`: `person_counts_movement=False`, person PRESENT, then hands only (`moved=True`, no person box) → person goes ABSENT after `person_gone_sec`.
  - The contract:

```python
class TestObjectStateTracker(ObjectStatesContract):
    @pytest.fixture
    def store(self):
        return InMemoryStateStore()

    @pytest.fixture
    def states(self, store):
        return make_tracker(store)[0]

    @pytest.fixture
    def add_change(self, store):
        return lambda states, ts, label, kind: store.append([record(ts, label, kind)])

    @pytest.fixture
    def put_object(self, store):
        def put(states, label, state, since):
            if state == 'UNKNOWN':
                states.on_detection(picture(since, o(label, 0.9)))        # seen once: not decided yet
            else:
                store.append([record(since, label, 'snapshot', state)])
                states.restore(now=since + 1)
        return put
```

- [ ] **Step 2: Run** `uv run pytest tests/unit/test_core_object_states_tracker.py -v` → fails.
- [ ] **Step 3: Write** `tracker.py`.
- [ ] **Step 4: Run** → passes. Ruff on both files.
- [ ] **Step 5: Commit** "Add the tracker that turns detections into object states".

### Task 4: The SQLite store

**Files:**
- Create: `desktalk/adapters/storage/object_states_db.py`
- Modify: `desktalk/adapters/storage/__init__.py` (export `SqliteStateStore`; one line in its docstring)
- Test: `tests/unit/test_storage_object_states_db.py`

**Interfaces:**
- Consumes: `ObjectStateStore`, `StateRecord`, `Phase`, `StateStoreContract` (Task 1).
- Produces: `SqliteStateStore(path: Path)`, an `ObjectStateStore`.
  - On open: make the folder, WAL mode, create the `events` table (columns in section 4; `bbox` as JSON text; a
    `CHECK` that `type` is one of the four; an `INTEGER PRIMARY KEY` for "the order they were added"), indexes on
    `(object, ts)` and `ts`, and two triggers that stop `UPDATE` and `DELETE` with
    `RAISE(ABORT, 'events is append-only')`.
  - One connection (`check_same_thread=False`) behind a lock. `append` writes all rows in one transaction.

- [ ] **Step 1: Write the failing tests.** `TestSqliteStateStore(StateStoreContract)` with a `store` fixture on
  `tmp_path / 'states.sqlite'`, plus:
  - `test_the_table_is_append_only`: a raw `UPDATE` and a raw `DELETE` both raise `sqlite3.DatabaseError`.
  - `test_type_must_be_one_of_the_four`: a raw insert with `type='teleported'` raises `sqlite3.IntegrityError`.
  - `test_rows_survive_reopening`: append, `close()`, open a new store on the same file → same `changes(0)`.
  - `test_wal_mode_is_on`: `PRAGMA journal_mode` is `wal`.
  - `test_the_folder_is_made`: a path in a folder that doesn't exist yet works.
- [ ] **Step 2: Run** `uv run pytest tests/unit/test_storage_object_states_db.py -v` → fails.
- [ ] **Step 3: Write** `object_states_db.py`.
- [ ] **Step 4: Run** → passes. Ruff on both files.
- [ ] **Step 5: Commit** "Save object states in an append-only SQLite table".

### Task 5: Hook it into the app, and the person switch

**Files:**
- Modify: `desktalk/bootstrap.py`, `desktalk/core/habits/desk_status.py`,
  `desktalk/core/chat/workers/vision_habits.py`, `desktalk/config.py` and `config/settings.yaml` (remove
  `reminders.someone_here_sec`), `docs/ARCHITECTURE.md`
- Modify tests: `tests/unit/test_core_habits_desk_status.py`, `tests/unit/test_core_habits_reminders.py` (the `Desk`
  rig), `tests/unit/test_core_workers_vision_habits.py`
- Create: `tests/integration/test_app_object_states.py`

**Interfaces:**
- Consumes: `ObjectStateTracker` (Task 3), `SqliteStateStore` (Task 4), `ObjectStatesView`, `FakeObjectStates`.
- Produces:
  - `DeskStatus(desk: DeskState, camera: FrameSource, prefs: Preferences, reminders: RemindersSettings, states: ObjectStatesView, *, person_label: str)`.
    `user_active(now)` is true when `states.snapshot().objects.get(person_label)` exists and its state is
    `'PRESENT'`. `now` stays, so callers don't change. Update the module docstring.
  - `VisionHabits.tick(now)`: right after the `self._vision is None` check, return `self._every` when
    `not self._status.user_active(now)`.
  - `DeskTalk` gets two fields: `object_states: ObjectStateTracker` and `state_store: SqliteStateStore` (one line each
    in the field list of its docstring).
  - In `build()`, right after the producer (before the roster, so `DeskStatus` can get it):
    ```python
    state_store = SqliteStateStore(data_path(config.object_states.database))
    object_states = ObjectStateTracker(config.object_states, bus, state_store, device_id=source.kind,
                                       new_session_after_sec=config.protocols.breaks.break_min * MINUTE)
    object_states.restore(clock.now())
    ```
    `DeskStatus(desk, source, preferences, config.reminders, object_states, person_label=config.object_states.person_label)`.
    `scheduler.every(config.object_states.snapshot_every_sec, lambda: object_states.save_snapshot(clock.now()), 'save object states')`.
    Add the tracker to the diagram in the module docstring.
  - `DeskTalk.stop()`: after `self.bus.wait_idle(wait)`, `self._shutdown_step('save the object states', lambda: self.object_states.save_snapshot(self.clock.now()))`.
    After `self.bus.close(wait)`, `self._shutdown_step('close the object states database', self.state_store.close)`.
    Update the docstring's list of steps.
  - `docs/ARCHITECTURE.md`: "The parts" gets `desktalk/core/object_states/` and `SqliteStateStore` in the
    `adapters/storage/` row; "Interfaces" gets `ObjectStatesView` (`ObjectStateTracker` / `FakeObjectStates`) and
    `ObjectStateStore` (`SqliteStateStore` / `InMemoryStateStore`); "Events" gets `object_state`; the `DeskStatus`
    row says "someone at the desk? (the person's object state)".

- [ ] **Step 1: Write the failing tests.**
  - `test_core_habits_desk_status.py`: replace the two `someone_here_sec` tests with
    `test_user_active_follows_the_persons_state` (with a `FakeObjectStates`: no person → False, UNKNOWN → False,
    PRESENT → True, ABSENT → False) and `test_the_persons_word_comes_from_the_settings` (`person_label='human'`:
    a PRESENT `person` → False, a PRESENT `human` → True). The `Rig` builds `DeskStatus` with the new arguments.
  - `test_core_habits_reminders.py`: the `Desk` rig passes `FakeObjectStates([ObjectSnapshot('person', 'PRESENT', start)])`
    and drops the `desk.person_last_seen = now` line. The existing tests should pass unchanged.
  - `test_core_workers_vision_habits.py`: `test_no_question_while_you_are_away`: `FakeDeskStatus(active=False)` →
    no picture goes to the vision AI.
  - `tests/integration/test_app_object_states.py`, with `DeskRig`:
    - `test_object_states_follow_the_pictures`: `rig.watch(5, CUP_FAR)` then `rig.watch(5)` → the event log has an
      `object_state` `appeared` and then `removed` for `cup`; `rig.app.object_states.snapshot().objects['cup'].state == 'ABSENT'`;
      the newest cup row in `rig.app.state_store.changes(0)` says ABSENT.
    - `test_no_reminders_when_you_are_away`: with `only_breaks(config, warmup_min=2, sit_limit_min=3)` (import it from
      `test_app_jobs.py`'s helpers, or copy its three lines), `rig.watch(4 * 60, LAPTOP, step=1)`, then
      `rig.watch(2 * 60, LAPTOP, person=False, step=1)` → `rig.app.desk_status.user_active(rig.clock.now())` is false
      and `rig.tick(rig.app.reminders)` sends no `group_message`. Then `rig.watch(5, LAPTOP)` → `user_active` is true.
    - `test_a_restart_keeps_the_states`: `rig.watch(5, CUP_FAR)`, `rig.app.stop(timeout=1)`; a second `DeskRig` on the
      same `tmp_path` (with the clock moved on 10 s) → `snapshot().objects['cup'].state == 'PRESENT'` before any
      picture, and `rig2.watch(5, CUP_FAR)` adds no `appeared`.
    - `test_object_states_are_saved_every_minute`: `'save object states'` is in `rig.app.scheduler.status()` with
      `every_sec == rig.config.object_states.snapshot_every_sec`.
- [ ] **Step 2: Run** those four test files → fail.
- [ ] **Step 3: Wire it in** as listed above. Remove `someone_here_sec` from `RemindersSettings` and
  `config/settings.yaml`, and fix the comment in `core/habits/tracker.py:81` that names it.
- [ ] **Step 4: Run** `uv run pytest tests/unit/test_core_habits_desk_status.py tests/unit/test_core_habits_reminders.py tests/unit/test_core_workers_vision_habits.py tests/integration -q`
  → passes (all of `tests/integration`, because every whole-app test now goes through the switch).
- [ ] **Step 5: Commit** "Track object states in the app and pause reminders when you're away".

### Task 6: The chat waits 2 minutes

**Files:**
- Modify: `config/settings.yaml` (`agents.leave_after_sec: 45` → `120`, with the comment in "Every number")
- Modify: `tests/unit/test_core_agents_roster.py`, `tests/integration/test_app_pictures.py`

Two tests wait a fixed 50 s for "left the chat". They'll fail with 120, so they read the number from the settings:

- [ ] **Step 1: Change the tests first** (they still pass with 45, which proves they now follow the setting):
  - `test_core_agents_roster.py::test_object_joins_and_leaves`: `r.run(t, config.agents.leave_after_sec + 5, d('person'))`
    instead of `50`, and `left.ts - t == pytest.approx(config.agents.leave_after_sec, abs=0.5)` instead of `45.0`.
  - `test_app_pictures.py`, step 4: `rig.watch(rig.config.agents.leave_after_sec + 5, LAPTOP)` instead of `50`.
  - `grep -rn "watch(50\|run(t, 50\|45.0" tests` for any other test that waits for a leave.
- [ ] **Step 2: Run** them → pass.
- [ ] **Step 3: Change** `settings.yaml`.
- [ ] **Step 4: Run** `uv run pytest tests/unit/test_core_agents_roster.py tests/integration/test_app_pictures.py tests/integration/test_app_jobs.py -q` → passes.
- [ ] **Step 5: Commit** "Wait 2 minutes before an object leaves the chat".

### Task 7: Task 6 of the rule engine plan: switch on the hydration rule

From [the rule engine plan](2026-10-23-rule-engine-plan.md), Task 6, now that the state machine is in.

**Files:**
- Modify: `desktalk/bootstrap.py`, `desktalk/core/habits/reminders.py` (delete `NoRules`),
  `tests/unit/test_core_habits_reminders.py` (delete `TestNoRules`), `tests/contracts/rule_nudges_contract.py`
  (its docstring names `NoRules`), `config/protocols.yaml`, `docs/ARCHITECTURE.md`
- Test: `tests/integration/test_app_jobs.py`

**Interfaces:**
- Consumes: `RuleRunner(rules, states, bus, settings)` (already on the branch), the tracker from Task 5.
- Produces:
  - In `build()`: `rule_runner = RuleRunner(config.protocols.rules, object_states, bus, config.rule_engine)`, passed
    to `HabitReminders` in place of `NoRules()`.
  - `DeskTalk.rule_runner: RuleRunner` (with a docstring line), and `(self.rule_runner, c.rule_engine.check_every_sec)`
    in `background_jobs()`. Add it to the module docstring's list of background jobs.
  - `config/protocols.yaml`: the hydration rule `enabled: true`; the old `hydration:` section `enabled: false`, with a
    comment that the rule replaced its nudges (sip counting and the Habits page keep working).
  - `docs/ARCHITECTURE.md`: the rule engine in "The parts" (`core/habits/`), `RuleNudges` in "Interfaces"
    (`RuleRunner` / `FakeRuleNudges`), `rule_outcome` in "Events", `RuleRunner` in the `PeriodicWorker` row.

- [ ] **Step 1: Write the failing test** `test_hydration_rule_nudges_in_the_running_app`: a `DeskRig` whose config
  has the other old protocols off (`breaks`, `light`, `phone_free`, `food`, `tasks`, with `changed()`, like
  `only_breaks`). `BOTTLE = Detection('bottle', 0.9, BBox(450, 300, 490, 380))`.
  `rig.watch(61 * 60, LAPTOP, BOTTLE, step=1)` (a person is in every picture), then `rig.tick(rig.app.rule_runner)`
  and `rig.tick(rig.app.reminders)` → the event log has one `nudge` with `rule == 'rule.hydration'`. Then
  `rig.watch(3 * 60, LAPTOP, step=1)` and `rig.tick(rig.app.rule_runner)` → one `rule_outcome` with
  `result == 'worked'`.
- [ ] **Step 2: Run** `uv run pytest tests/integration/test_app_jobs.py::test_hydration_rule_nudges_in_the_running_app -v` → fails.
- [ ] **Step 3: Wire it in** as listed above.
- [ ] **Step 4: Run the whole fast suite**: `uv run pytest -m "not slow"`. Run `uv run ruff check` on every file this
  plan changed. Show both outputs. (No detection code changed, so no `slow` tests.)
- [ ] **Step 5: Commit** "Switch on the hydration rule". Then open the PR and review it as `AGENTS.md` says.

## Order of work

| Round | Tasks, side by side | Files each one owns |
|---|---|---|
| 1 | Task 1 · Task 2 | 1: `domain/`, `ports.py`, `config.py`, `settings.yaml`, `tests/fakes/`, `tests/contracts/`, `test_config_object_states.py`, `test_domain.py` · 2: `core/object_states/smoothing.py`, `machine.py`, `__init__.py` and their tests |
| 2 | Task 3 · Task 4 · Task 6 | 3: `core/object_states/tracker.py` and its test · 4: `adapters/storage/` and its test · 6: `settings.yaml`, the roster test, `test_app_pictures.py` |
| 3 | Task 5 | `bootstrap.py`, `desk_status.py`, `vision_habits.py`, `config.py`, `settings.yaml`, `ARCHITECTURE.md`, its tests |
| 4 | Task 7 | `bootstrap.py`, `reminders.py`, `protocols.yaml`, `ARCHITECTURE.md`, `test_app_jobs.py`, the reminders test, the rule nudges contract |

Task 2 only needs `Phase` from Task 1. Start both at once and have Task 2 import `Phase` from
`desktalk.domain.object_states`; if Task 2 is ready first, it waits for Task 1's commit before running its tests.

**Branch.** This plan is on `rule-engine` (PR randomnoobi/desk-talk#4, base `refactor`), because the tracker
implements the `ObjectStatesView` that branch added. Build it on a new branch `object-states` cut from `rule-engine`,
with its PR based on `rule-engine`, so PR #4 stays small enough to review. The cleanup plan's Task 4 also edits
`ports.py` and `config.py`, so don't run the two plans at the same time.

## Not in this plan (maybe later)

- Moving the chat roster and the old habit tracker onto these states. The habit tracker's own "at the desk" check (desk time, breaks) would then read the person switch too.
- Deleting `DeskState.person_last_seen` and `last_motion_ts` (and the producer lines that set them), which nothing reads after Task 5.
- Slowing YOLO down (say, 1 picture a second) while you're away, to save power. It can't stop completely, or it wouldn't see you come back.
- Ignoring things that are off the desk, like the roster's `only_on_desk` does.
- Deleting old rows. The table is append-only on purpose. It grows by a few MB a month, so this isn't needed yet.
- Telling two of the same thing apart (bottle #1 vs bottle #2). For now there's one state per word.
- A page in the app that shows the states.

## Decisions (answered 2026-10-23)

1. **The person is the on/off switch.** When you're PRESENT, reminders, idle chat and vision checks run. When you're away they pause, while detection, safety alerts and lost items keep going (section 3).
2. **ABSENT → PRESENT fires `appeared`.** OK'd.
3. **The chat waits 2 minutes** before "left the chat", the same as the longest sip (section 5).

## Calls I made while moving it onto `refactor` (check these)

1. **`reminders.someone_here_sec` goes away.** Someone who has it in `config/settings.local.yaml` gets a start-up
   error naming it, like any unknown setting. The message says which line to delete.
2. **`changes(since)` reads SQLite**, not a list in memory, so the rule engine still sees changes from before a restart.
3. **`watched_since` starts again after a restart** (and after a camera gap of `breaks.break_min`), so the hydration
   rule's `no_event … for: 60m` waits a full hour of watching after each start. The states themselves come back.
4. **Restoring skips `moved` rows**, because their `dwell_sec` is time at the old spot, not time in the state.
5. **The person shows up in the snapshot only once seen** (a box, or movement): before that it's "away".
6. **A new `ObjectStateStore` interface** and its in-memory fake, so the tracker's tests don't need SQLite and the
   SQLite class gets its own contract tests.
7. **Snapshots run on the existing `Scheduler`** (a `PeriodicWorker`), next to compacting the event log, rather than
   as a job of their own.
8. **Build on a new `object-states` branch from `rule-engine`**, not on PR #4 itself.
