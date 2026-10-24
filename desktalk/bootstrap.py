"""
Builds Desk Talk from its parts. This is the only place that knows the real classes; every other part
gets only the interfaces it asks for (desktalk/ports.py) and its own section of the settings.

    desktalk = build(load_config())     # every part, from config/ and .env
    desktalk.start()                    # every background job, each in its own thread (the Supervisor)
    ...
    desktalk.stop()                     # stop the jobs, finish the events, save the habits, compact the log

    Camera ──► DetectionProducer ──► EventStream ──┬─► EventLog          (data/events/)
    (Pi / phone /                                  ├─► Roster            (agents join / leave)
     webcam)                                       ├─► HabitTracker      (water, breaks, light, phone)
                                                   ├─► DeskMonitor       (danger zones, desk edges, pets)
                                                   ├─► LostItems         (where your keys were)
                                                   └─► MessageGenerator ──► MessageHub ──► open app pages
    Background jobs (Supervisor): the producer, the scheduler (compact and prune the event log, save the
    habits), habit reminders, Lens, small talk, lost items, the daily recap, vision habits, and the Ollama
    keep-alive when the chat runs on Ollama.

Tests build the same app with fakes through build()'s keyword arguments (see tests/integration/app_kit.py).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from desktalk.adapters.camera import PhoneFrameSource, PiFrameSource, WebcamSource, make_pair_key
from desktalk.adapters.detector import UltralyticsDetector
from desktalk.adapters.llm import OllamaKeepAlive, OpenAICompatibleChatModel, ResilientChatModel
from desktalk.adapters.picture import OpenCvPictureTools
from desktalk.adapters.storage import EventLog, JsonFileStore
from desktalk.config import AppConfig, ConfigError
from desktalk.core.agents.profiles import ProfileBook
from desktalk.core.agents.roster import Roster
from desktalk.core.chat.message_generator import MessageGenerator
from desktalk.core.chat.message_hub import MessageHub
from desktalk.core.chat.workers import (
    DailySummary,
    IdleChat,
    Lens,
    LostItems,
    VisionHabits,
)
from desktalk.core.events import EventStream
from desktalk.core.habits.desk_status import DeskStatusService
from desktalk.core.habits.preferences import PreferencesFile
from desktalk.core.habits.reminders import HabitReminders
from desktalk.core.habits.tracker import HabitTracker
from desktalk.core.vision import desk_edges
from desktalk.core.vision.analyzer import DeskAnalyzer
from desktalk.core.vision.camera_health import CameraHealth
from desktalk.core.vision.desk_monitor import DeskMonitor
from desktalk.core.vision.live_view import DeskPainter
from desktalk.core.vision.producer import DetectionProducer
from desktalk.core.vision.proof_photos import ProofPhotos
from desktalk.core.vision.rules import SafetyRules
from desktalk.desk_state import DeskState
from desktalk.ports import Clock, Detector, FrameSource, KeyValueStore, PeriodicWorker, PhoneCamera, PiUploads
from desktalk.runtime import Scheduler, Supervisor, SystemClock, plain_error, setup_logging

log = logging.getLogger(__name__)

MINUTE = 60


@dataclass(eq=False)
class DeskTalk:
    """Every part of the running app, each a named field. What the web layer uses each one for:

    config          everything from config/ and .env: web.port, the list sizes, the pi section for /api/pi/config
    clock           the time, for the times in replies
    desk            DeskState: the live view pictures, what's on the desk, the safety state and the proof photos
                    (/api/live, the video, /api/snapshot/<id>, the captures list)
    bus             the event stream: stats() for /api/health
    event_log       the event log: days(), events(), presence(), recent_detections(), counts() (/api/log/...)
    store           the data folder: preferences, habit days, learned personalities, the daily recap's day
    config_store    the config folder: safety_rules.json
    profiles        every agent: get(), all(), public() for the app's agent list and names
    preferences     wake time, bedtime, quiet hours: all() and update() (/api/preferences)
    source          the camera: kind, connected, fps; uploads go to pi_source / phone_source (below)
    pair_key        what the Pi and the phone must send in X-Desk-Key (and show in the Pi command and phone link)
    safety_rules    the danger rules: for_settings_page(), save(), clean_outline() (/api/rules, desk corners)
    analyzer        the desk checks: latest result (the relationships graph)
    painter         draws the boxes on the live view (the producer uses it)
    camera_health   Lens's camera checks: issues() (/api/live) and phone_tip() (the phone upload reply)
    photos          proof photos: save() (a photo of the desk on request)
    detector        object detection on this computer: label (/api/live)
    producer        camera pictures → events: pictures_analysed, last_detect_ms (yolo_ms), problem (/api/health)
    roster          who's in the chat: snapshot() and member_ids() (/api/agents, /api/live)
    desk_status     camera_connected(), warm(), reminders_ready() ... (/api/live, /api/health)
    habits          today's habits: today(), log(), day_times() (/api/habits, the habit buttons)
    monitor         the desk monitor (safety alerts; nothing for the web to call)
    ai              the AI connection itself: label, is_local, local_vision
    model           the AI as the parts use it (time limits, retries, a breaker): ready, label, problem()
    hub             chat messages for open app pages: subscribe() / unsubscribe() (SSE), recent(), send()
    generator       the chat: answer(), live_context(), save_widget(), note_panel_event() (/api/chat ...)
    reminders       habit reminders (a background job)
    lens            the camera's own character (a background job)
    idle_chat       small talk between agents (a background job)
    lost_items      "have you seen my keys?": is_lost_query() and answer() (/api/chat)
    vision_habits   posture and nail biting: summary(), events(), latest() (/api/habits)
    daily_summary   the end-of-day recap (a background job)
    scheduler       timed jobs (compacting and pruning the event log, saving habits): status() (/api/health)
    supervisor      runs the background jobs: status() (/api/health)
    started_at      when the app was built (how long it has been running)
    keepalive       keeps Ollama's model loaded (None for a cloud AI)
    log_file        the log file (None when build() didn't set up logging)

    pi_source / phone_source are the source when it's the Pi / the phone (None otherwise): push() for
    /api/pi/frame and /api/phone/frame, status() and pi_info() for /api/health and /api/live,
    set_motion() and mark_stopped() for the phone.
    """

    config: AppConfig
    clock: Clock
    desk: DeskState
    bus: EventStream
    event_log: EventLog
    store: KeyValueStore
    config_store: KeyValueStore
    profiles: ProfileBook
    preferences: PreferencesFile
    source: FrameSource
    pair_key: str
    safety_rules: SafetyRules
    analyzer: DeskAnalyzer
    painter: DeskPainter
    camera_health: CameraHealth
    photos: ProofPhotos
    detector: Detector
    producer: DetectionProducer
    roster: Roster
    desk_status: DeskStatusService
    habits: HabitTracker
    monitor: DeskMonitor
    ai: OpenAICompatibleChatModel
    model: ResilientChatModel
    hub: MessageHub
    generator: MessageGenerator
    reminders: HabitReminders
    lens: Lens
    idle_chat: IdleChat
    lost_items: LostItems
    vision_habits: VisionHabits
    daily_summary: DailySummary
    scheduler: Scheduler
    supervisor: Supervisor
    started_at: float
    keepalive: OllamaKeepAlive | None = None
    log_file: Path | None = None

    @property
    def source_kind(self) -> str:
        """pi, phone or webcam."""
        return self.source.kind

    @property
    def pi_source(self) -> PiFrameSource | None:
        return self.source if isinstance(self.source, PiFrameSource) else None

    @property
    def phone_source(self) -> PhoneFrameSource | None:
        return self.source if isinstance(self.source, PhoneFrameSource) else None

    def background_jobs(self) -> list[tuple[PeriodicWorker, float]]:
        """Every background job, with how long it waits before its first round."""
        c = self.config
        jobs: list[tuple[PeriodicWorker, float]] = [
            (self.producer, 0.0),
            (self.scheduler, 0.0),
            (self.reminders, c.reminders.check_every_sec),
            (self.idle_chat, c.idle_chat.start_delay_sec),
            (self.lost_items, c.lost_items.start_delay_sec),
            (self.lens, c.lens.check_every_sec),
            (self.daily_summary, c.daily_summary.start_delay_sec),
            (self.vision_habits, c.vision_habits.start_delay_sec),
        ]
        if self.keepalive is not None:
            jobs.append((self.keepalive, c.llm.ollama_keepalive_start_sec))
        return jobs

    def start(self) -> None:
        """Run every background job, each in its own thread. Call once."""
        for job, start_after in self.background_jobs():
            self.supervisor.add(job, start_after=start_after)
        self.supervisor.start()
        log.info('Desk Talk started: %s', ', '.join(job.name for job, _ in self.background_jobs()))

    def stop(self, timeout: float | None = None) -> list[str]:
        """Stop everything, in this order: the background jobs; the events still waiting are handled; today's
        habits are saved; the event log is compacted; the event stream is closed (and a webcam let go).
        A step that fails is logged and the rest still happen. `timeout`: how long to wait for each job
        (settings → supervisor.stop_wait_sec when not given). Returns the names of jobs and event consumers
        still busy after their wait (their threads don't keep the app from closing)."""
        wait = self.config.supervisor.stop_wait_sec if timeout is None else timeout
        still_running = self.supervisor.stop(wait)
        if not self.bus.wait_idle(wait):
            log.warning('Some events were still waiting when Desk Talk stopped')
        self._shutdown_step('save the habits', lambda: self.habits.save(force=True))
        self._shutdown_step('compact the event log', lambda: self.event_log.compact(self.clock.now()))
        still_running += self.bus.close(wait)
        if isinstance(self.source, WebcamSource):
            self.source.release()
        if still_running:
            log.warning('Still busy when Desk Talk stopped: %s', ', '.join(still_running))
        return still_running

    @staticmethod
    def _shutdown_step(what: str, step: Callable[[], object]) -> None:
        try:
            step()
        except Exception as exc:
            log.error('Could not %s while stopping: %s', what, plain_error(exc), exc_info=True)


# ════════════════════════════════════════════════════════════════════
#  Building it
# ════════════════════════════════════════════════════════════════════
def make_source(config: AppConfig, clock: Clock) -> FrameSource:
    """The camera named in settings → camera.source."""
    camera = config.camera
    if camera.source == 'phone':
        return PhoneFrameSource(clock, camera)
    if camera.source == 'webcam':
        if not camera.webcam_indices:
            raise ConfigError('config/settings.yaml: camera.webcam_indices needs at least one webcam number')
        return WebcamSource(clock, camera, camera.webcam_indices[0])
    return PiFrameSource(clock, camera)


def make_detector(config: AppConfig) -> UltralyticsDetector:
    """Object detection on this computer (settings → detector), warmed up so the first picture isn't slow.
    A model that won't load leaves the Pi's own detections working."""
    detector = UltralyticsDetector(config.resolve(config.detector.model), config.detector,
                                   end2end=config.detector.end2end, rect=config.detector.rect)
    detector.warmup()
    return detector


def build(config: AppConfig, *, clock: Clock | None = None, detector: Detector | None = None,
          client_factory: Callable[..., Any] | None = None, source: FrameSource | None = None,
          data_folder: Path | None = None, config_store: KeyValueStore | None = None, pair_key: str | None = None,
          synchronous_events: bool = False, set_up_logging: bool = True) -> DeskTalk:
    """Every part of Desk Talk, connected. Nothing runs until start().

    The keyword arguments are for tests; left out, the real things are made from `config`:
        clock               the computer's clock
        detector            UltralyticsDetector (settings → detector), warmed up
        client_factory      makes the OpenAI-compatible client (openai.OpenAI)
        source              the camera in settings → camera.source
        data_folder         where the files this run writes go (the store, the event log, the log files):
                            the folders in the settings are taken under it instead of the project folder
        config_store        the store for config/safety_rules.json (a JsonFileStore of the config folder)
        pair_key            the key in settings → camera.pair_key_file (made and saved the first time)
        synchronous_events  the event stream hands each event to its consumers right inside publish(),
                            with no threads, so tests are exact
        set_up_logging      log to the terminal and to the log folder (settings → logging)"""
    clock = clock or SystemClock()

    def data_path(path: Path) -> Path:
        path = Path(path).expanduser()
        return config.resolve(path) if data_folder is None or path.is_absolute() else Path(data_folder) / path

    log_file = None
    if set_up_logging:
        logs = config.logging
        log_file = setup_logging(data_path(logs.folder), logs.level, logs.file_size_mb, logs.files_kept)

    # ── storage and shared state ──
    store = JsonFileStore(data_path(config.storage.folder))
    config_store = config_store if config_store is not None else JsonFileStore(config.config_folder)
    desk = DeskState(max_snapshots=config.snapshots.max_kept)
    bus = EventStream(config.event_stream, synchronous=synchronous_events)
    event_log = EventLog(data_path(config.event_log.folder), config.event_log)
    bus.subscribe('event-log', event_log.write)          # first, so it logs events in the order they happen
    profiles = ProfileBook(config.agents_file, store, personality_max_chars=config.agents.personality_max_chars)
    preferences = PreferencesFile(store, config.protocols.schedule, config.reminders.quiet_hours)

    # ── the camera, the desk rules and detection ──
    source = source if source is not None else make_source(config, clock)
    if pair_key is None:
        pair_key = make_pair_key(config.resolve(config.camera.pair_key_file), config.camera.pair_key_bytes)
    safety_rules = SafetyRules(config_store, config.safety, liquids=profiles.labels_for_habit('hydration'),
                               foods=profiles.labels_for_habit('food'))
    analyzer = DeskAnalyzer(safety_rules)
    painter = DeskPainter(desk, profiles, analyzer)
    photos = ProofPhotos(desk, clock, config.snapshots)
    camera_health = CameraHealth(config.lens, source_kind=source.kind, moved_within_sec=config.camera.moved_within_sec,
                                 ignore_labels=config.agents_file.ignore)
    detector = detector if detector is not None else make_detector(config)
    producer = DetectionProducer(source, detector, bus, desk, clock, camera_health, painter, config.camera,
                                 new_session_after_sec=config.protocols.breaks.break_min * MINUTE)

    # ── the consumers of detection events (each subscribes itself, in this order) ──
    def on_desk(x: float, y: float, width: int, height: int) -> bool:
        return desk_edges.on_desk((x, y), safety_rules.current.outline, (width, height))

    phone_is_camera = source.kind == 'phone'
    roster = Roster(bus, profiles, desk, config.agents, config.presence, on_desk=on_desk)
    desk_status = DeskStatusService(desk, source, preferences, config.reminders)
    habits = HabitTracker(clock, bus, store, profiles, preferences, desk, config.protocols, config.habit_tracker,
                          config.presence, config.agents, phone_counts=not phone_is_camera)
    monitor = DeskMonitor(bus, desk, analyzer, profiles, habits, desk_status, photos, config.alerts)

    # ── the AI and the chat ──
    ai = OpenAICompatibleChatModel(config.llm, config.secrets, clock, client_factory=client_factory,
                                   replies=config.prompts.replies)
    model = ResilientChatModel(ai, clock, config.llm, time_limit_sec=ai.time_limit_sec)
    vision = model if model.local_vision else None       # pictures only go to an AI on this computer (no quota)
    hub = MessageHub(clock, recent_kept=config.chat.recent_kept, queue_size=config.web.live_queue_size)
    default_temperature = config.agents_file.default.temperature
    lost_items = LostItems(clock, bus, desk, roster, profiles, habits, model, vision, config.lost_items,
                           config.llm.replies, photos=photos, default_temperature=default_temperature,
                           pictures=OpenCvPictureTools(), prompts=config.prompts.lost_items,
                           photos_kept=config.snapshots.max_kept, phone_is_camera=phone_is_camera)
    generator = MessageGenerator(model, hub, roster, profiles, habits, desk, clock, status=desk_status,
                                 habit_log=habits, llm=config.llm, chat=config.chat, agents=config.agents,
                                 sunlight_default_min=config.habit_tracker.sunlight_default_min,
                                 reply_max_chars=config.chat.reply_max_chars, prompts=config.prompts,
                                 drink_labels=config.safety.liquids, preview_chars=config.logging.preview_chars,
                                 local_model=ai.is_local)
    generator.subscribe_to(bus)

    # ── the background jobs ──
    reminders = HabitReminders(habits, desk_status, roster, profiles, generator, hub, bus, config.reminders)
    lens = Lens(camera_health, source, safety_rules, preferences, hub, bus, photos, config.lens, config.pi,
                prompts=config.prompts.lens, preview_chars=config.logging.preview_chars,
                phone=source if isinstance(source, PhoneCamera) else None,
                pi_uploads=source if isinstance(source, PiUploads) else None)
    idle_chat = IdleChat(model, hub, desk_status, roster, profiles, desk, lambda: generator.live_context()['summary'],
                         config.idle_chat, config.llm.replies, memory_kept=config.llm.memory_per_agent,
                         memory_chars=config.chat.memory_reply_chars, default_temperature=default_temperature,
                         reply_max_chars=config.chat.reply_max_chars, prompts=config.prompts.small_talk,
                         preview_chars=config.logging.preview_chars)
    vision_habits = VisionHabits(vision, bus, desk, roster, profiles, desk_status, config.vision_habits, clock, photos,
                                 prompts=config.prompts.vision)
    daily_summary = DailySummary(model, hub, roster, habits, store, config.daily_summary, config.llm.replies,
                                 default_temperature=default_temperature, clock=clock,
                                 prompts=config.prompts.daily_recap, preview_chars=config.logging.preview_chars)
    scheduler = Scheduler(clock, longest_wait_sec=config.supervisor.scheduler_longest_wait_sec)
    scheduler.every(config.event_log.compact_every_min * MINUTE, lambda: event_log.compact(clock.now()),
                    'compact event log')
    scheduler.every(config.event_log.prune_every_min * MINUTE, lambda: event_log.prune(clock.now()),
                    'delete old event-log days')
    scheduler.every(config.habit_tracker.save_check_every_sec, habits.save, 'save habit day')
    supervisor = Supervisor(clock, first_retry_sec=config.supervisor.restart_start_sec,
                            max_retry_sec=config.supervisor.restart_max_sec)

    return DeskTalk(
        config=config, clock=clock, desk=desk, bus=bus, event_log=event_log, store=store, config_store=config_store,
        profiles=profiles, preferences=preferences, source=source, pair_key=pair_key, safety_rules=safety_rules,
        analyzer=analyzer, painter=painter, camera_health=camera_health, photos=photos, detector=detector,
        producer=producer, roster=roster, desk_status=desk_status, habits=habits, monitor=monitor, ai=ai,
        model=model, hub=hub, generator=generator, reminders=reminders, lens=lens, idle_chat=idle_chat,
        lost_items=lost_items, vision_habits=vision_habits, daily_summary=daily_summary, scheduler=scheduler,
        supervisor=supervisor, started_at=clock.now(), keepalive=ai.keepalive_worker(), log_file=log_file)
