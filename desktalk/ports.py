"""
The interfaces (ports) between the parts of Desk Talk.

Every part is written against these small interfaces, never against another part's class.
desktalk/bootstrap.py builds the real parts and connects them; tests build the same parts
with the fakes in tests/fakes/. Each interface has a contract test suite in tests/contracts/
that the real version and the fake both pass.

Change this file only on purpose: every part depends on it.

The desk right now is five small interfaces near the end of this file: LivePictures, DeskScene, SafetyBoard,
CharacterMemory and PhotoAlbum (desktalk/core/desk/ has one class for each). The last sections are what the
web layer asks of the running app (desktalk/bootstrap.py → DeskTalk types its fields with these), and the
developer page's tools (tests, smoothing, the database, the data folder).
"""

from __future__ import annotations

import queue
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from desktalk.domain import (
    AgentProfile,
    Box,
    Danger,
    DataFile,
    DeskRules,
    DeskSnapshot,
    Detection,
    Event,
    Frame,
    Gravity,
    Light,
    Nudge,
    Photo,
    Picture,
    QueryResult,
    RunStatus,
    SafetyNow,
    SeenObject,
    Size,
    SmoothingStep,
    StateChange,
    StateRecord,
    TableInfo,
    TestItem,
    UploadInfo,
)


@runtime_checkable
class Clock(Protocol):
    """The time. Code asks the clock instead of calling time.time(), so tests can move time by hand."""

    def now(self) -> float:
        """Seconds since 1970 (wall clock): for timestamps and "what day is it"."""
        ...

    def monotonic(self) -> float:
        """Seconds that only ever go up: for measuring how long something took."""
        ...

    def sleep(self, seconds: float) -> None:
        """Wait. (Background jobs don't call this: the Supervisor does their waiting, so stopping is instant.)"""
        ...

    def local_hour(self, now: float) -> int:
        """The hour (0-23) of the local time at `now` (seconds since 1970)."""
        ...

    def local_day(self, now: float) -> str:
        """The local day at `now`, e.g. '2026-10-24' (the same day names the event log uses)."""
        ...

    def utc_offset_sec(self, now: float) -> float:
        """How many seconds the local time at `now` is ahead of UTC (negative when behind): for the local time
        helpers in desktalk/util.py (their utc_offset_sec)."""
        ...


@runtime_checkable
class PeriodicWorker(Protocol):
    """A background job (reminders, Lens, idle chat, the scheduler, the camera producer ...).

    The Supervisor (desktalk/runtime/) calls tick() over and over in the job's own thread.
    Tests call tick() directly with a FakeClock: no threads, no waiting.
    A job that should wait before its first round says so when it's added:
    supervisor.add(job, start_after=seconds).
    """

    name: str

    def tick(self, now: float) -> float:
        """Do one round of work. Returns how many seconds to wait before the next round (a number, never None).

        Raising means this round failed: the Supervisor logs it and tries again after a
        growing wait (settings → supervisor), so one bad round never switches a feature off."""
        ...


# ── Camera and detection ──────────────────────────────────────────────
@runtime_checkable
class Detector(Protocol):
    """Picture → what's in it. UltralyticsDetector on this computer (phone, webcam, and backup for the Pi)."""

    @property
    def label(self) -> str:
        """For the app, e.g. "yolo26m-objv1-150.pt (365 kinds of objects, mps)"."""
        ...

    def detect(self, picture: Any) -> list[Detection]:
        """Detections in a BGR numpy picture, boxes in its pixels.
        Raises DetectorUnavailable (plain-words reason) when it can't answer; never anything else."""
        ...


@runtime_checkable
class FrameSource(Protocol):
    """Where pictures come from: the Pi, the phone or a webcam. Keeps only the newest picture,
    so a slow computer skips pictures instead of falling behind."""

    @property
    def kind(self) -> str:
        """pi, phone or webcam."""
        ...

    @property
    def connected(self) -> bool:
        """A picture came in recently (settings → camera.disconnect_after_sec)."""
        ...

    @property
    def fps(self) -> float:
        """Pictures a second coming in lately (0 when not connected)."""
        ...

    def read(self, timeout: float) -> Frame | None:
        """The newest picture not handed out yet; waits up to `timeout` seconds, then None."""
        ...


@runtime_checkable
class PictureInbox(Protocol):
    """A camera that sends pictures to us (the Pi, the phone): the web layer hands each upload to it."""

    def push(self, jpeg: bytes, info: UploadInfo | None = None) -> bool:
        """False if the bytes aren't a picture."""
        ...



@runtime_checkable
class PiUploads(Protocol):
    """The Pi as the camera: what came with its pictures (Lens asks)."""

    def latest_upload(self) -> tuple[UploadInfo, float] | None:
        """What the Pi sent with its newest picture (health, its own clock time ...) and when that picture
        arrived, on this computer's clock. None before the first picture."""
        ...

    def clock_off_sec(self) -> float | None:
        """The Pi's clock minus this computer's (negative = behind), judged by its quickest recent picture
        (detection time taken off). None before a picture that said when it was taken."""
        ...


@runtime_checkable
class PiStatus(Protocol):
    """The Pi as the camera: what the app's pages show about it (/api/live, /api/health)."""

    def pi_info(self) -> dict[str, Any]:
        """The newest picture's id (last_frame_id) and everything the Pi has said about itself."""
        ...

    def status(self) -> dict[str, Any]:
        """pi_info() plus upload_format, detector (ok / busy / off), detector_reason, detect_ms, model and
        clock_off_sec (seconds; negative = behind)."""
        ...


@runtime_checkable
class PhoneCamera(Protocol):
    """The phone as the camera: what Lens asks about it."""

    def recent_gravity(self) -> Gravity | None:
        """The phone's latest tilt reading, or None if it hasn't sent one lately."""
        ...

    @property
    def stopped_by_user(self) -> bool:
        """You pressed Stop on the phone (so the video stopping isn't a problem)."""
        ...


@runtime_checkable
class PhoneControls(PhoneCamera, Protocol):
    """The phone as the camera: what the phone page's routes tell it (/api/phone/frame, /api/phone/stop), and
    its tilt when the desk corners are marked (/api/rules)."""

    def set_motion(self, gravity: Gravity | None = None, rotation: int | None = None) -> None:
        """How the phone was tilted and how far it turned the picture, sent with its newest picture.
        None = it didn't say (the old value stays)."""
        ...

    def mark_stopped(self) -> None:
        """You pressed Stop on the phone: not connected from now on (until the next picture)."""
        ...

# ── Events and storage ────────────────────────────────────────────────
@runtime_checkable
class EventBus(Protocol):
    """Publish / subscribe inside this program. Each consumer has its own bounded queue and thread,
    so a slow consumer never holds up the camera. When a queue is full the oldest *picture*
    event is dropped (never joins, alerts or habits) and counted."""

    def publish(self, event: Event) -> None:
        ...

    def subscribe(self, name: str, handler: Callable[[Event], None],
                  types: Iterable[type[Event]] | None = None) -> None:
        """`types` = event classes this consumer wants (None = all). A handler that raises is counted
        and logged; the consumer keeps going."""
        ...

    def wait_idle(self, timeout: float) -> bool:
        """Wait until every consumer has handled everything published so far (tests, shutdown)."""
        ...

    def stats(self) -> list[dict[str, Any]]:
        """Per consumer: name, handled, waiting, dropped, errors."""
        ...

    def close(self, timeout: float) -> list[str]:
        """Stop handing out events (bootstrap, when the app stops): each consumer finishes what it has, waiting
        up to `timeout` seconds. Returns the names of consumers still busy after that."""
        ...


@runtime_checkable
class EventStore(Protocol):
    """The event log: one folder per day in data/events/ (see docs/ARCHITECTURE.md)."""

    @property
    def folder(self) -> Path:
        """Where the days are kept (the Log page shows it)."""
        ...

    @property
    def last_compaction(self) -> dict[str, Any] | None:
        """What the last compact() did, with when (ts); None before the first."""
        ...

    def write(self, event: Event) -> None:
        ...

    def days(self) -> list[str]:
        """Days with a log, newest first (YYYY-MM-DD)."""
        ...

    def events(self, day: str, types: set[str] | None, limit: int) -> list[dict[str, Any]]:
        """Non-picture events of a day as dicts, newest first."""
        ...

    def presence(self, day: str, include_live: bool = True) -> list[dict[str, Any]]:
        """What was on the desk when, as intervals."""
        ...

    def recent_detections(self, day: str, limit: int) -> list[dict[str, Any]]:
        ...

    def counts(self, day: str) -> dict[str, Any]:
        ...

    def compact(self, now: float) -> dict[str, Any]:
        """Turn per-picture lines into intervals."""
        ...

    def prune(self, now: float) -> list[str]:
        """Delete days older than settings → event_log.keep_days. Returns the days deleted."""
        ...


@runtime_checkable
class ObjectStateStore(Protocol):
    """The history of object states: one row each time an object appears, goes away or moves,
    plus a snapshot of every state now and then (so a restart can pick up where it left off)."""

    def append(self, records: Sequence[StateRecord]) -> None:
        ...

    def latest_states(self, since: float) -> dict[str, StateRecord]:
        """Per object, its newest `appeared`, `removed` or `snapshot` row with `ts >= since`.
        `moved` rows are skipped: they don't change the state, and their `dwell_sec` isn't the time in the state."""
        ...

    def changes(self, since: float) -> list[StateRecord]:
        """The `appeared`, `removed` and `moved` rows with `ts > since`, oldest first
        (rows with the same `ts` in the order they were added)."""
        ...

    def close(self) -> None:
        ...


@runtime_checkable
class KeyValueStore(Protocol):
    """Small JSON documents (preferences, habit days, learned personalities, desk rules).
    Saves are all-or-nothing (temp file + rename); a broken file reads as missing and is logged."""

    def load(self, key: str, default: Any = None) -> Any:
        """A copy of the stored value: changing it doesn't change the store until save()."""
        ...

    def save(self, key: str, value: Any) -> None:
        ...

    def keys(self, prefix: str = '') -> list[str]:
        ...


# ── AI and chat ───────────────────────────────────────────────────────
@runtime_checkable
class ChatModel(Protocol):
    """The AI (Gemini / OpenAI / Groq / Ollama through the OpenAI-compatible API)."""

    @property
    def label(self) -> str:
        ...

    @property
    def ready(self) -> bool:
        """An AI is set up (a key in .env, or Ollama)."""
        ...

    @property
    def is_local(self) -> bool:
        """The chat runs on this computer (Ollama)."""
        ...

    @property
    def local_vision(self) -> bool:
        """Pictures go to Ollama on this computer, so looking at them uses no cloud quota."""
        ...

    def chat(self, messages: Sequence[Mapping[str, Any]], *, max_tokens: int, temperature: float,
             background: bool = False) -> str:
        """Reply text. `background` = nobody is waiting (idle chat, reminders): these pause after a rate limit
        so the quota is saved for replying to you. Raises AIUnavailable (plain-words reason)."""
        ...

    def vision(self, jpeg: bytes, prompt: str, *, background: bool = True) -> str:
        """Reply about a picture. Raises AIUnavailable."""
        ...

    def problem(self) -> str | None:
        """The current AI problem in plain words, or None if the last call worked."""
        ...


@runtime_checkable
class Notifier(Protocol):
    """Sends a message to every open app page (the live chat, over SSE)."""

    def send(self, message: Mapping[str, Any]) -> dict[str, Any]:
        """Returns the message as sent (with its id and ts filled in)."""
        ...


@runtime_checkable
class ChatVoice(Protocol):
    """How a character puts things into words (the message generator)."""

    def phrase_nudge(self, agent_id: str, nudge: Nudge) -> str | None:
        """The agent's words for a habit reminder (AI, or the nudge's ready-made text). None = say nothing."""
        ...


# ── What parts ask each other ─────────────────────────────────────────
@runtime_checkable
class RosterView(Protocol):
    def member_ids(self) -> list[str]:
        """Agents in the chat right now, in the order they joined."""
        ...


@runtime_checkable
class Profiles(Protocol):
    """Who each detected object becomes (config/agents.yaml + personalities the AI wrote)."""

    def get(self, agent_id: str) -> AgentProfile | None:
        ...

    def for_label(self, label: str) -> AgentProfile | None:
        """The agent a detector word belongs to (None for ignored words)."""
        ...

    def mention_words(self) -> dict[str, str]:
        """Every word that can @mention an agent in the chat → its id."""
        ...

    def all(self) -> list[AgentProfile]:
        ...

    def ids_for_habit(self, habit: str) -> list[str]:
        ...

    def labels_for_habit(self, habit: str) -> set[str]:
        ...

    def is_ignored(self, label: str) -> bool:
        ...

    def relationship_risk(self, a: str, b: str) -> str | None:
        ...

    def set_personality(self, agent_id: str, text: str) -> None:
        ...


@runtime_checkable
class HabitsView(Protocol):
    """Today's habits (the habit tracker)."""

    def today(self, now: float) -> dict[str, Any]:
        ...

    def bump(self, key: str, amount: int = 1) -> None:
        """Count something that happened (an alert, a lost item found ...)."""
        ...

    def nudges(self, now: float, open_todos: Sequence[str]) -> list[Nudge]:
        """The reminders ready to send now, best first (ones sent too recently are left out)."""
        ...

    def note_fired(self, nudge: Nudge, now: float) -> None:
        ...


@runtime_checkable
class HabitsLifecycle(Protocol):
    """Saving today's habit numbers: only bootstrap calls this (every habit_tracker.save_check_every_sec, and at
    shutdown). The parts that read or count habits use HabitsView."""

    def save(self, force: bool = False) -> None:
        """Save today's numbers if they changed since the last save (always, with force)."""
        ...


@runtime_checkable
class HabitLog(Protocol):
    """The habit tracker's log(): what a chat action (a sip, a break ...) is written to."""

    def log(self, habit: str, action: str, **details: Any) -> Any:
        ...


@runtime_checkable
class ObjectStatesView(Protocol):
    """Where each object on the desk is right now, and what changed lately (read by the rule engine)."""

    def snapshot(self) -> DeskSnapshot:
        """Every object we know of, with its state (PRESENT, ABSENT or UNKNOWN) and since when."""
        ...

    def changes(self, since: float) -> list[StateChange]:
        """The changes after `since` (a change at exactly `since` is left out), oldest first."""
        ...


@runtime_checkable
class ObjectStates(ObjectStatesView, Protocol):
    """The object states as the app keeps them: what the rule engine and the desk status read, plus writing a
    snapshot row of every object on the desk (only bootstrap calls this: the scheduler, and stop())."""

    def save_snapshot(self, now: float) -> int:
        """Write one snapshot row per object on the desk now; returns how many."""
        ...


@runtime_checkable
class RuleNudges(Protocol):
    """The reminders the rule engine can send, with the memory of when each one last went out."""

    def nudges(self, now: float) -> list[Nudge]:
        """The reminders ready to send now, best first (ones sent too recently are left out)."""
        ...

    def note_fired(self, nudge: Nudge, now: float) -> None:
        ...


@runtime_checkable
class Preferences(Protocol):
    """Choices made in the app (wake time, bedtime, quiet hours); they win over config/protocols.yaml."""

    def get(self, key: str) -> Any:
        ...

    def all(self) -> dict[str, Any]:
        ...

    def update(self, changes: Mapping[str, Any]) -> dict[str, Any]:
        ...

    def quiet_now(self, now: float) -> bool:
        """Quiet hours are on and `now` is inside them, by the local time."""
        ...


@runtime_checkable
class CameraHealthView(Protocol):
    """Lens's camera checks (brightness, blur, glare, the camera moving)."""

    def check(self, now: float, *, connected: bool, fps: float | None, gravity: Any, reference_gravity: Any,
              has_outline: bool, stopped: bool, quiet: bool = False) -> list[dict[str, Any]]:
        """Things Lens should say: dicts with key, kind (problem / fixed / tip) and text.
        `quiet` = quiet hours: problems are still followed, but nothing is said or counted as said."""
        ...


@runtime_checkable
class DeskStatus(Protocol):
    """Questions the background jobs ask before speaking up."""

    def camera_connected(self) -> bool:
        ...

    def warm(self, now: float) -> bool:
        """The camera has watched long enough for nudges (settings → reminders.warmup_min)."""
        ...

    def reminders_ready(self, now: float) -> bool:
        """Warm, camera on, and not quiet hours."""
        ...

    def user_active(self, now: float) -> bool:
        """You're at the desk: the person's object state is PRESENT."""
        ...

    def open_todos(self) -> list[str]:
        ...


@runtime_checkable
class PictureCheck(Protocol):
    """Lens looking at every analysed picture (brightness, blur, glare, the camera moving)."""

    def sample(self, picture: Any, detections: Sequence[Detection], now: float) -> None:
        ...

    def moved_recently(self, now: float) -> bool:
        """The camera itself moved in the last moment (so "things moved" isn't the desk changing)."""
        ...


@runtime_checkable
class LiveViewPainter(Protocol):
    """Draws the boxes and desk overlays (danger zones, desk edges, agent names) for the live view."""

    def paint(self, picture: Any, detections: Sequence[Detection]) -> Any:
        """A new picture with the drawings; the one passed in is left as it was."""
        ...


# ── Pictures, photos and the desk rules ───────────────────────────────
@runtime_checkable
class PictureTools(Protocol):
    """Everything core does to a picture (OpenCV in the real app), so core never imports cv2 itself.
    Pictures are numpy arrays: BGR colour or one gray channel."""

    def decode_jpeg(self, jpeg: bytes, *, gray: bool = False) -> Picture | None:
        """The picture in a JPEG (gray = read it straight into one gray channel). None if it isn't one."""
        ...

    def encode_jpeg(self, picture: Picture, quality: int) -> bytes | None:
        """The picture as a JPEG (`quality` 1-100). None if it can't be made (an empty picture)."""
        ...

    def size(self, picture: Picture) -> Size:
        ...

    def shrink(self, picture: Picture, width: int, height: int | None = None) -> Picture:
        """A smaller copy `width` pixels wide; as tall as keeps its shape, unless `height` is given."""
        ...

    def to_gray(self, picture: Picture) -> Picture:
        """A one-channel gray copy (a gray picture comes back as it is)."""
        ...

    def crop(self, picture: Picture, box: Box) -> Picture:
        """The part of the picture inside `box` (pixels), cut to the picture's own edges."""
        ...

    def brightness(self, gray: Picture) -> float:
        """Average brightness of a gray picture, 0-255."""
        ...

    def glare(self, gray: Picture, white_level: float) -> float:
        """Share (0-1) of a gray picture's pixels at `white_level` or brighter."""
        ...

    def difference(self, a: Picture, b: Picture) -> float:
        """How different two gray pictures look: the average change per pixel, 0-255.
        Pictures of different sizes count as completely different (infinity)."""
        ...

    def sharpness(self, gray: Picture) -> float:
        """How sharp a gray picture is (edges make it bigger; about 0 for a flat or very blurry one)."""
        ...


@runtime_checkable
class PhotoTaker(Protocol):
    """Keeps the live view picture as a photo for the chat and the Captures page (the proof photos)."""

    def save(self, label: str, crop_boxes: Sequence[Box] = ()) -> str | None:
        """Keep the newest live view picture, cropped around `crop_boxes` (if any). Returns the photo's id,
        or None when there is no picture yet."""
        ...


@runtime_checkable
class DeskLayout(Protocol):
    """The desk corners marked in Settings (what Lens needs from the desk rules)."""

    def has_outline(self) -> bool:
        """The desk corners are marked (Settings)."""
        ...

    def reference_gravity(self) -> tuple[float, ...] | None:
        """How the phone was tilted when the corners were marked (None if they aren't, or it wasn't the phone)."""
        ...


@runtime_checkable
class DeskRulesView(DeskLayout, Protocol):
    """The danger rules in use: config/settings.yaml → safety, with what the Settings page saved on top."""

    @property
    def current(self) -> DeskRules:
        """The rules right now. A save swaps in new ones, so a reader never sees half a change."""
        ...

    @property
    def loaded(self) -> bool:
        """Rules were saved from the Settings page (otherwise only the built-in ones are used)."""
        ...

    def save(self, changes: Mapping[str, Any]) -> None:
        """Replace these parts of the saved rules (danger_zones, edge_proximity, desk_outline ...); the rest
        stays as it was. Takes effect at once."""
        ...

    def clean_outline(self, points: Any) -> list[list[float]]:
        """Desk corners sent by the Settings page, each cut to 0-1 of the picture.
        Raises ValueError (plain words) for something that isn't corners."""
        ...

    def for_settings_page(self) -> dict[str, Any]:
        """What the Settings page shows: the saved rules, or the built-in ones where nothing was saved."""
        ...


# ── The desk right now ────────────────────────────────────────────────
# Five small interfaces, one per kind of thing the desk knows. Each method does its own locking, so a caller
# never holds a lock; a change that has to happen all at once is one method.
@runtime_checkable
class LivePictures(Protocol):
    """The newest pictures for the live view, per camera (0 = the first camera)."""

    def put(self, cam: int, plain: bytes, boxed: bytes | None, size: Size) -> None:
        """A camera's newest picture as JPEGs: plain, and with the boxes drawn on (None = no boxed one)."""
        ...

    def plain(self, cam: int | None = None) -> bytes | None:
        """The newest plain JPEG of `cam`; with no camera given, of the lowest-numbered camera that has one."""
        ...

    def boxed(self, cam: int | None = None) -> bytes | None:
        """The newest JPEG with boxes of `cam`; with no camera given, of the lowest-numbered camera that has one."""
        ...

    def camera_numbers(self) -> list[int]:
        """The cameras that have sent a picture, lowest first."""
        ...

    def size(self) -> Size:
        """How big the newest picture is."""
        ...

    def set_light(self, brightness: float, lux: float | None) -> None:
        ...

    def newest_light(self) -> Light | None:
        """The light in the newest analysed picture (None before the first)."""
        ...


@runtime_checkable
class DeskScene(Protocol):
    """What the camera sees on the desk right now. Everything that comes back is a copy."""

    def set_members(self, members: Mapping[str, SeenObject], now: float) -> None:
        """The agents in the chat that are in the picture just now (the roster), by agent id. Each one's first
        sighting this session is kept, and its last sighting becomes `now`."""
        ...

    def note_picture(self, seen: Mapping[str, SeenObject], now: float, *, person: bool, moved: bool,
                     new_session: bool) -> None:
        """One analysed picture: the best box for each detector word in it, whether a person was in it and
        whether things moved. new_session = the camera was off a while, so a new session starts now (one
        also starts with the very first picture)."""
        ...

    def members(self) -> dict[str, SeenObject]:
        """Agent id → its box, for the agents on the desk right now."""
        ...

    def seen(self) -> dict[str, SeenObject]:
        """Every detector word in the newest analysed picture → its best box."""
        ...

    def first_seen(self, agent_id: str) -> float | None:
        """When the agent was first on the desk this session."""
        ...

    def last_seen(self, agent_id: str) -> float | None:
        ...

    def person_seen_at(self) -> float | None:
        ...

    def last_motion(self) -> float | None:
        """When things last moved in the picture."""
        ...

    def session_started_at(self) -> float | None:
        """When the camera started watching this session (None before the first picture)."""
        ...

    def set_scan_labels(self, labels: Iterable[str]) -> None:
        """What the last vision scan (lost items) saw; it also tags the proof photos."""
        ...

    def scan_labels(self) -> set[str]:
        ...


@runtime_checkable
class SafetyBoard(Protocol):
    """The dangers on the desk right now (written by the desk monitor), and the latest alerts."""

    def report(self, state: str, dangers: Sequence[Danger], result: Mapping[str, Any] | None, now: float) -> None:
        """The desk monitor's newest findings: SAFE or DANGEROUS, the dangers, and the analyzer's full result.
        A danger counts from the first report it is in until a report without it."""
        ...

    def now(self) -> SafetyNow:
        """The newest report (SafetyNow() before the first)."""
        ...

    def danger_since(self, pair: frozenset[str]) -> float | None:
        """When the danger between these two words started (None if it isn't active)."""
        ...

    def note_alert(self, event: Mapping[str, Any], keep: int) -> None:
        """An alert that went out, for the AI's context; only the newest `keep` are kept."""
        ...

    def recent_alerts(self, limit: int) -> list[dict[str, Any]]:
        """The newest `limit` alerts, oldest first (none when limit is 0)."""
        ...


@runtime_checkable
class CharacterMemory(Protocol):
    """What the characters remember, and what the app told us about you (typing, tracker panels)."""

    def remember(self, agent_id: str, text: str, keep: int) -> None:
        """Something the agent said; only its newest `keep` lines are kept (none when keep is 0)."""
        ...

    def recent(self, agent_id: str) -> list[str]:
        """What the agent said lately, oldest first."""
        ...

    def set_widget(self, agent_id: str, data: Mapping[str, Any]) -> None:
        """The tracker panel data the app sent for an agent (the to-do list ...)."""
        ...

    def widget(self, agent_id: str) -> dict[str, Any] | None:
        ...

    def widgets(self) -> dict[str, dict[str, Any]]:
        """Agent id → its tracker panel data, for every agent the app sent one for."""
        ...

    def note_widget_view(self, agent_id: str, action: str, now: float, keep: int) -> None:
        """You opened (or used) an agent's tracker panel; only the newest `keep` are kept."""
        ...

    def widget_views_of(self, agent_id: str) -> list[dict[str, Any]]:
        """The agent's panel views, oldest first: [{'ts', 'action'}]."""
        ...

    def note_user_activity(self, now: float) -> None:
        """You typed or sent something (small talk waits)."""
        ...

    def user_active_at(self) -> float:
        """When you last typed or sent something (0 before the first time)."""
        ...

    def set_vision_brief(self, text: str) -> None:
        """The last posture check in a few words, for the AI's reminders."""
        ...

    def vision_brief(self) -> str:
        ...


@runtime_checkable
class PhotoAlbum(Protocol):
    """The proof photos, newest first. When it is full, the oldest photo goes."""

    def add(self, jpeg: bytes, label: str, ts: float, *, char_ids: Sequence[str] = (), labels: Sequence[str] = (),
            vlm_labels: Sequence[str] = ()) -> str:
        """Keep a photo with its tags (see domain.Photo). Returns its new id."""
        ...

    def get(self, photo_id: str) -> Photo | None:
        ...

    def newest(self, limit: int) -> list[Photo]:
        """The newest `limit` photos, newest first."""
        ...


# ── What the app's pages ask (the web layer) ──────────────────────────
# desktalk/bootstrap.py → DeskTalk types its fields with these, so a route only sees what it may call.
class _Closed:
    def __repr__(self) -> str:
        return 'CLOSED'


CLOSED = _Closed()      # the last thing in a dropped page's chat queue: compare with `is` (see ChatFeed.subscribe)


@runtime_checkable
class ChatFeed(Protocol):
    """The chat messages for open app pages (the MessageHub): /api/events and /api/messages/recent."""

    def subscribe(self, on_put: Callable[[], None] | None = None) -> queue.Queue:
        """A queue that gets every message from now on (one per open page), ending with CLOSED if the
        page is dropped for not keeping up. on_put: called after each thing put in the queue."""
        ...

    def unsubscribe(self, q: queue.Queue) -> None:
        ...

    def recent(self) -> list[dict[str, Any]]:
        """Recent chat lines and join / leave notes, oldest first."""
        ...


@runtime_checkable
class ChatReplies(Protocol):
    """The chat's answers to you (the message generator): /api/chat and the tracker panels."""

    def answer(self, chat_id: str, message: str, history: Any = None, widget: Any = None) -> dict[str, Any]:
        """The reply to your message in the group chat or a 1-on-1. Raises UnknownChat for an unknown agent."""
        ...

    def save_widget(self, pid: str, raw: Any) -> bool:
        """Keep the to-do list the app sent for an agent's panel. False for an unknown agent or a bad list."""
        ...

    def note_panel_event(self, pid: str, action: Any) -> bool:
        """You opened (or used) an agent's panel. False for an unknown agent."""
        ...

    def live_context(self, user_message: str = '') -> dict[str, Any]:
        """The desk right now as the AI is told it (its 'summary' feeds the small talk)."""
        ...


@runtime_checkable
class RosterList(RosterView, Protocol):
    """Who's in the chat, for the app's agent list (/api/agents, /api/live)."""

    def snapshot(self) -> list[dict[str, Any]]:
        """Agents in the chat now, plus ones that were here earlier, newest first."""
        ...


@runtime_checkable
class CameraTips(Protocol):
    """What Lens's camera checks tell the pages: the problems (/api/live) and a tip for the phone screen."""

    def issues(self) -> list[dict[str, str]]:
        """Current problems, most important first: [{key, title, fix}]."""
        ...

    def phone_tip(self) -> str | None:
        """A few words to show on the phone screen itself, or None."""
        ...

    def forget(self, key: str) -> None:
        """Drop a problem without a thank-you (e.g. the desk was just marked again)."""
        ...


@runtime_checkable
class DetectionStats(PeriodicWorker, Protocol):
    """The camera producer (a background job), and how detection on this computer is doing (/api/health)."""

    @property
    def pictures_analysed(self) -> int:
        ...

    @property
    def last_detect_ms(self) -> float | None:
        """How long the last detection took (None before the first)."""
        ...

    @property
    def problem(self) -> str | None:
        """What's wrong with detection right now, in plain words (None when it works)."""
        ...


@runtime_checkable
class LostItemsView(PeriodicWorker, Protocol):
    """"Have you seen my keys?" (a background job too): /api/chat asks it first, /api/live highlights the items."""

    @property
    def items(self) -> Mapping[str, Mapping[str, Any]]:
        """Item id → its name, detector labels and words (settings → lost_items.items)."""
        ...

    def is_lost_query(self, text: str) -> bool:
        ...

    def answer(self, user_message: str) -> dict[str, Any]:
        """The chat's reply: {'speaker', 'cta', 'text'}."""
        ...


@runtime_checkable
class VisionHabitsView(PeriodicWorker, Protocol):
    """The posture and nail biting checks (a background job too), for the Habits page."""

    def summary(self) -> dict[str, int]:
        """Today's slouching (seconds) and nail biting (times)."""
        ...

    def events(self) -> list[dict[str, Any]]:
        """The habit log, newest first."""
        ...

    def latest(self) -> tuple[dict[str, Any], float]:
        """The AI's last answer and when it came (({}, 0.0) before the first)."""
        ...


@runtime_checkable
class HabitTracking(HabitsView, HabitLog, HabitsLifecycle, Protocol):
    """The habit tracker as the app holds it: today's habits, the habit buttons (log), saving, and the day's
    times (wake, bed, sunset ...) for /api/preferences."""

    def day_times(self, now: float) -> Mapping[str, Any]:
        ...


@runtime_checkable
class ScheduledJobs(PeriodicWorker, Protocol):
    """The timed jobs (compacting and pruning the event log, saving habits), a background job itself."""

    def status(self) -> list[dict[str, Any]]:
        """One entry per job: name, every_sec, runs, last_run_at, last_error, next_run_at, next_in_sec."""
        ...


@runtime_checkable
class Jobs(Protocol):
    """Runs the background jobs, each in its own thread (the Supervisor)."""

    def add(self, worker: PeriodicWorker, start_after: float = 0.0) -> None:
        ...

    def start(self) -> None:
        ...

    def stop(self, timeout: float) -> list[str]:
        """Stop every job, waiting up to `timeout` seconds for each. Returns the names of jobs still busy."""
        ...

    def status(self) -> list[dict[str, Any]]:
        """One entry per job: name, state, failures_in_row, total_failures, last_error, last_ok_at."""
        ...


# ── The developer page (http://localhost:5000/dev/, only from this computer) ──
# Only built when settings → dev.enabled is true. The plain data they return is in desktalk/domain/dev.py.
@runtime_checkable
class TestRunner(Protocol):
    """Runs the checks ("Run everything": settings → dev.checks) or some tests, one run at a time, in a child
    process. In a test file, use it as `ports.TestRunner`: pytest tries to run any class named Test... that a
    test file imports by name."""

    def collect(self) -> list[TestItem]:
        """Every test the list shows (settings → dev.collect_markers); the slow ones are marked 'slow'."""
        ...

    def start_check(self) -> str:
        """Start every step of settings → dev.checks, one after the other. Returns the new run's id.
        Raises RunAlreadyGoing while another run is going."""
        ...

    def start_tests(self, ids: list[str]) -> str:
        """Start the tests (or files) with these ids. Returns the new run's id. Raises RunAlreadyGoing while
        another run is going, and ValueError for an id that the last collect() didn't give."""
        ...

    def status(self, run_id: str, output_from: int) -> RunStatus:
        """How the run is going, with its printout after offset `output_from` (ask from the last output_end
        next time). A run past settings → dev.run_timeout_min is stopped here, as 'timed out'. Raises
        KeyError for a run it doesn't know."""
        ...

    def stop(self, run_id: str) -> None:
        """Stop the run (its state becomes 'stopped'). A finished run, or one it doesn't know, is left alone."""
        ...


@runtime_checkable
class DatabaseBrowser(Protocol):
    """Looks inside the app's SQLite databases, read-only. tables() and query() raise KeyError for a database
    name that databases() doesn't list."""

    def databases(self) -> list[str]:
        """The databases' names, e.g. ['object_states']. One whose file isn't there yet has no tables."""
        ...

    def tables(self, db: str) -> list[TableInfo]:
        ...

    def query(self, db: str, sql: str) -> QueryResult:
        """Run one statement that only reads. Raises QueryRefused for anything else (a write, two statements)
        or one that runs longer than settings → dev.sql_timeout_sec."""
        ...


@runtime_checkable
class DataFiles(Protocol):
    """What's stored in the data folder on this computer, read-only."""

    def list(self) -> list[DataFile]:
        """Every file and folder in it, with paths relative to it."""
        ...

    def preview(self, path: str) -> str:
        """The start of a file (path relative to the data folder), as text. Raises ValueError for a path outside
        the folder or a folder, and FileNotFoundError when nothing is there."""
        ...


@runtime_checkable
class SmoothingTraceView(Protocol):
    """What the object state tracker made of each of the last pictures, per kind of object (settings →
    object_states.trace_pictures), so the developer page can show the smoothing."""

    def traced_labels(self) -> list[str]:
        ...

    def trace(self, label: str) -> list[SmoothingStep]:
        """Oldest first; [] for a label never seen."""
        ...
