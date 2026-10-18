"""
Builds Desk Talk and starts it (python server.py runs main()).

    Camera source ──► DetectionProducer ──► EventStream ──┬─► EventLog (data/events/, on this computer)
    (Pi / phone /       (YOLO, 3 pics/sec)                 ├─► Roster        (agents join / leave)
     webcam)                                               ├─► DeskMonitor   (danger zones, edges, pets)
                                                           ├─► HabitTracker  (water, breaks, light, phone)
                                                           ├─► LostItems     (where your keys were)
                                                           └─► MessageGenerator (chat messages)
    Scheduler: every 10 min, compact the event log.
    Threads: reminders, Lens, idle chat, lost-item scans, vision habits, daily recap.

DeskTalkApp holds every part, so the web routes and tests can reach them.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from . import paths
from .agents.profiles import ProfileBook
from .agents.roster import Roster
from .chat.generator import MessageGenerator
from .chat.llm import LLMClient
from .chat.lost_items import LostItems
from .events import stream as ev
from .events.log import EventLog
from .events.stream import EventStream
from .habits.reminders import quiet_hours_now
from .habits.tracker import HabitTracker
from .scheduler import Scheduler
from .settings import Preferences, load_settings
from .state import DeskState
from .vision import camera_health as ch
from .vision import relationships
from .vision.desk_monitor import DeskMonitor
from .vision.pairing import make_pair_key
from .vision.sources import PhoneFrameSource, PiFrameSource, WebcamSource


class DeskTalkApp:
    def __init__(self, settings=None, detector=None, source=None, synchronous: bool = False,
                 data_dir: Path | None = None, rules_path: Path | None = None, llm_client_factory=None,
                 pair_key: str | None = None):
        self.settings = settings or load_settings()
        s = self.settings
        self.data_dir = Path(data_dir) if data_dir else paths.DATA_DIR
        self.rules_path = Path(rules_path) if rules_path else paths.RULES_FILE
        self.prefs = Preferences(s, self.data_dir / 'preferences.json')
        self.desk = DeskState(max_snapshots=int(s.snapshots.get('max_kept', 150)))
        self.stream = EventStream(synchronous=synchronous)
        self.event_log = EventLog(paths.project_path(s.event_log.folder) if not data_dir else self.data_dir / 'events',
                                  s.event_log.get('merge_gap_sec', 3), s.event_log.get('keep_days', 30),
                                  s.event_log.get('log_empty_frames', True))
        self.profiles = ProfileBook(s.agents_file, self.data_dir / 'learned_personalities.json')

        # Camera
        cam = s.camera
        self.source_kind = str(cam.get('source', 'pi')).lower()
        timeout = float(cam.get('disconnect_after_sec', 3))
        if source is not None:
            self.source = source
        elif self.source_kind == 'phone':
            self.source = PhoneFrameSource(timeout)
        elif self.source_kind == 'webcam':
            self.source = WebcamSource((cam.get('webcam_indices') or [0])[0])
        else:
            self.source_kind = 'pi'
            self.source = PiFrameSource(timeout)
        self.phone_is_camera = getattr(self.source, 'kind', '') == 'phone'
        self.pair_key = pair_key or make_pair_key(str(paths.PAIR_KEY_FILE))
        self.camera_reference_gravity = None

        # Desk geometry + Lens
        relationships.set_categories(self.profiles.labels_for_habit('hydration'),
                                     self.profiles.labels_for_habit('food'), {'scissors', 'knife'})
        self.rules_loaded = self.load_rules()
        ch.configure(s.get('lens'))
        self.camera_health = ch.CameraHealth(ignore_classes=self.profiles.ignore,
                                             source_kind=getattr(self.source, 'kind', 'phone'))
        self.analyzer = relationships.RelationshipAnalyzer()

        # Consumers
        self.llm = LLMClient(s.llm, s.secrets, client_factory=llm_client_factory)
        self.roster = Roster(self.profiles, self.stream, self.desk, s.agents, s.get('presence'))
        self.habits = HabitTracker(s, self.profiles, self.stream, self.desk, self.prefs,
                                   folder=self.data_dir / 'habits', phone_counts=not self.phone_is_camera)
        self.monitor = DeskMonitor(self)
        self.lost_items = LostItems(self)
        self.generator = MessageGenerator(self)
        self._subscribe()

        self.detector = detector
        self.producer = None
        self.scheduler = Scheduler()
        self.scheduler.every(float(s.event_log.get('compact_every_min', 10)) * 60, self.event_log.compact,
                             'compact event log')
        self.scheduler.every(3600, self.event_log.prune, 'delete old event-log days')
        self.scheduler.every(60, self.habits.save, 'save habit day')
        self.started_at = time.time()

    def _subscribe(self):
        st = self.stream
        st.subscribe('event-log', self.event_log.write)
        st.subscribe('roster', self.roster.on_detection, types={ev.DETECTION})
        st.subscribe('desk-monitor', self.monitor.on_detection, types={ev.DETECTION})
        st.subscribe('habits', self.habits.on_detection, types={ev.DETECTION})
        st.subscribe('lost-items', self.lost_items.on_detection, types={ev.DETECTION})
        st.subscribe('chat:roster', self.generator.on_roster, types={ev.AGENT_JOINED, ev.AGENT_LEFT})
        st.subscribe('chat:alerts', self.generator.on_alert, types={ev.ALERT})
        st.subscribe('chat:habits', self.generator.on_habit, types={ev.HABIT})

    # ── safety_rules.json (danger zones + desk corners, saved by the Settings page) ──
    def load_rules(self) -> bool:
        if not self.rules_path.is_file():
            print('  [rules] No safety_rules.json found — using built-in defaults')
            return False
        relationships.load_config(str(self.rules_path))
        with open(self.rules_path, encoding='utf-8') as f:
            data = json.load(f)
        g = (data.get('camera_reference') or {}).get('gravity')
        self.camera_reference_gravity = tuple(g) if g and len(g) == 3 and data.get('desk_outline') else None
        return True

    def save_rules(self, changes: dict) -> None:
        try:
            existing = json.loads(self.rules_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            existing = {}
        existing.update(changes)
        self.rules_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.rules_path.with_suffix('.tmp')
        tmp.write_text(json.dumps(existing, indent=4), encoding='utf-8')
        os.replace(tmp, self.rules_path)
        self.rules_loaded = self.load_rules()

    # ── questions the threads ask ──
    def camera_connected(self) -> bool:
        return bool(getattr(self.source, 'connected', False))

    def warm(self, now: float) -> bool:
        """The camera has watched long enough for nudges."""
        with self.desk.lock:
            start = self.desk.session_start
        return bool(start) and now - start >= float(self.settings.reminders.get('warmup_min', 15)) * 60

    def reminders_ready(self, now: float) -> bool:
        return self.warm(now) and self.camera_connected() and not quiet_hours_now(self.prefs, now)

    def user_active(self, now: float, window: float = 600) -> bool:
        """Someone seems to be at the desk: a person or movement was seen in the last 10 minutes."""
        with self.desk.lock:
            seen = [self.desk.person_last_seen, self.desk.last_motion_ts]
        return any(t and now - t < window for t in seen)

    def open_todos(self) -> list[str]:
        with self.desk.lock:
            todos = []
            for w in self.desk.char_widgets.values():
                if isinstance(w, dict) and w.get('type') == 'todo':
                    todos += [t.get('text', '') for t in w.get('items', []) if not t.get('done')]
        return todos

    # ── starting everything ──
    def start_detection(self):
        from .vision.detector import Detector
        from .vision.producer import DetectionProducer
        if self.detector is None:
            self.detector = Detector.from_settings(self.settings.detector)
            self.detector.warmup()
        self.producer = DetectionProducer(
            self.source, self.detector, self.stream, self.desk, self.camera_health, self.analyzer, self.profiles,
            fps=float(self.settings.camera.get('fps', 3)),
            break_after_sec=float((self.settings.protocols.get('breaks') or {}).get('break_min', 5)) * 60)
        self.producer.start()

    def start_background(self):
        from .chat.daily_summary import DailySummaryThread
        from .chat.habit_vlm import HabitSceneVlmThread
        from .chat.idle_chat import IdleChatThread
        from .chat.lens import LensThread
        from .habits.reminders import HabitReminderThread
        self.scheduler.start()
        self.lost_items.start()
        self.llm.start_ollama_keepalive()
        self.reminders = HabitReminderThread(self)
        self.lens = LensThread(self)
        self.idle_chat = IdleChatThread(self)
        self.vision_habits = HabitSceneVlmThread(self)
        self.daily_summary = DailySummaryThread(self)
        for t in (self.reminders, self.lens, self.idle_chat, self.vision_habits, self.daily_summary):
            t.start()

    def shutdown(self):
        try:
            self.habits.save(force=True)
            self.event_log.compact()
        except Exception as exc:
            print(f'  [shutdown] {exc!r}')


def main():
    from .web.routes import create_web_app
    from .web.phone import start_phone_server
    from .vision.pairing import lan_ip, print_qr

    app = DeskTalkApp()
    s = app.settings
    port = int(s.web.get('port', 5000))
    print()
    print('═' * 60)
    print('  Desk Talk')
    print('═' * 60)
    print(f'  Camera       : {app.source_kind}  ({s.camera.fps} pictures a second)')
    print(f'  AI           : {app.llm.label}')
    print(f'  Danger zones : {"config/safety_rules.json" if app.rules_loaded else "built-in"}')
    print(f'  Event log    : {app.event_log.folder}  (compacted every {s.event_log.compact_every_min} min)')
    if s.get('_env_applied'):
        print(f'  From .env    : {", ".join(s._env_applied)}')
    if s.get('_env_moved'):
        print(f'  Note         : {", ".join(s._env_moved)} in .env are no longer used; '
              f'they live in config/settings.yaml now.')
    print('  Loading YOLO model (the first run downloads it)...')
    app.start_detection()
    print(f'  YOLO model   : {app.detector.label}')
    app.start_background()
    web = create_web_app(app)
    ip = lan_ip()
    if app.source_kind == 'pi':
        print('\n  📷 Raspberry Pi camera: on the Pi, run')
        print(f'     python3 pi/camera_node.py --server http://{ip}:{port} --key {app.pair_key}\n')
    elif app.source_kind == 'phone':
        url = start_phone_server(app, ip)
        if url:
            print('  📱 Phone camera — scan this with your phone (same Wi-Fi), or open:')
            print(f'     {url}\n')
            print_qr(url)
            print('  The phone will warn the connection is "not private" (it\'s a local certificate).')
            print('  Tap Show Details → visit this website, then tap Start camera.\n')
    print(f'  → Open http://localhost:{port} on this computer\n')
    try:
        from .web.quiet import QuietHandler
        web.run(host='0.0.0.0', port=port, threaded=True, request_handler=QuietHandler)
    finally:
        app.shutdown()
