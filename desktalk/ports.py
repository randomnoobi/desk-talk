"""
The interfaces (ports) between the parts of Desk Talk.

Every part is written against these small interfaces, never against another part's class.
desktalk/bootstrap.py builds the real parts and connects them; tests build the same parts
with the fakes in tests/fakes/. Each interface has a contract test suite in tests/contracts/
that the real version and the fake both pass.

Change this file only on purpose: every part depends on it.

DeskState (desktalk/desk_state.py, "the desk right now") is still shared directly for now:
parts get it passed in, never through an `app` object.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any, Protocol, runtime_checkable

from desktalk.domain import (
    AgentProfile,
    Box,
    DeskRules,
    Detection,
    Event,
    Frame,
    Gravity,
    Nudge,
    Picture,
    Size,
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
class PhoneControls(Protocol):
    """The phone as the camera: what the phone page's routes tell it (/api/phone/frame, /api/phone/stop)."""

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


@runtime_checkable
class EventStore(Protocol):
    """The event log: one folder per day in data/events/ (see docs/ARCHITECTURE.md)."""

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

    def save(self, force: bool = False) -> None:
        ...



@runtime_checkable
class HabitLog(Protocol):
    """The habit tracker's log(): what a chat action (a sip, a break ...) is written to."""

    def log(self, habit: str, action: str, **details: Any) -> Any:
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
        """Someone seems to be at the desk (a person or movement seen lately)."""
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
