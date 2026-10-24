# Rule Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

Date: 2026-10-23. Status: **draft, waiting for your OK.** No code has changed yet.

**Goal:** Habit nudges are written as rules in `config/protocols.yaml`, and a rule engine checks them against the
object states and turns the ones that are due into nudges. The hydration rule comes first.

**Architecture:** One pure function, `evaluate(rules, snapshot, history, now) → list[Nudge]` (the sketch's "Rule
engine" box). It never reads the clock, the camera or a file. Around it sits `RuleRunner`, a background job that
runs **once every 60 seconds** (`rule_engine.check_every_sec`). Each round it asks the object states for the snapshot
and the recent changes, runs `evaluate` once, keeps the nudges that are due, and checks earlier nudges' success.
`HabitReminders` picks up the kept nudges next to the old Protocols nudges, without running the rules again, so the
chat, the agents and the AI wording all stay the same.

**Tech Stack:** Python 3.12, uv, pydantic (the config models), `zoneinfo` (standard library), pytest, ruff.

**Spec:** the hydration rule we agreed on in chat on 2026-10-23 (copied in full under "The YAML" below), the sketch
(Observation → State machine → Events, with Protocol feeding both), and
[the object state machine plan](2026-10-22-object-state-machine-plan.md) for the states and events.

## Where this sits

```
 pictures ─▶ state machine ─▶ snapshot (each object: present / absent / unknown, since when)
                     │
                     └──────▶ changes  (appeared / removed / moved, with times)
                                    │
 config/protocols.yaml  rules: ─────┤
                                    ▼
                         RuleRunner.tick, once every 60 s (rule_engine.check_every_sec)
                           │  remembers when each rule nudged, and which nudges still await success
                           ├─▶ evaluate(rules, snapshot, history, now) → [Nudge]   (core/habits/rule_engine.py)
                           │     └─▶ kept as "due" until the next round
                           └─▶ success checks ─▶ RuleOutcome event ("worked" / "missed")

                         HabitReminders.tick (every 30 s, as today)
                           └─▶ takes the due rule nudges ─▶ agent says it in the chat ─▶ NudgeSent event
```

**What this plan needs first.** The object state machine doesn't exist yet on any branch. Its plan was written for
`main`'s old layout (`desktalk/app.py`, `desktalk/states/`) and needs moving onto the `refactor` layout before it's
built. Tasks 1–5 here don't wait for it: the engine only talks to the new `ObjectStatesView` interface, and the tests
use a fake. **Task 6 (switching it on in the app) waits until the state machine is in.** The state machine's
tracker then implements `ObjectStatesView` and runs the contract suite from Task 2.

**Branch.** This builds on `refactor`, like the cleanup plan, because `refactor` replaces most of `main`'s code. Cut a
`rule-engine` branch from `refactor`. The cleanup plan's Task 4 also edits `ports.py` and `config.py`, so don't run
the two plans at the same time.

## The YAML (agreed 2026-10-23)

A new `rules:` list at the end of `config/protocols.yaml`. Widened on 2026-10-23 at Sam's request: the rule now
counts every drink (cup, mug, bottle, water bottle, wine glass) and every sip logged in the app or in chat.

```yaml
rules:
  - id: hydration
    enabled: true
    habit: hydration            # which agent speaks for it in the chat
    timezone: local             # "local" = this computer's clock. Or a place name like "America/Los_Angeles",
                                # which follows daylight saving. A fixed offset like "+08:00" ignores it.
    gate:
      state: {object: person, is: present}
    when:
      all:
        - state:    {object: person, is: present, for: 60m}
        - no_event: {object: [cup, mug, bottle, water bottle, wine glass], events: [moved, removed], for: 60m}
        - no_habit: {habit: hydration, for: 60m}     # a sip logged in the app or in chat counts
    cooldown: 30m
    max_nudges_per_hour: 4
    quiet_hours: {start: "22:00", end: "07:00"}
    message: "You've been at the desk for an hour without a drink. Time for some water?"
    success:
      within: 5m
      when:
        any:
          - event: {object: [cup, mug, bottle, water bottle, wine glass], events: [moved, removed]}
          - habit: {habit: hydration}
```

## How a rule is checked

**A round runs once every 60 seconds** (`rule_engine.check_every_sec`), whatever else is going on. Between rounds,
the due nudges stay as they were, so a nudge can go out up to about 90 seconds after its rule became true (60 s
for the round plus up to 30 s for the reminder loop). That's fine for rules measured in tens of minutes.

**Each round, for each rule, in this order** (the cheap checks first, and the first "no" stops it):

1. `enabled` is false → skip.
2. Now is inside the rule's `quiet_hours`, read on the clock of the rule's `timezone` → skip.
3. Less than `cooldown` since this rule last nudged → skip.
4. This rule already nudged `max_nudges_per_hour` times in the last 60 minutes → skip.
5. `gate` isn't true → skip.
6. `when` isn't true → skip.
7. Otherwise → one `Nudge`.

**Conditions** (`event` and `no_event` take one object or a list, like `object: [cup, mug]`; any of them counts):

| Kind | True when |
|---|---|
| `state: {object, is, for}` | the object's state is `is`, and has been for at least `for` (no `for` = right now is enough) |
| `no_event: {object, events, for}` | none of `events` happened to the object in the last `for`, **and** the camera has been watching for at least `for` |
| `event: {object, events}` | one of `events` happened to the object after the nudge went out. Only allowed inside `success`. |
| `no_habit: {habit, for}` | nothing was logged for that habit (e.g. `hydration`) in the last `for`, **and** the camera has been watching for at least `for` |
| `habit: {habit}` | something was logged for that habit after the nudge went out. Only allowed inside `success`. |
| `all: [...]` / `any: [...]` / `not: ...` | every one / at least one / not this one |

**Objects the camera has never seen** count as `absent` since the camera started watching. With no bottle on the
desk at all, "the bottle hasn't moved in an hour" is true, so the hydration rule still nudges. Nothing showed that you
drank. (My call. It answers the question I left open in chat.)

**Quiet hours** read `start` and `end` as clock times in the rule's timezone. If `end` is earlier than `start`, the
quiet hours run past midnight. `start` counts as quiet and `end` doesn't. On daylight-saving nights the wall clock
decides: a start time in the missing hour begins at 03:00, and the repeated hour is quiet both times. If `start`
equals `end`, the app refuses to start (my call: today's code treats that as "never quiet", which is easy to miss).
These quiet hours come **on top of** the app's own quiet hours (Settings → quiet hours), which still silence everything.

**Waiting times** (`for`, `cooldown`, `within`, the 60 minutes for `max_nudges_per_hour`) are counted in real
seconds, never on the clock, so a daylight-saving jump doesn't change them.

**The nudge** that comes out:

| Nudge field | Value |
|---|---|
| `habit` | the rule's `habit` |
| `key` | `rule.<id>`, e.g. `rule.hydration` |
| `text` | the rule's `message` (Lens says it word for word; agents reword it with the AI) |
| `evidence` | made from the `when` conditions that held, joined with `; ` (wording below). The AI has to mention it. |
| `repeat_after_sec` | the rule's `cooldown` in seconds |

Evidence wording (minutes rounded down):
- `state` → `the {object} has been {is} for {m} minutes` (with no `for`: `the {object} is {is}`)
- Conditions under a `not` aren't described.
- `no_event` → `the {object} hasn't been {events joined with " or "} in {m} minutes`, where `m` counts from the last
  such event, or from when the camera started watching if there was none.

So the hydration rule gives: `the person has been present for 64 minutes; the bottle hasn't been moved or removed in
72 minutes`.

**Success.** After a rule nudges, each round (once a minute) also checks `success.when`. Because `event` looks back
to the moment of the nudge, a sip between two rounds still counts. True at any round within `within` → a `RuleOutcome` event with `result='worked'`. Still not true when `within` runs out →
`result='missed'`. Each nudge gets its own check. Outcomes only go to the event log for now (they show in the Log tab).

**Old and new hydration.** When Task 6 switches the rule on, it also sets `hydration.enabled: false` in the old
section, so you don't get two water nudges. That flag only stops the old nudges. Sip counting and the Habits page
keep working. The old "water on waking" nudge stops too, until it's written as a rule (that needs clock-time
conditions, see "Not in this plan").

## Global Constraints

- Everything in `AGENTS.md` holds: test first, interfaces first, `Clock` instead of `time.time()`, `logging` never
  `print`, plain words, and no numbers in code. Every number lives in `settings.yaml` or `protocols.yaml`. The one
  exception is `HOUR = 3600` for "per hour", which is what the setting's name means.
- Tests never need the network, an API key, a camera, a Pi or Docker. Engine tests pass `now` and fake data.
- Run with `uv run pytest <test file>` while working, then `uv run pytest -m "not slow"` at the very end.
  `uv run ruff check <changed files>` adds no new errors. Show the output.
- A mistake in a rule stops the app at start with a message that names the file, the rule and the key, like every
  other config error (`ConfigError`).
- The `refactor` branch's routes and replies don't change: `tests/characterization/` keeps passing.
- No two subagents edit the same file (the order of work is at the end).
- Commit messages: one short plain sentence.

## Review Focus

1. **Just after a start, or after the camera was off.** Expected: `no_event … for: 60m` is false until the camera has
   watched for 60 minutes. Otherwise every rule would fire 15 minutes after start, when the reminder warm-up ends.
   Test in Task 3: `test_no_event_needs_enough_watching`.
2. **The break rule's success ("you left") while reminders are paused.** Expected: once you leave, the person
   switch pauses reminders, but success checks keep running. So a nudge that worked is logged as `worked`, not
   lost. Test in Task 4: `test_success_is_checked_while_you_are_away`.
3. **Quiet hours on a daylight-saving night, in a rule with a place-name timezone.** Expected: 22:00–07:00 is quiet by
   the wall clock in summer, in winter, and on both change nights. Test in Task 3:
   `test_quiet_hours_follow_daylight_saving`.
4. **A typo in a rule** (`no_evnt:`, `is: presnt`, `for: 60`, `timezone: Mars/Base`, two rules with the same id).
   Expected: the app won't start, and the message names `config/protocols.yaml`, the rule and the bad word. Tests in
   Task 1: `test_rule_typos_name_the_rule_and_the_word`.
5. **A rule that keeps being true.** Expected: no more often than `cooldown`, never more than
   `max_nudges_per_hour` in any 60 minutes, and the app-wide `reminders.min_gap_sec` still applies on top. Tests in
   Task 3: `test_cooldown_and_hourly_cap`. In Task 5: `test_rule_nudges_respect_the_app_wide_gap`.

---

### Task 1: Read the rules from `protocols.yaml`

**Files:**
- Modify: `desktalk/config.py` (new types next to the `config/protocols.yaml` section; `Protocols` gets `rules`;
  a new `RuleEngineSettings` in the `settings.yaml` part; `AppConfig` gets `rule_engine`)
- Modify: `config/protocols.yaml` (the `rules:` list above, with a short header comment that explains the condition
  kinds in plain words. In this task the hydration rule ships with `enabled: false`, because nothing runs it yet.)
- Modify: `config/settings.yaml` (new section after `reminders:`)
- Test: `tests/unit/test_config_rules.py`

**Interfaces:**
- Produces (all in `desktalk/config.py`, all frozen `_Section`s with `extra='forbid'`):
  - `Duration`: written `30s`, `5m` or `1.5h`, stored as **seconds** (`float`). A bare number is an error:
    "must be a time like 30m".
  - `ObjectState = Literal['present', 'absent', 'unknown']`, `ChangeKind = Literal['appeared', 'removed', 'moved']`
  - `StateCheck(object: str, is_: ObjectState [yaml key "is"], for_: Duration | None = None [yaml key "for"])`
  - `EventCheck(object: str, events: tuple[ChangeKind, ...])` (at least one), and
    `NoEventCheck(object: str, events: tuple[ChangeKind, ...], for_: Duration [yaml key "for"])`
  - `Condition`: exactly one of `StateCond(state: StateCheck)`, `NoEventCond(no_event: NoEventCheck)`,
    `EventCond(event: EventCheck)`, `AllCond(all: tuple[Condition, ...])`, `AnyCond(any: tuple[Condition, ...])`,
    `NotCond(not_: Condition [yaml key "not"])`. `all` and `any` need at least one item.
  - `QuietWindow(start: ClockTime, end: ClockTime)`. `start == end` is an error.
  - `Success(within: Duration, when: Condition)`
  - `RuleConfig(id: str, enabled: bool, habit: Habit, timezone: str, gate: Condition, when: Condition,
    cooldown: Duration, max_nudges_per_hour: PositiveInt, quiet_hours: QuietWindow | None, message: str,
    success: Success | None)`. `timezone` is `local`, a `zoneinfo` place name or `±HH:MM`, and is checked at load.
    `event:` anywhere in `gate` or `when` is an error ("event only works inside success").
  - `Protocols.rules: tuple[RuleConfig, ...]`. The ids must be unique.
  - `RuleEngineSettings(check_every_sec: PositiveNumber)` as `AppConfig.rule_engine`
  - `rule_timezone(name: str) -> tzinfo`: `local` → the computer's zone, a place name → `ZoneInfo(name)`,
    `±HH:MM` → a fixed `timezone(timedelta(...))`.

- [ ] **Step 1: Write the failing tests** (they load from a temp folder, the way the existing config tests do):

```python
def test_hydration_rule_loads_as_written():
    rule = load_config(...).protocols.rules[0]
    assert rule.id == 'hydration' and rule.habit == 'hydration'
    assert rule.cooldown == 1800 and rule.max_nudges_per_hour == 4
    assert rule.quiet_hours.start == '22:00' and rule.quiet_hours.end == '07:00'
    assert rule.gate.state.object == 'person' and rule.gate.state.is_ == 'present'
    first, second = rule.when.all
    assert first.state.for_ == 3600
    assert second.no_event.events == ('moved', 'removed') and second.no_event.for_ == 3600
    assert rule.success.within == 300 and rule.success.when.event.object == 'bottle'

@pytest.mark.parametrize('text, seconds', [('30s', 30), ('5m', 300), ('1.5h', 5400)])
def test_durations_become_seconds(text, seconds): ...

@pytest.mark.parametrize('change, words', [
    ({'when': {'no_evnt': {...}}}, ['config/protocols.yaml', 'rules[0]', 'no_evnt']),
    ({'gate': {'state': {'object': 'person', 'is': 'presnt'}}}, ['rules[0].gate', 'presnt']),
    ({'cooldown': 60}, ['rules[0].cooldown', 'a time like 30m']),
    ({'timezone': 'Mars/Base'}, ['rules[0].timezone', 'Mars/Base']),
    ({'quiet_hours': {'start': '22:00', 'end': '22:00'}}, ['rules[0].quiet_hours']),
    ({'when': {'event': {'object': 'bottle', 'events': ['moved']}}}, ['rules[0].when', 'only works inside success']),
])
def test_rule_typos_name_the_rule_and_the_word(change, words):
    with pytest.raises(ConfigError) as err: ...
    assert all(w in str(err.value) for w in words)

def test_two_rules_with_the_same_id_is_an_error(): ...   # message names the id
def test_rule_timezone_kinds():  # 'local', 'America/Los_Angeles', '+08:00' → utcoffset in July is
    ...                          # (computer's), -7 h, +8 h
def test_any_all_not_nest(): ... # all: [any: [state, state], not: {state: ...}] loads
```

- [ ] **Step 2: Run** `uv run pytest tests/unit/test_config_rules.py -v` → fails (no `rules` field).
- [ ] **Step 3: Add the types and the settings section.** `Condition` is a recursive union of the six one-key
  sections, so `extra='forbid'` lets only one of them match. If pydantic's union errors come out unreadable, check
  the condition with a `model_validator(mode='before')` that looks at its one key and gives the plain message. The
  error has to go through `_problem`, so it names the file and the dotted path.

```yaml
# ── Rule engine (the rules themselves are in protocols.yaml → rules) ──
rule_engine:
  check_every_sec: 60         # how often the rules are checked (and whether earlier nudges worked)
```

- [ ] **Step 4: Run** the test file → passes. Also `uv run pytest tests/unit -k config` → still passes.
- [ ] **Step 5: Commit** "Read habit rules from protocols.yaml".

### Task 2: The data the engine reads, and its interfaces

**Files:**
- Create: `desktalk/domain/rules.py` (export from `desktalk/domain/__init__.py`)
- Modify: `desktalk/domain/events.py` (add `RuleOutcome`)
- Modify: `desktalk/ports.py` (add `ObjectStatesView`, `RuleNudges`)
- Modify: `tests/fakes/views.py` (add `FakeObjectStates`, `FakeRuleNudges`), `tests/fakes/__init__.py`
- Create: `tests/contracts/object_states_contract.py`
- Modify: `tests/contracts/test_fakes_pass_contracts.py`

**Interfaces:**
- Produces:
  - `ObjectSnapshot(label: str, state: str, since: float | None)`, frozen. `state` is `'PRESENT' | 'ABSENT' |
    'UNKNOWN'`, the state machine's own strings.
  - `DeskSnapshot(objects: Mapping[str, ObjectSnapshot], watched_since: float | None)`, frozen.
    `watched_since` = when the camera's current run of pictures began (`None` = no pictures yet).
  - `StateChange(ts: float, label: str, change: str)`, frozen. `change` is `appeared | removed | moved`.
  - `RuleHistory(changes: Sequence[StateChange], fired: Mapping[str, Sequence[float]])`, frozen. `changes` is oldest
    first. `fired` maps rule id → when it nudged, oldest first.
  - `RuleOutcome(Event)` with `TYPE = 'rule_outcome'`, `rule: str`, `habit: str`, `result: str` (`worked | missed`),
    `after_sec: float`.
  - `ObjectStatesView` (Protocol): `snapshot(self) -> DeskSnapshot`, `changes(self, since: float) -> list[StateChange]`
    (changes with `ts > since`, oldest first).
  - `RuleNudges` (Protocol): `nudges(self, now: float) -> list[Nudge]`, `note_fired(self, nudge: Nudge, now: float) -> None`.
  - `FakeObjectStates(objects=(), watched_since=None)` with `set(label, state, since)`, `change(ts, label, kind)`.
    `FakeRuleNudges(nudges=())` records `note_fired` calls in `.fired`.

- [ ] **Step 1: Write the contract** `object_states_contract.py`. It's a base class, like the other contracts,
  that any real `ObjectStatesView` will run:
  `test_changes_since_is_strictly_after` (a change at exactly `since` isn't returned),
  `test_changes_are_oldest_first`, `test_snapshot_states_are_the_three_strings`.
  Hook `FakeObjectStates` up in `test_fakes_pass_contracts.py`.
- [ ] **Step 2: Run** `uv run pytest tests/contracts/test_fakes_pass_contracts.py -v` → fails (import error).
- [ ] **Step 3: Add the types, interfaces and fakes** listed above.
- [ ] **Step 4: Run** the contract tests → pass. Also `uv run pytest tests/characterization -q` → still passes (a new
  event type must not change any reply).
- [ ] **Step 5: Commit** "Add the object states and rule nudge interfaces".

### Task 3: The engine

**Files:**
- Create: `desktalk/core/habits/rule_engine.py`
- Test: `tests/unit/test_core_habits_rule_engine.py`

**Interfaces:**
- Consumes: Task 1's config types and `rule_timezone`, Task 2's `DeskSnapshot`, `RuleHistory` and `StateChange`.
- Produces:
  - `evaluate(rules: Sequence[RuleConfig], snapshot: DeskSnapshot, history: RuleHistory, now: float) -> list[Nudge]`,
    in the order the rules are written.
  - `holds(cond: Condition, snapshot: DeskSnapshot, changes: Sequence[StateChange], now: float,
    events_after: float | None = None) -> bool`. `events_after` is the time an `event` condition counts from; it's
    only set for success checks.
  - `check_success(rule: RuleConfig, fired_at: float, snapshot: DeskSnapshot, changes: Sequence[StateChange],
    now: float) -> str | None` → `'worked'`, `'missed'`, or `None` (still waiting).
  - `in_quiet_hours(window: QuietWindow, timezone: str, now: float) -> bool`
  - `lookback_sec(rules: Sequence[RuleConfig]) -> float`: the longest `for` or `within` in any rule. The runner asks
    for this much history.

Helpers in the tests build `RuleConfig`s with `RuleConfig.model_validate({...})` from the YAML above as a dict, so
the tests read like the YAML. `T0 = 1_780_000_000.0`.

- [ ] **Step 1: Write the failing tests.**

```python
def test_hydration_nudges_after_an_hour_with_an_untouched_bottle():
    snap = desk(person=('PRESENT', T0), bottle=('PRESENT', T0), watched_since=T0)
    [n] = evaluate([HYDRATION], snap, history(), T0 + 3600)
    assert n.key == 'rule.hydration' and n.habit == 'hydration' and n.repeat_after_sec == 1800
    assert n.text == HYDRATION.message
    assert n.evidence == ('the person has been present for 60 minutes; '
                          "the bottle hasn't been moved or removed in 60 minutes")

def test_not_yet_at_59_minutes(): ...                           # same, at T0 + 3599 → []
def test_a_sip_resets_the_hour():                              # removed at T0+1800 → [] at T0+3600, one at T0+5400
def test_gate_closed_when_you_are_away():                      # person ABSENT → [] even after 2 h
def test_no_event_needs_enough_watching():                     # watched_since = now - 300, no changes → []
def test_never_seen_bottle_counts_as_absent_since_watching():  # no bottle in snapshot, watched 60 min → one nudge
def test_state_for_needs_the_whole_time():                     # person PRESENT since now - 1800 → []
def test_any_all_not(): ...                                    # each combinator, true and false cases

def test_cooldown_and_hourly_cap():
    rule = HYDRATION.model_copy(update={'cooldown': 600, 'max_nudges_per_hour': 2})
    assert evaluate([rule], ready, history(fired={'hydration': [now - 599]}), now) == []
    assert len(evaluate([rule], ready, history(fired={'hydration': [now - 600]}), now)) == 1
    assert evaluate([rule], ready, history(fired={'hydration': [now - 3000, now - 1200]}), now) == []
    assert len(evaluate([rule], ready, history(fired={'hydration': [now - 3601, now - 1200]}), now)) == 1

@pytest.mark.parametrize('local, quiet', [('21:59', False), ('22:00', True), ('02:00', True),
                                          ('06:59', True), ('07:00', False), ('13:00', False)])
def test_quiet_hours_wrap_past_midnight(local, quiet): ...      # timezone 'America/Los_Angeles', a day in July

def test_quiet_hours_follow_daylight_saving():
    la = 'America/Los_Angeles'
    w = QuietWindow(start='22:00', end='07:00')
    assert in_quiet_hours(w, la, ts('2026-07-01 22:30', la))          # summer (PDT)
    assert in_quiet_hours(w, la, ts('2026-01-15 22:30', la))          # winter (PST)
    assert in_quiet_hours(w, la, first_and_second('2026-11-01 01:30', la))   # fall back: both times
    early = QuietWindow(start='02:30', end='05:00')
    assert not in_quiet_hours(early, la, ts('2026-03-08 01:59', la))
    assert in_quiet_hours(early, la, ts('2026-03-08 03:00', la))      # spring forward: begins at 03:00

def test_fixed_offset_ignores_daylight_saving(): ...  # '+08:00': 22:30 there is quiet in both July and January

def test_success_worked_missed_and_waiting():
    fired = T0
    assert check_success(HYDRATION, fired, snap, [], fired + 60) is None
    assert check_success(HYDRATION, fired, snap, [change(fired + 120, 'bottle', 'removed')], fired + 130) == 'worked'
    assert check_success(HYDRATION, fired, snap, [change(fired - 5, 'bottle', 'removed')], fired + 300) == 'missed'

def test_break_style_success_on_state():  # success: {within: 2m, when: {state: {object: person, is: absent}}}
    ...                                   # person ABSENT at fired + 100 → 'worked'
```

- [ ] **Step 2: Run** `uv run pytest tests/unit/test_core_habits_rule_engine.py -v` → fails (no module).
- [ ] **Step 3: Write `rule_engine.py`.** It follows the order in "How a rule is checked". `in_quiet_hours` turns
  `now` into wall-clock minutes since midnight with `datetime.fromtimestamp(now, rule_timezone(tz))` and compares
  them the same way `in_hour_window` does, but in minutes. `max_nudges_per_hour` counts the `fired` times with
  `now - t < HOUR`. The evidence wording is in "How a rule is checked". No `time.time()` anywhere.
- [ ] **Step 4: Run** the test file → passes. `uv run ruff check desktalk/core/habits/rule_engine.py tests/unit/test_core_habits_rule_engine.py`.
- [ ] **Step 5: Commit** "Add the rule engine that turns rules into nudges".

### Task 4: The runner

**Files:**
- Create: `desktalk/core/habits/rule_runner.py`
- Test: `tests/unit/test_core_habits_rule_runner.py`

**Interfaces:**
- Consumes: `evaluate`, `check_success` and `lookback_sec` (Task 3); `ObjectStatesView`, `RuleNudges`, `EventBus`,
  `PeriodicWorker` and `RuleOutcome` (Task 2); `RuleConfig` and `RuleEngineSettings` (Task 1).
- Produces: `RuleRunner(rules: Sequence[RuleConfig], states: ObjectStatesView, bus: EventBus,
  settings: RuleEngineSettings)`. It implements both `RuleNudges` and `PeriodicWorker` (`name = 'rule engine'`,
  `tick(now) -> float` returns `settings.check_every_sec`).
  - `tick(now)` is the **only** place the rules run, once per `check_every_sec`. It reads `states.snapshot()` once
    and `states.changes(...)` once, going back to the earlier of `now - lookback` and the oldest open check's
    `fired_at`. Then it:
    1. runs `evaluate(rules, snapshot, RuleHistory(changes, fired), now)` and keeps the result as the due list,
       replacing the last one;
    2. runs `check_success` on each open check. On `'worked'` or `'missed'` it publishes
       `RuleOutcome(ts=now, rule=id, habit=rule.habit, result=..., after_sec=now - fired_at)` and closes the check.
  - `nudges(now)` → a copy of the due list from the last `tick`. It never runs the rules or reads the states.
    Before the first `tick` it's `[]`.
  - `note_fired(nudge, now)` → ignores keys that don't start with `rule.`. Otherwise it records `now` for that rule
    (keeping the last hour), takes that rule's nudge off the due list so it can't go out twice before the next round,
    and, if the rule has `success`, opens a success check.
  - It's thread-safe: `nudges` / `note_fired` run on the reminders thread and `tick` on its own, so one lock covers
    the fire log and the open checks.
  - Fire times are kept in memory. A restart forgets them, and the reminder warm-up (`reminders.warmup_min`) covers
    that.

- [ ] **Step 1: Write the failing tests** with `FakeObjectStates` and `SyncEventBus`:
  - `test_runner_returns_the_engines_nudges`: an untouched bottle for an hour, `tick(now)` → `nudges(now)` is one
    `rule.hydration` nudge.
  - `test_rules_run_only_on_tick`: `FakeObjectStates` counts its `snapshot()` calls. `nudges()` before any `tick` →
    `[]`, with 0 calls. After `tick(now)` → 1 call. Ten more `nudges()` calls → still 1. The bottle is `removed` at
    `now + 10` → `nudges(now + 30)` still shows the nudge, and `tick(now + 60)` clears it.
  - `test_note_fired_takes_it_off_the_due_list`: `tick`, `note_fired` → `nudges(now + 1) == []`. The next `tick` at
    `now + 60` is still in the cooldown → `[]`. A `tick` at `now + 1800` → one again.
  - `test_note_fired_ignores_old_protocols_nudges`: a nudge with key `hydration.pace` changes nothing.
  - `test_outcome_worked_is_published`: fire, then a bottle `removed` 2 minutes later, then `tick` → one
    `RuleOutcome(result='worked', after_sec=120)` and no second one on the next `tick`.
  - `test_outcome_missed_after_within`: fire, nothing, `tick(fired + 301)` → `result='missed'`.
  - `test_success_is_checked_while_you_are_away`: a break-style rule. Fire, the person goes ABSENT, `tick` → `worked`.
    (The runner's tick never asks `DeskStatus`, which is the point.)
  - `test_runner_passes_the_periodic_worker_contract`: `isinstance(runner, PeriodicWorker)`, and `tick` returns
    `check_every_sec` (the test builds settings with 60 and checks for 60).
  - `test_sip_between_rounds_counts_as_success`: fire at `T`, the bottle is `removed` at `T + 20` and back at
    `T + 40`, `tick(T + 60)` → `worked`.
- [ ] **Step 2: Run** `uv run pytest tests/unit/test_core_habits_rule_runner.py -v` → fails.
- [ ] **Step 3: Write `rule_runner.py`.**
- [ ] **Step 4: Run** → passes. Ruff on both files.
- [ ] **Step 5: Commit** "Add the rule runner that remembers nudges and checks if they worked".

### Task 5: Reminders ask the rules too

**Files:**
- Modify: `desktalk/core/habits/reminders.py`
- Modify: `tests/unit/test_core_habits_reminders.py` (and every other place that builds `HabitReminders` in tests:
  `grep -rn "HabitReminders(" tests`)

**Interfaces:**
- Consumes: `RuleNudges` and `FakeRuleNudges` (Task 2).
- Produces: `HabitReminders(habits, rules: RuleNudges, status, roster, profiles, voice, notifier, bus, settings)`,
  with `rules` as the new second argument. In `tick`:
  `nudges = [*self._rules.nudges(now), *self._habits.nudges(now, self._status.open_todos())]`.
  After sending, it calls both `self._rules.note_fired(nudge, now)` and `self._habits.note_fired(nudge, now)` (the
  tracker counts every nudge in `nudges_sent`). Rule nudges go first. Update the module docstring to say so.

- [ ] **Step 1: Write the failing tests:**
  - `test_rule_nudge_is_said_and_noted`: `FakeRuleNudges([rule_nudge])` and `FakeHabits()` → the notifier gets its
    text, `NudgeSent.rule == 'rule.hydration'`, and both fakes saw `note_fired`.
  - `test_rule_nudges_go_before_old_ones`: one of each → the rule's goes out.
  - `test_rule_nudges_respect_the_app_wide_gap`: a second tick within `min_gap_sec` sends nothing.
  - `test_no_rule_nudges_while_away`: `FakeDeskStatus(active=False)` → `FakeRuleNudges.nudges` is never called.
- [ ] **Step 2: Run** `uv run pytest tests/unit/test_core_habits_reminders.py -v` → fails.
- [ ] **Step 3: Change `HabitReminders`** and the test constructors. `bootstrap.py` is left for Task 6. Until then,
  pass it an empty `RuleNudges` so the app still builds: a two-line `NoRules` class in `reminders.py` that returns
  `[]` and ignores `note_fired`.
- [ ] **Step 4: Run** the reminders tests plus `uv run pytest tests/integration/test_app_build.py -q` → pass.
- [ ] **Step 5: Commit** "Let reminders send nudges from the rule engine".

### Task 6: Switch it on (after the state machine is in)

**Waits for:** the object state machine on this branch, with its tracker implementing `ObjectStatesView` and
running `ObjectStatesContract`.

**Files:**
- Modify: `desktalk/bootstrap.py` (build `RuleRunner(config.protocols.rules, object_states, bus, config.rule_engine)`,
  pass it to `HabitReminders` instead of `NoRules`, and add it to the supervisor like the other workers)
- Modify: `desktalk/core/habits/reminders.py` (delete `NoRules`)
- Modify: `config/protocols.yaml` (hydration rule `enabled: true`; old `hydration.enabled: false`, with a comment that
  the rule replaced its nudges)
- Modify: `docs/ARCHITECTURE.md` (one row for the rule engine in "The pieces", one for `rule_outcome` in "Events")
- Test: `tests/integration/test_app_jobs.py`

- [ ] **Step 1: Write the failing test** `test_hydration_rule_nudges_in_the_running_app`: build the app with the
  integration kit and a `FakeClock`. Feed pictures with a person and a bottle that never moves for 61 minutes, past
  the warm-up. Run the rule engine's tick, then the reminders tick → one `NudgeSent` with `rule='rule.hydration'`. Then feed 3 minutes without the
  bottle, run the rule engine's tick, and check for `RuleOutcome(result='worked')`.
- [ ] **Step 2: Run** it → fails.
- [ ] **Step 3: Wire it in** as listed above.
- [ ] **Step 4: Run** the whole fast suite: `uv run pytest -m "not slow"`. Run `uv run ruff check` on every file this
  plan changed. Show both outputs.
- [ ] **Step 5: Commit** "Switch on the hydration rule". Then open the PR and review it as `AGENTS.md` says.

## Order of work

| Round | Tasks, side by side | Files each one owns |
|---|---|---|
| 1 | Task 1 · Task 2 | 1: `config.py`, `protocols.yaml`, `settings.yaml`, `test_config_rules.py` · 2: `domain/`, `ports.py`, `tests/fakes/`, `tests/contracts/` |
| 2 | Task 3 · Task 5 | 3: `rule_engine.py` and its test · 5: `reminders.py` and the reminder tests |
| 3 | Task 4 | `rule_runner.py` and its test |
| later | Task 6 | after the state machine |

## Not in this plan (maybe later)

- **Clock-time conditions** ("within 60 minutes of waking", "after sunset"). They're needed for the "water on
  waking", light and screens rules. The `timezone` field is already there for them.
- **Counts and amounts** ("fewer than 6 sips today", millilitres behind pace). The engine only sees states and
  events, not the habit tracker's daily totals.
- **A word that stands for a group** ("a drink"). A list of objects works (`object: [bottle, cup]`).
- **Using the outcomes**: showing "worked 7 of 10 times" on the Habits page, or nudging less when they keep missing.
- **Messages that fill in numbers** ("{minutes}"). For now the AI adds them from the evidence. Lens says the fixed text.
- **Moving the other habits** (breaks, phone-free, light, food, tasks) onto rules, one per PR, each turning its old
  section's nudges off.

## Calls I made (check these)

1. An object the camera has never seen counts as `absent` since the camera started watching, so no bottle means
   the hydration rule still nudges.
2. `no_event` is false until the camera has watched for the whole `for`.
3. If quiet hours `start` equals `end`, the app refuses to start.
4. `event:` only works inside `success` for now.
5. Rule nudges go before the old Protocols nudges, and the app-wide 10-minute gap still applies to both.
6. Turning the rule on turns the old hydration nudges off, including "water on waking", until clock-time
   conditions exist.
7. Nudge times are kept in memory, so a restart forgets cooldowns (the 15-minute warm-up covers it).
8. Outcomes only go to the event log for now.
9. This is built on `refactor`, not `main`.
10. The rules run once every 60 seconds, in the runner's own job, and success checks run in the same round. A nudge
    can therefore go out up to about 90 seconds after its rule became true.
