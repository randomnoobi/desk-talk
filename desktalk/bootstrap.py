"""
Builds Desk Talk from its parts. This is the only place that knows the real classes; every other part
gets only the interfaces it asks for (desktalk/ports.py) and its own section of the settings.

    desktalk = build(load_config())     # every part, from config/ and .env
    desktalk.start()                    # every background job, each in its own thread (the Supervisor)
    ...
    desktalk.stop()                     # stop the jobs, finish the events, save the states and habits, compact the log

    Camera ──► DetectionProducer ──► EventStream ──┬─► EventLog          (data/events/)
    (Pi / phone /                                  ├─► ObjectStateTracker (present / absent per object, saved in
     webcam)                                       │     SQLite; your own state switches reminders on and off)
                                                   ├─► Roster            (agents join / leave)
                                                   ├─► HabitTracker      (water, breaks, light, phone)
                                                   ├─► DeskMonitor       (danger zones, desk edges, pets)
                                                   ├─► LostItems         (where your keys were)
                                                   └─► MessageGenerator ──► MessageHub ──► open app pages
    Background jobs (Supervisor): the producer, the scheduler (compact and prune the event log, save the
    habits and the object states), the rule engine (the habit rules in protocols.yaml → rules), habit reminders,
    Lens, small talk, lost items, the daily recap, vision habits, and the Ollama keep-alive when the chat runs
    on Ollama.

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
from desktalk.adapters.dev import DataFolder, PytestRunner, SqliteBrowser
from desktalk.adapters.llm import OpenAICompatibleChatModel, ResilientChatModel
from desktalk.adapters.picture import DeskPainter, OpenCvPictureTools
from desktalk.adapters.storage import EventLog, JsonFileStore, SqliteStateStore
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
from desktalk.core.desk import Album, DangerBoard, LiveView, Memories, Scene, WholeDesk
from desktalk.core.events import EventStream
from desktalk.core.habits.desk_status import DeskStatusService
from desktalk.core.habits.preferences import PreferencesFile
from desktalk.core.habits.reminders import HabitReminders
from desktalk.core.habits.rule_runner import RuleRunner
from desktalk.core.habits.tracker import HabitTracker
from desktalk.core.object_states import ObjectStateTracker
from desktalk.core.vision import desk_edges
from desktalk.core.vision.analyzer import DeskAnalyzer
from desktalk.core.vision.camera_health import CameraHealth
from desktalk.core.vision.desk_monitor import DeskMonitor
from desktalk.core.vision.producer import DetectionProducer
from desktalk.core.vision.proof_photos import ProofPhotos
from desktalk.core.vision.rules import SafetyRules
from desktalk.ports import (
    CameraTips,
    CharacterMemory,
    ChatFeed,
    ChatModel,
    ChatReplies,
    Clock,
    DatabaseBrowser,
    DataFiles,
    DeskRulesView,
    DeskScene,
    DeskStatus,
    DetectionStats,
    Detector,
    EventBus,
    EventStore,
    FrameSource,
    HabitTracking,
    Jobs,
    KeyValueStore,
    LivePictures,
    LostItemsView,
    ObjectStateStore,
    PeriodicWorker,
    PhoneControls,
    PhotoAlbum,
    PhotoTaker,
    PictureInbox,
    PiStatus,
    Preferences,
    Profiles,
    RosterList,
    SafetyBoard,
    ScheduledJobs,
    TestRunner,
    TracedObjectStates,
    VisionHabitsView,
)
from desktalk.runtime import Scheduler, Supervisor, SystemClock, plain_error, setup_logging

log = logging.getLogger(__name__)

MINUTE = 60


@dataclass(eq=False)
class DeskTalk:
    """Every part of the running app, each a named field typed with the interface the web layer (and the
    tests) may use it through. What the web layer uses each one for:

    config            everything from config/ and .env: web.port, the list sizes, the pi section for /api/pi/config
    clock             the time, for the times in replies and today's day
    live_pictures     the newest pictures: the video, /api/latest_frame_raw, which camera the live view shows
    scene             what the camera sees: the agents' and other boxes, what the vision scan saw (/api/live)
    safety_board      the dangers right now (/api/live, /api/status)
    character_memory  when you last typed (/api/activity, /api/chat)
    photo_album       the proof photos (/api/snapshot/<id>, /api/snapshots)
    bus               the event stream: stats() for /api/health
    event_log         the event log: days(), events(), presence(), recent_detections(), counts() (/api/log/...)
    store             the data folder: preferences, habit days, learned personalities, the daily recap's day
    config_store      the config folder: safety_rules.json
    profiles          every agent: get(), for_label(), is_ignored() for the app's agent list and the live view
    preferences       wake time, bedtime, quiet hours: all(), update() and quiet_now() (/api/preferences)
    source            the camera: kind, connected, fps
    pi_inbox, pi      the Pi when it is the camera (None otherwise): push() for /api/pi/frame, status() and
                      pi_info() for /api/health and /api/live
    phone_inbox,      the phone when it is the camera (None otherwise): push() for /api/phone/frame, set_motion()
    phone             and mark_stopped(), and its tilt when the desk corners are marked
    pair_key          what the Pi and the phone must send in X-Desk-Key (and show in the Pi command and phone link)
    safety_rules      the danger rules: for_settings_page(), save(), clean_outline() (/api/rules, desk corners)
    camera_health     Lens's camera checks: issues() (/api/live), phone_tip() (the upload replies), forget()
    photos            proof photos: save() (a photo of the desk on request)
    detector          object detection on this computer: label (/api/live)
    producer          camera pictures → events: pictures_analysed, last_detect_ms (yolo_ms), problem (/api/health)
    object_states     present / absent for each object on the desk: snapshot() and changes() (the person is the
                      switch), save_snapshot() (the scheduler and stop()), and each object's last pictures of
                      smoothing: traced_labels() and trace() (/api/dev/smoothing)
    state_store       the SQLite file the object states are saved in (settings → object_states.database)
    roster            who's in the chat: snapshot() and member_ids() (/api/agents, /api/live)
    desk_status       camera_connected(), warm(), reminders_ready() ... (/api/live, /api/health)
    habits            today's habits: today(), log(), day_times() (/api/habits, the habit buttons), save()
    ai                the AI connection itself: label, is_local, local_vision
    model             the AI as the parts use it (time limits, retries, a breaker): ready, label, problem()
    hub               chat messages for open app pages: subscribe() / unsubscribe() (SSE), recent()
    generator         the chat: answer(), save_widget(), note_panel_event() (/api/chat ...)
    rule_runner       the habit rules in protocols.yaml → rules, checked against the object states (a background
                      job); the reminders ask it for nudges
    reminders         habit reminders (a background job)
    lens              the camera's own character (a background job)
    idle_chat         small talk between agents (a background job)
    lost_items        "have you seen my keys?": is_lost_query(), answer() and the items (/api/chat, /api/live)
    vision_habits     posture and nail biting: summary(), events(), latest() (/api/habits)
    daily_summary     the end-of-day recap (a background job)
    scheduler         timed jobs (compacting and pruning the event log, saving habits and object states):
                      status() (/api/health)
    supervisor        runs the background jobs: status() (/api/health)
    started_at        when the app was built (how long it has been running)
    keepalive         keeps Ollama's model loaded (None for a cloud AI)
    log_file          the log file (None when build() didn't set up logging)
    test_runner       the developer page's tests and checks: collect(), start_check(), start_tests(), status(),
                      stop() (/api/dev/tests, /api/dev/runs...); close() at stop()
    databases         the developer page's read-only look inside the databases (/api/dev/db...)
    data_files        what's in the data folder, read-only (/api/dev/files...)
                      (these three are None when settings → dev.enabled is false)
    """

    config: AppConfig
    clock: Clock
    live_pictures: LivePictures
    scene: DeskScene
    safety_board: SafetyBoard
    character_memory: CharacterMemory
    photo_album: PhotoAlbum
    bus: EventBus
    event_log: EventStore
    store: KeyValueStore
    config_store: KeyValueStore
    profiles: Profiles
    preferences: Preferences
    source: FrameSource
    pi_inbox: PictureInbox | None
    pi: PiStatus | None
    phone_inbox: PictureInbox | None
    phone: PhoneControls | None
    pair_key: str
    safety_rules: DeskRulesView
    camera_health: CameraTips
    photos: PhotoTaker
    detector: Detector
    producer: DetectionStats
    object_states: TracedObjectStates
    state_store: ObjectStateStore
    roster: RosterList
    desk_status: DeskStatus
    habits: HabitTracking
    ai: ChatModel
    model: ChatModel
    hub: ChatFeed
    generator: ChatReplies
    rule_runner: PeriodicWorker
    reminders: PeriodicWorker
    lens: PeriodicWorker
    idle_chat: PeriodicWorker
    lost_items: LostItemsView
    vision_habits: VisionHabitsView
    daily_summary: PeriodicWorker
    scheduler: ScheduledJobs
    supervisor: Jobs
    started_at: float
    keepalive: PeriodicWorker | None = None
    log_file: Path | None = None
    test_runner: TestRunner | None = None
    databases: DatabaseBrowser | None = None
    data_files: DataFiles | None = None

    @property
    def source_kind(self) -> str:
        """pi, phone or webcam."""
        return self.source.kind

    def background_jobs(self) -> list[tuple[PeriodicWorker, float]]:
        """Every background job, with how long it waits before its first round."""
        c = self.config
        jobs: list[tuple[PeriodicWorker, float]] = [
            (self.producer, 0.0),
            (self.scheduler, 0.0),
            (self.rule_runner, c.rule_engine.check_every_sec),
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
        """Stop everything, in this order: the background jobs and a developer page test run still going; the
        events still waiting are handled; the object states are saved; today's habits are saved; the event log
        is compacted; the event stream is closed (and a webcam let go); the object states database is closed.
        A step that fails is logged and the rest still happen. `timeout`: how long to wait for each job
        (settings → supervisor.stop_wait_sec when not given). Returns the names of jobs and event consumers
        still busy after their wait (their threads don't keep the app from closing)."""
        wait = self.config.supervisor.stop_wait_sec if timeout is None else timeout
        still_running = self.supervisor.stop(wait)
        if self.test_runner is not None:
            self._shutdown_step('stop the test run', self.test_runner.close)
        if not self.bus.wait_idle(wait):
            log.warning('Some events were still waiting when Desk Talk stopped')
        self._shutdown_step('save the object states', lambda: self.object_states.save_snapshot(self.clock.now()))
        self._shutdown_step('save the habits', lambda: self.habits.save(force=True))
        self._shutdown_step('compact the event log', lambda: self.event_log.compact(self.clock.now()))
        still_running += self.bus.close(wait)
        if isinstance(self.source, WebcamSource):
            self.source.release()
        self._shutdown_step('close the object states database', self.state_store.close)
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
@dataclass(frozen=True)
class Camera:
    """The camera, and what else it is when it's the Pi or the phone (both send their pictures to us)."""

    source: FrameSource
    pi: PiFrameSource | None = None
    phone: PhoneFrameSource | None = None


def make_camera(config: AppConfig, clock: Clock) -> Camera:
    """The camera named in settings → camera.source."""
    camera = config.camera
    if camera.source == 'phone':
        phone = PhoneFrameSource(clock, camera)
        return Camera(phone, phone=phone)
    if camera.source == 'webcam':
        if not camera.webcam_indices:
            raise ConfigError('config/settings.yaml: camera.webcam_indices needs at least one webcam number')
        return Camera(WebcamSource(clock, camera, camera.webcam_indices[0]))
    pi = PiFrameSource(clock, camera)
    return Camera(pi, pi=pi)


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
          synchronous_events: bool = False, set_up_logging: bool = True,
          test_runner: TestRunner | None = None) -> DeskTalk:
    """Every part of Desk Talk, connected. Nothing runs until start().

    The keyword arguments are for tests; left out, the real things are made from `config`:
        clock               the computer's clock
        detector            UltralyticsDetector (settings → detector), warmed up
        client_factory      makes the OpenAI-compatible client (openai.OpenAI)
        source              the camera in settings → camera.source (one passed in is only a camera: not the
                            Pi's or the phone's uploads)
        data_folder         where the files this run writes go (the store, the event log, the object states
                            database, the log files):
                            the folders in the settings are taken under it instead of the project folder
        config_store        the store for config/safety_rules.json (a JsonFileStore of the config folder)
        pair_key            the key in settings → camera.pair_key_file (made and saved the first time)
        synchronous_events  the event stream hands each event to its consumers right inside publish(),
                            with no threads, so tests are exact
        set_up_logging      log to the terminal and to the log folder (settings → logging)
        test_runner         the developer page's test runner (PytestRunner on this project), when settings →
                            dev.enabled is on"""
    clock = clock or SystemClock()

    def data_path(path: Path) -> Path:
        path = Path(path).expanduser()
        return config.resolve(path) if data_folder is None or path.is_absolute() else Path(data_folder) / path

    log_file = None
    if set_up_logging:
        logs = config.logging
        log_file = setup_logging(data_path(logs.folder), logs.level, logs.file_size_mb, logs.files_kept,
                                 logs.file_name)

    # ── storage and the desk right now ──
    store = JsonFileStore(data_path(config.storage.folder))
    config_store = config_store if config_store is not None else JsonFileStore(config.config_folder)
    live_pictures, scene, safety_board = LiveView(), Scene(), DangerBoard()
    character_memory, photo_album = Memories(), Album(keep=config.snapshots.max_kept)
    # For the parts that still ask for several desk interfaces in one object; it passes each call on.
    desk = WholeDesk(live_pictures, scene, safety_board, character_memory, photo_album)
    bus = EventStream(config.event_stream, synchronous=synchronous_events)
    event_log = EventLog(data_path(config.event_log.folder), config.event_log, clock)
    bus.subscribe('event-log', event_log.write)          # first, so it logs events in the order they happen
    profiles = ProfileBook(config.agents_file, store, personality_max_chars=config.agents.personality_max_chars)
    preferences = PreferencesFile(store, config.protocols.schedule, config.reminders.quiet_hours, clock=clock)

    # ── the camera, the desk rules and detection ──
    camera = Camera(source) if source is not None else make_camera(config, clock)
    source = camera.source
    if pair_key is None:
        pair_key = make_pair_key(config.resolve(config.camera.pair_key_file), config.camera.pair_key_bytes)
    safety_rules = SafetyRules(config_store, config.safety, words=config.prompts.desk_rules,
                               liquids=profiles.labels_for_habit('hydration'), foods=profiles.labels_for_habit('food'))
    analyzer = DeskAnalyzer(safety_rules, words=config.prompts.desk_checks)
    painter = DeskPainter(scene, profiles, analyzer, config.drawing)
    pictures = OpenCvPictureTools()
    photos = ProofPhotos(desk, clock, config.snapshots, pictures=pictures, no_label=config.prompts.photos.no_label)
    camera_health = CameraHealth(config.lens, source_kind=source.kind, moved_within_sec=config.camera.moved_within_sec,
                                 pictures=pictures, words=config.prompts.camera,
                                 ignore_labels=config.agents_file.ignore)
    detector = detector if detector is not None else make_detector(config)
    producer = DetectionProducer(source, detector, bus, desk, clock, camera_health, painter, config.camera,
                                 new_session_after_sec=config.protocols.breaks.break_min * MINUTE, pictures=pictures)
    state_store = SqliteStateStore(data_path(config.object_states.database))
    object_states = ObjectStateTracker(config.object_states, bus, state_store, device_id=source.kind,
                                       new_session_after_sec=config.protocols.breaks.break_min * MINUTE, clock=clock)
    object_states.restore(clock.now())

    # ── the consumers of detection events (each subscribes itself, in this order) ──
    def on_desk(x: float, y: float, width: int, height: int) -> bool:
        return desk_edges.on_desk((x, y), safety_rules.current.outline, (width, height))

    phone_is_camera = source.kind == 'phone'
    roster = Roster(bus, profiles, scene, config.agents, config.presence, on_desk=on_desk)
    desk_status = DeskStatusService(desk, source, preferences, config.reminders, object_states,
                                    person_label=config.object_states.person_label, clock=clock)
    habits = HabitTracker(clock, bus, store, profiles, preferences, scene, config.protocols, config.habit_tracker,
                          config.presence, config.agents, phone_counts=not phone_is_camera,
                          words=config.prompts.habits)
    # The desk monitor has nothing the web calls: it subscribes itself, and the event stream keeps it.
    DeskMonitor(bus, desk, analyzer, profiles, habits, desk_status, photos, config.alerts,
                words=config.prompts.desk_alerts)

    # ── the AI and the chat ──
    ai = OpenAICompatibleChatModel(config.llm, config.secrets, clock, client_factory=client_factory,
                                   replies=config.prompts.replies)
    model = ResilientChatModel(ai, clock, config.llm, time_limit_sec=ai.time_limit_sec, replies=config.prompts.replies)
    vision = model if model.local_vision else None       # pictures only go to an AI on this computer (no quota)
    hub = MessageHub(clock, recent_kept=config.chat.recent_kept, queue_size=config.web.live_queue_size)
    default_temperature = config.agents_file.default.temperature
    lost_items = LostItems(clock, bus, desk, roster, profiles, habits, model, vision, config.lost_items,
                           config.llm.replies, photos=photos, default_temperature=default_temperature,
                           pictures=pictures, prompts=config.prompts.lost_items,
                           photos_kept=config.snapshots.max_kept, phone_is_camera=phone_is_camera)
    generator = MessageGenerator(model, hub, roster, profiles, habits, desk, clock, status=desk_status,
                                 habit_log=habits, llm=config.llm, chat=config.chat, agents=config.agents,
                                 sunlight_default_min=config.habit_tracker.sunlight_default_min,
                                 reply_max_chars=config.chat.reply_max_chars, prompts=config.prompts,
                                 drink_labels=config.safety.liquids, preview_chars=config.logging.preview_chars,
                                 local_model=ai.is_local)
    generator.subscribe_to(bus)

    # ── the background jobs ──
    rule_runner = RuleRunner(config.protocols.rules, object_states, bus, config.rule_engine)
    reminders = HabitReminders(habits, rule_runner, desk_status, roster, profiles, generator, hub, bus,
                               config.reminders)
    lens = Lens(camera_health, source, safety_rules, preferences, hub, bus, photos, config.lens, config.pi,
                clock=clock, prompts=config.prompts.lens, preview_chars=config.logging.preview_chars,
                phone=camera.phone, pi_uploads=camera.pi)
    idle_chat = IdleChat(model, hub, desk_status, roster, profiles, character_memory,
                         lambda: generator.live_context()['summary'], config.idle_chat, config.llm.replies,
                         memory_kept=config.llm.memory_per_agent, memory_chars=config.chat.memory_reply_chars,
                         default_temperature=default_temperature, reply_max_chars=config.chat.reply_max_chars,
                         prompts=config.prompts.small_talk, memory_words=config.prompts.memory,
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
    scheduler.every(config.object_states.snapshot_every_sec, lambda: object_states.save_snapshot(clock.now()),
                    'save object states')
    supervisor = Supervisor(clock, first_retry_sec=config.supervisor.restart_start_sec,
                            max_retry_sec=config.supervisor.restart_max_sec)

    # ── the developer page's tools (only when settings → dev.enabled) ──
    dev = config.dev
    databases = data_files = None
    if dev.enabled:
        if test_runner is None:
            test_runner = PytestRunner(dev, config.prompts.dev, config.root, clock,
                                       stop_wait_sec=config.supervisor.stop_wait_sec)
        databases = SqliteBrowser({'object_states': data_path(config.object_states.database)},
                                  max_rows=dev.sql_max_rows, timeout_sec=dev.sql_timeout_sec, clock=clock,
                                  words=config.prompts.dev, check_every_steps=dev.sql_check_every_steps)
        data_files = DataFolder(data_path(config.storage.folder), dev.file_preview_kb * 1024, words=config.prompts.dev)
    else:
        test_runner = None

    return DeskTalk(
        config=config, clock=clock, live_pictures=live_pictures, scene=scene, safety_board=safety_board,
        character_memory=character_memory, photo_album=photo_album, bus=bus, event_log=event_log, store=store,
        config_store=config_store, profiles=profiles, preferences=preferences, source=source,
        pi_inbox=camera.pi, pi=camera.pi, phone_inbox=camera.phone, phone=camera.phone, pair_key=pair_key,
        safety_rules=safety_rules, camera_health=camera_health, photos=photos, detector=detector,
        producer=producer, object_states=object_states, state_store=state_store, roster=roster,
        desk_status=desk_status, habits=habits, ai=ai, model=model, hub=hub, generator=generator,
        rule_runner=rule_runner, reminders=reminders, lens=lens, idle_chat=idle_chat,
        lost_items=lost_items, vision_habits=vision_habits, daily_summary=daily_summary, scheduler=scheduler,
        supervisor=supervisor, started_at=clock.now(), keepalive=ai.keepalive_worker(), log_file=log_file,
        test_runner=test_runner, databases=databases, data_files=data_files)
