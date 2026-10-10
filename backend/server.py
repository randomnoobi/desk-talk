"""
Desk Talk — Backend Server
4-character desk ecosystem with relationship-driven interactions.
Now integrated with the safety_rules.json pipeline for per-pair
pixel-based distance thresholds (chart flow).

Usage (from repo root):  python server.py
   or:  python backend/server.py
Then open http://localhost:5000
"""

import os, sys, json, time, math, uuid, random, threading, queue, base64, re
from collections import OrderedDict
from typing import Any, Optional

# Project layout: backend/server.py → repo root is parent of backend/
_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(_BACKEND_DIR)
# root server.py uses runpy.run_path — that does not put backend/ on sys.path, so plain
# `import relationships` fails unless we add this directory explicitly.
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)
APP_STATIC = os.path.join(PROJECT_ROOT, "app")
RULES_PATH = os.path.join(PROJECT_ROOT, "config", "safety_rules.json")

import cv2
import numpy as np
from ultralytics import YOLO
from flask import Flask, Response, jsonify, request, send_from_directory, stream_with_context
from flask_cors import CORS

try:
    from openai import OpenAI
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    _env_path = os.path.join(PROJECT_ROOT, ".env")
    if os.path.isfile(_env_path):
        with open(_env_path) as _f:
            for _line in _f:
                _line = _line.strip()
                if not _line or _line.startswith('#'):
                    continue
                if '=' in _line:
                    _k, _v = _line.split('=', 1)
                    _k, _v = _k.strip(), _v.strip()
                    if _k and not os.environ.get(_k):
                        os.environ[_k] = _v

# ═══════════════════════════════════════════════════════════════════
#  Config
# ═══════════════════════════════════════════════════════════════════
OPENAI_API_KEY   = os.getenv('OPENAI_API_KEY', '')
GEMINI_API_KEY   = os.getenv('GEMINI_API_KEY', '')
GROQ_API_KEY     = os.getenv('GROQ_API_KEY', '')
LLM_MODEL        = os.getenv('LLM_MODEL', 'gpt-4o-mini')
GEMINI_MODEL     = os.getenv('GEMINI_MODEL', 'gemini-2.5-flash')
GROQ_MODEL       = os.getenv('GROQ_MODEL', 'llama-3.3-70b-versatile')
OLLAMA_URL       = os.getenv('OLLAMA_URL', '')
OLLAMA_MODEL     = os.getenv('OLLAMA_MODEL', 'llama3.2')


def _camera_indices_from_env():
    """Match pipeline/main.py: PIPELINE_CAMERA if set; else CAMERA_INDICES; else [0]."""
    raw = os.environ.get('CAMERA_INDICES')
    if raw is not None:
        parts = [p.strip() for p in raw.split(',') if p.strip() != '']
        return [int(p) for p in parts] if parts else [0]
    pc = os.environ.get('PIPELINE_CAMERA')
    if pc is not None and str(pc).strip() != '':
        return [int(str(pc).strip())]
    return [0]


CAMERA_INDICES = _camera_indices_from_env()

# Phone camera mode (default): the phone streams the desk over Wi-Fi instead of a USB webcam.
# CAMERA_SOURCE=webcam switches back to the old OpenCV webcam behavior.
CAMERA_SOURCE    = os.getenv('CAMERA_SOURCE', 'phone').strip().lower()
PHONE_MODE       = CAMERA_SOURCE == 'phone'
PHONE_PORT       = int(os.getenv('PHONE_PORT', '5443'))   # HTTPS port the phone connects to
PHONE_CAM_IDX    = 0                                       # the phone shows up as camera 0
if PHONE_MODE:
    CAMERA_INDICES = [PHONE_CAM_IDX]
YOLO_MODEL      = os.getenv('YOLO_MODEL', 'weights/yolo26n.pt')  # Ultralytics YOLO26 nano; auto-downloads if needed
CONFIDENCE       = float(os.getenv('CONFIDENCE', '0.45'))
# Cup/bottle/wine false positives are common; require higher conf to map → Cup.
GLUG_MIN_CONF    = float(os.getenv('GLUG_MIN_CONF', '0.55'))
EVENT_COOLDOWN   = int(os.getenv('EVENT_COOLDOWN', '25'))
APPEAR_FRAMES    = int(os.getenv('APPEAR_FRAMES', '10'))
DISAPPEAR_FRAMES = int(os.getenv('DISAPPEAR_FRAMES', '40'))
MAX_SNAPSHOTS    = 150
MIN_LLM_INTERVAL = float(os.getenv('MIN_LLM_INTERVAL', '6'))
LLM_MAX_RETRIES  = 3

# Lost object framework: timed loop + scene/heartbeat gate + YOLO then VLM fallback
LOST_LOOP_SEC         = int(os.getenv('LOST_LOOP_SEC', '60'))
LOST_SCENE_DIFF_THR   = float(os.getenv('LOST_SCENE_DIFF_THR', '8.0'))  # mean abs diff 0-255, small 64x64 grays
LOST_HEARTBEAT_SEC    = int(os.getenv('LOST_HEARTBEAT_SEC', '300'))    # rescan if scene looks static
LOST_VISION_MODEL     = os.getenv('LOST_VISION_MODEL', os.getenv('OLLAMA_MODEL', 'llava'))
HABIT_MIN_GAP_SEC     = int(os.getenv('HABIT_MIN_GAP_SEC', '120'))     # min seconds between any habit nudges
HABIT_DND_ENABLED     = os.getenv('HABIT_DND_ENABLED', '0').lower() in ('1', 'true', 'yes')
HABIT_DND_START_HOUR  = int(os.getenv('HABIT_DND_START_HOUR', '22'))   # inclusive, local time
HABIT_DND_END_HOUR    = int(os.getenv('HABIT_DND_END_HOUR', '7'))      # exclusive end of DND in morning
HABIT_VISION_ENRICH_SEC = int(os.getenv('HABIT_VISION_ENRICH_SEC', '300'))  # optional VLM touch for habit state
HABIT_VLM_INTERVAL_SEC = int(os.getenv('HABIT_VLM_INTERVAL_SEC', '42'))  # desk scene VLM for posture / phone / distance
DESK_SCAN_SNAPSHOT_MIN_SEC = int(os.getenv('DESK_SCAN_SNAPSHOT_MIN_SEC', '120'))  # min gap between auto “Desk scan” saves
HABIT_LOG_MAX_EVENTS = int(os.getenv('HABIT_LOG_MAX_EVENTS', '120'))
HABIT_POSTURE_SEGMENT_MIN_SEC = int(os.getenv('HABIT_POSTURE_SEGMENT_MIN_SEC', '90'))  # min slouch span to log when posture returns to good
HABIT_NAIL_EVENT_COOLDOWN_SEC = int(os.getenv('HABIT_NAIL_EVENT_COOLDOWN_SEC', '50'))

# ═══════════════════════════════════════════════════════════════════
#  Characters (4 desk objects in chat UI; YOLO may detect other classes
#  but they are not assigned a speaking character — avoids one-ID-per-class conflicts.)
# ═══════════════════════════════════════════════════════════════════
CHARACTERS = {
    'monty': {
        'name': 'Computer', 'icon': '💻', 'object': 'Computer',
        'personality': (
            'Fragile and anxious, the emotional center of the desk. '
            'Highly self-aware, monitors surroundings constantly. '
            'Expresses discomfort through subtle physical metaphors '
            '(cold, pressure, tightness). Does not panic loudly.'
        ),
        'yolo_classes': ['laptop', 'tv', 'keyboard', 'mouse'],
        'temperature': 0.60,
    },
    'glug': {
        'name': 'Cup', 'icon': '☕', 'object': 'Cup',
        'personality': (
            'Passive, innocent, completely unaware of its own danger. '
            'Believes it is harmless and defends itself by insisting it is stationary. '
            'Does not feel guilt, only confusion when others panic.'
        ),
        'yolo_classes': ['bottle', 'cup', 'wine glass'],
        'temperature': 0.75,
    },
    'munch': {
        'name': 'Snack', 'icon': '🍕', 'object': 'Snack',
        'personality': (
            'Hedonistic and unapologetic. Prioritizes enjoyment over safety. '
            'Dismisses concerns raised by others. Does not feel responsible '
            'for consequences. Carefree and defensive.'
        ),
        'yolo_classes': [
            'banana', 'apple', 'sandwich', 'orange', 'broccoli',
            'carrot', 'hot dog', 'pizza', 'donut', 'cake',
        ],
        'temperature': 0.90,
    },
    'sheets': {
        'name': 'Paper', 'icon': '📄', 'object': 'Paper',
        'personality': (
            'Gentle and anxious, quietly warns others. '
            'Represents value (work, deadlines, effort). Extremely vulnerable. '
            'Loves being near other papers. Gets lonely and bored alone.'
        ),
        'yolo_classes': ['book'],
        'temperature': 0.50,
    },
}

# Per-character rolling memory: keeps last N things each character said + key facts.
CHAR_MEMORY_MAX = 8

YOLO_TO_CHAR = {}
for _cid, _cdata in CHARACTERS.items():
    for _cls in _cdata['yolo_classes']:
        YOLO_TO_CHAR[_cls] = _cid

# Reverse: character ID → list of YOLO classes
CHAR_TO_YOLO = {cid: cdata['yolo_classes'] for cid, cdata in CHARACTERS.items()}

# All YOLO/COCO classes relevant for a desk environment
AVAILABLE_YOLO_OBJECTS = [
    'cell phone', 'laptop', 'keyboard', 'mouse', 'remote', 'book',
    'bottle', 'cup', 'wine glass', 'fork', 'knife', 'spoon', 'bowl',
    'banana', 'apple', 'sandwich', 'orange', 'pizza', 'donut', 'cake',
    'scissors', 'clock', 'vase', 'tv', 'tie',
]

# ═══════════════════════════════════════════════════════════════════
#  Scenarios — the three pillars of the detection system
# ═══════════════════════════════════════════════════════════════════
SCENARIOS = {
    'risk_detection': {
        'name': 'Risk Detection',
        'icon': '🛡️',
        'features': {
            'object_danger': {
                'name': 'Danger Towards Objects',
                'desc': 'Detects when objects are too close (cup near laptop, snack near paper)',
                'active': True,
            },
            'pet_risk': {
                'name': 'Pet Risk',
                'desc': 'Detects cats or dogs near desk items — knocked drinks, chewed cables',
                'active': True,
            },
            'edge_danger': {
                'name': 'Edge Proximity',
                'desc': 'Warns when objects drift to the edge of the desk',
                'active': True,
            },
        },
    },
    'lost_object': {
        'name': 'Lost Object Tracking',
        'icon': '🔍',
        'features': {
            'track_phone': {
                'name': 'Phone Tracker',
                'desc': 'Alerts when your phone hasn\'t been seen for a while',
                'active': True, 'yolo_class': 'cell phone', 'alert_after_sec': 600,
            },
            'track_keys': {
                'name': 'Keys Tracker',
                'desc': 'Tracks when keys were last seen on desk',
                'active': True, 'yolo_class': 'remote', 'alert_after_sec': 900,
            },
            'track_wallet': {
                'name': 'Wallet / Cards',
                'desc': 'Logs wallet or card-like objects on the desk',
                'active': True, 'yolo_class': 'handbag', 'alert_after_sec': 900,
            },
            'track_airpods': {
                'name': 'AirPods / earbuds',
                'desc': 'Not in COCO — located via vision (Ollama) when the camera runs',
                'active': True, 'yolo_class': 'airpods', 'alert_after_sec': 900,
            },
        },
    },
    'reminders': {
        'name': 'Reminders',
        'icon': '⏰',
        'features': {
            'take_a_break': {
                'name': 'Take a Break',
                'desc': 'Auto-detects when you leave for 5+ min and logs a break',
                'active': True,
            },
            'focus': {
                'name': 'Focus Mode',
                'desc': 'Detects phone on desk — nudges you to put it away during work',
                'active': True,
            },
            'organization': {
                'name': 'Desk Organization',
                'desc': 'Alerts when too many objects clutter the desk surface',
                'active': True, 'clutter_threshold': 6,
            },
            'water_plants': {
                'name': 'Water Plants',
                'desc': 'Time-based reminder to water your plants',
                'active': True, 'interval_sec': 86400,
            },
            'hydration': {
                'name': 'Hydration',
                'desc': 'Auto-logs sips when cup leaves and returns to desk',
                'active': True,
            },
            'healthy_eating': {
                'name': 'Healthy Eating',
                'desc': 'Auto-detects food on desk and rates your choices',
                'active': True,
            },
            'posture_check': {
                'name': 'Posture Check',
                'desc': 'Periodic reminders to check posture, stretch, and sit properly',
                'active': True, 'interval_sec': 2400,
            },
            'screen_time': {
                'name': 'Screen Time',
                'desc': 'Tracks continuous screen time and nudges you to look away',
                'active': True,
            },
        },
    },
}

# Classes that are tracked but not assigned to a character
PET_CLASSES = {'cat', 'dog'}
LOST_OBJECT_CLASSES = {'cell phone', 'remote', 'handbag', 'scissors'}
LOST_OBJECT_ALERT_SEC = 600  # 10 min default
LOSEABLE_SNAPSHOT_COOLDOWN_SEC = int(os.getenv('LOSEABLE_SNAPSHOT_COOLDOWN_SEC', '75'))
LOST_ITEM_BOX_LABELS = {
    'remote': 'Keys / remote',
    'cell phone': 'Phone',
    'handbag': 'Wallet / bag',
    'scissors': 'Scissors',
}

# Chat replies must never expose internal YOLO/COCO names like "remote" when the user meant keys.
LOST_OBJECT_SHEETS_SYSTEM = """You are Paper, the warm, organized paper character in a desk-objects group chat.
The user asked whether something was seen on their desk. You are given FACTS from cameras + quick vision (may be wrong or empty).

Write a friendly reply in 2–5 short sentences. Rules:
- If they asked about keys, only say "keys" or "keychain" — never say "remote" (that word is an internal detector label, not for users).
- Say "phone", "wallet", "AirPods", "scissors" — not "cell phone", "handbag", or other jargon.
- If something was not seen on the desk in this run, suggest Desk captures for dated stills.
- The app always shows a button below this message labeled "Desk captures" (with a picture icon) — you may mention it in one short phrase.
- Only state sightings supported by FACTS; do not invent. Do not mention YOLO, COCO, models, or "API".
- Under 110 words."""

# ═══════════════════════════════════════════════════════════════════
#  Load safety_rules.json — per-pair pixel thresholds (chart pipeline)
#
#  The JSON rules use YOLO class names (e.g. "cell phone", "laptop").
#  We convert them to character IDs for the analysis loop.
# ═══════════════════════════════════════════════════════════════════
SAFETY_RULES = []      # [{char_a, char_b, speaker, threshold_px, label, yolo_a, yolo_b, relationship_type}]
SAFETY_RULES_RAW = []  # original JSON rules for the /api/rules endpoint


from relationships import RelationshipAnalyzer, load_config as load_rel_config
from phone_camera import (
    PhoneFrameSource, make_pair_key, lan_ip, ensure_cert, qr_svg, print_qr,
)

rel_analyzer = RelationshipAnalyzer()

# Phone camera state (only used when PHONE_MODE is on)
phone_source = PhoneFrameSource()
PHONE_PAIR_KEY = make_pair_key()
PHONE_STATIC = os.path.join(PROJECT_ROOT, 'phone')
phone_url = ''          # filled in at startup, e.g. https://192.168.1.20:5443/phone?key=...


def _load_safety_rules():
    """Load safety_rules.json — both legacy pair rules (SAFETY_RULES) and
    the new relationship config (danger zones, edge proximity, etc.)."""
    global SAFETY_RULES, SAFETY_RULES_RAW
    rules_path = RULES_PATH
    if not os.path.isfile(rules_path):
        print('  [rules] No safety_rules.json found — using built-in defaults')
        return False

    # Load the new relationship config (patches module-level constants)
    load_rel_config(rules_path)

    with open(rules_path, 'r') as f:
        data = json.load(f)

    # Also build SAFETY_RULES from "rules" array if present (backward compat)
    SAFETY_RULES_RAW = data.get('rules', [])
    SAFETY_RULES.clear()
    for rule in SAFETY_RULES_RAW:
        yolo_a = rule.get('object_a', '')
        yolo_b = rule.get('object_b', '')
        char_a = YOLO_TO_CHAR.get(yolo_a)
        char_b = YOLO_TO_CHAR.get(yolo_b)
        if not char_a or not char_b:
            continue
        speaker = char_b if char_b in ('monty', 'sheets') else char_a
        SAFETY_RULES.append({
            'char_a': char_a,
            'char_b': char_b,
            'speaker': speaker,
            'threshold_px': rule.get('safe_distance', 150),
            'label': rule.get('label', f'{yolo_a} near {yolo_b}'),
            'yolo_a': yolo_a,
            'yolo_b': yolo_b,
        })

    from relationships import DANGER_ZONES, EDGE_DANGER_PX
    print(f'  [rules] Loaded {len(SAFETY_RULES)} pair rules, '
          f'{len(DANGER_ZONES)} danger zones, '
          f'edge margin {EDGE_DANGER_PX}px')
    return True

HAS_SAFETY_RULES = False  # set at startup

# ═══════════════════════════════════════════════════════════════════
#  Fallback proximity rules (used when no safety_rules.json)
# ═══════════════════════════════════════════════════════════════════
PROXIMITY_RATIO  = float(os.getenv('PROXIMITY_RATIO', '0.22'))

FALLBACK_PROXIMITY_RULES = [
    ('glug', 'monty',  'monty',  'critical', 1.5),
    ('glug', 'sheets', 'sheets', 'high',     1.3),
    ('munch', 'monty',  'monty',  'high',    1.3),
    ('munch', 'sheets', 'sheets', 'medium',  1.0),
    ('munch', 'glug',   'glug',   'medium',  1.0),
]

SYMBIOTIC_RULES = []

RELATIONSHIP_DESCRIPTIONS = {
    frozenset({'glug', 'monty'}):  'Cup or bottle close to Computer — possible spill risk.',
    frozenset({'glug', 'sheets'}): 'Cup/liquid near Paper — water damage risk.',
    frozenset({'munch', 'monty'}): 'Snack near Computer — possible crumbs near keyboard.',
    frozenset({'munch', 'sheets'}): 'Snack near Paper — oil/grease stain risk.',
    frozenset({'munch', 'glug'}):  'Snack near Cup — increased hand movement near liquid.',
}

RELATIONSHIP_CONTEXT = """DESK ECOSYSTEM RELATIONSHIPS:

COMPUTER — Fragile, anxious, emotional center (includes keyboard):
- Cup nearby = possible spill on keyboard/screen. Reacts with subtle tension.
- Snack nearby = possible crumbs near keyboard. Genuinely frustrated.

CUP — Innocent, unaware of own danger:
- Near Computer = possible spill risk but Cup doesn't understand why everyone worries.
- Near Paper = water damage risk. Cup is confused by the concern.

SNACK — Hedonistic, unapologetic:
- Near Laptop = crumb contamination on keyboard. "Life's too short to worry."
- Near Cup = increases hand movement near liquids. Oblivious.
- Near Paper = oil stain risk. Dismissive.

PAPER — Gentle, anxious, extremely vulnerable:
- Cup nearby = terrified of water damage.
- Snack nearby = dreads oil stains.
- Other papers nearby = wants to be together. Gets lonely."""

# ═══════════════════════════════════════════════════════════════════
#  Fallback templates (used only when LLM is unavailable)
# ═══════════════════════════════════════════════════════════════════
FALLBACK_RESPONSES = {
    'monty': ["Noted. Processing.", "I'll keep that in mind.", "Understood."],
    'glug': ["aww! 💧 now drink water!", "hehe ok!", "you're the best!!"],
    'munch': ["mmm yeah 🍕", "can't talk, eating.", "life's too short to worry."],
    'sheets': ['• noted\n• filed', 'Got it.', '• acknowledged'],
}

# ═══════════════════════════════════════════════════════════════════
#  Habit Focus — each character coaches a different habit area
# ═══════════════════════════════════════════════════════════════════
HABIT_FOCUS = {
    'glug': {
        'area': 'Hydration',
        'tips': [
            "Have you had water in the last hour? Go drink some!",
            "Hydration check! Your brain needs water to think clearly.",
            "Time for a sip! Even a small glass counts.",
            "Your body is 60% water — keep it topped up!",
        ],
        'interval': 1800,  # remind every 30 min
    },
    'monty': {
        'area': 'Screen breaks, posture, energy & eye health',
        'tips': [
            "You've been staring at me for a while. Look away for 20 seconds!",
            "20-20-20 rule: every 20 min, look 20 feet away for 20 seconds.",
            "Time to rest your eyes. Close them for a moment.",
            "Screen brightness check — am I too bright for this room?",
            "How's your posture? Sit up straight, shoulders back.",
            "Stretch your wrists — you've been typing too long.",
            "Stand up and stretch — you've been sitting a long time.",
            "Feeling drained? A 5-minute break beats another scroll.",
        ],
        'interval': 1200,  # remind every 20 min
    },
    'munch': {
        'area': 'Healthy eating & mindful snacking',
        'tips': [
            "Hungry? Grab something healthy instead of junk food!",
            "Eating at your desk again? At least put me on a plate.",
            "Snack tip: fruits > chips. Your future self agrees.",
            "Don't skip meals! Your brain needs fuel to work.",
        ],
        'interval': 3600,  # remind every 60 min
    },
    'sheets': {
        'area': 'Task management & focus',
        'tips': [
            "Have you checked your to-do list recently?",
            "What's your top priority right now? Focus on ONE thing.",
            "Break that big task into smaller steps. You got this.",
            "Deadline approaching? Better start now than stress later.",
        ],
        'interval': 2400,  # remind every 40 min
    },
}

HABIT_FALLBACK_RESPONSES = {
    'glug': ["Drink water! 💧", "Hydration time!", "Sip sip! 💧"],
    'monty': ["Rest your eyes!", "Look away from the screen.", "20-20-20 rule!"],
    'munch': ["Eat something healthy!", "Snack wisely! 🍎", "Don't skip meals."],
    'sheets': ["Check your to-do list.", "What's your priority?", "Focus on one thing."],
}

# ═══════════════════════════════════════════════════════════════════
#  Shared state
# ═══════════════════════════════════════════════════════════════════
BREAK_THRESHOLD_SEC = int(os.getenv('BREAK_THRESHOLD_SEC', '300'))  # 5 min
SIP_GONE_MIN_SEC    = 3    # cup must be gone at least 3s
SIP_GONE_MAX_SEC    = 120  # and return within 2 min

class DeskState:
    rel_result: Optional[dict[str, Any]]
    person_last_seen: Optional[float]
    person_away_since: Optional[float]
    person_continuous_since: Optional[float]
    glug_gone_since: Optional[float]
    phone_on_desk_since: Optional[float]
    last_clutter_alert: float
    last_posture_reminder: float
    last_screen_time_reminder: float
    last_water_plants_reminder: float

    def __init__(self):
        self.lock = threading.Lock()
        self.objects = {}
        self.frames = {}
        self.raw_frames = {}
        self.frame_w = 640
        self.frame_h = 480
        self.events_queue = queue.Queue()
        self.cooldowns = {}
        self.interaction_counts = {}
        self.cameras = {}
        self.snapshots = OrderedDict()
        self.safety_state = 'SAFE'
        self.safety_dangers = []
        # Live context tracking
        self.object_first_seen = {}    # char_id → timestamp when first detected this session
        self.object_last_seen = {}     # char_id → timestamp of most recent detection
        self.danger_active_since = {}  # frozenset pair → timestamp when danger started
        self.recent_events = []        # last N events for context (capped)
        self.rel_result = None         # latest RelationshipAnalyzer output
        self.session_start = time.time()
        self.char_widgets = {}
        # Per-character rolling memory (list of short strings, newest last)
        self.char_memory = {cid: [] for cid in CHARACTERS}
        # Auto-detection state
        self.person_last_seen = None
        self.person_away_since = None
        self.person_break_logged = False
        self.person_continuous_since = None  # when person started continuous session (screen time)
        self.glug_gone_since = None
        self.munch_seen_classes = set()
        # Lost object tracking (yolo class keys) + VLM string keys, e.g. vlm:keys
        self.lost_objects = {}         # class key → {'last_seen': ts, 'alerted': bool, 'source': 'yolo'|'vlm'}
        self.merged_extra = {}         # last merged YOLO extras (updated by merger)
        self.habit_vision_brief = ''  # one-line desk signals for habit LLM
        self.lost_prev_gray = None     # 64x64 uint8 for scene change
        self.lost_last_scan_ts = 0.0
        self.lost_last_vlm_ts = 0.0
        self.habit_policy = {          # overridable via /api/habit_policy
            'dnd_enabled': HABIT_DND_ENABLED,
            'dnd_start_hour': HABIT_DND_START_HOUR,
            'dnd_end_hour': HABIT_DND_END_HOUR,
        }
        self.habit_pending = []        # {character, text, area, send_after}
        self.last_habit_fire = {}      # char_id → ts
        self.habit_vlm_brief = ''     # last optional VLM summary for habits
        self.habit_vlm_brief_ts = 0.0
        self.lost_last_vlm_visible = set()  # last VLM pass labels
        self.snapshot_order = []  # newest first: id, ts, label, char_ids, yolo_char, yolo_extra, vlm_labels
        self.last_desk_scan_snapshot_ts = 0.0
        # Ollama VLM habit scene (posture, distance, phone in hand)
        self.vlm_habit_state = {}
        self.vlm_habit_ts = 0.0
        self.vlm_habit_alert_ts = {}
        # Pet tracking
        self.pet_last_seen = {}        # 'cat'/'dog' → timestamp
        self.pet_near_cooldown = {}    # pet_class → last alert time
        # Focus / organization
        self.phone_on_desk_since = None
        self.phone_focus_alerted = False
        self.last_clutter_alert = 0
        self.last_posture_reminder = 0
        self.last_screen_time_reminder = 0
        self.last_water_plants_reminder = 0
        self.last_user_activity = 0.0  # ts of last user typing/sending — idle chat defers around it
        # Active scenarios (allow toggling)
        self.active_scenarios = {s: True for s in SCENARIOS}
        # Edge-triggered snapshots when loseable items newly appear (YOLO + VLM)
        self.loseable_snap_armed = False
        self.loseable_snap_prev_classes = set()
        self.loseable_snap_last_ts = {}
        self.loseable_vlm_snap_armed = False
        # Vision-backed habit log (Computer UI): posture segments, nail biting, etc.
        self.habit_events = []
        self.widget_views = {}          # char_id → [{'ts': float, 'action': str}, ...]
        self.daily_stats = {}           # date_str → DailyStats dict
        self.vlm_posture_slouch_since = None
        self.vlm_prev_nail_biting = False
        self.habit_nail_last_event_ts = 0.0

    def save_snapshot(self, cam_idx=None, crop_bboxes=None, label='Capture'):
        """Copy frame bytes under lock; run cv2 crop *outside* lock so the camera thread never stalls."""
        snap_id = uuid.uuid4().hex[:12]
        with self.lock:
            jpeg = None
            if cam_idx is not None:
                j = self.frames.get(cam_idx)
                if j:
                    jpeg = bytes(j)
            if not jpeg:
                for ci in sorted(self.frames.keys()):
                    j = self.frames.get(ci)
                    if j:
                        jpeg = bytes(j)
                        break
        if not jpeg:
            return None

        if crop_bboxes and len(crop_bboxes) > 0:
            arr = np.frombuffer(jpeg, dtype=np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if img is not None:
                ih, iw = img.shape[:2]
                ux1 = min(b[0] for b in crop_bboxes)
                uy1 = min(b[1] for b in crop_bboxes)
                ux2 = max(b[2] for b in crop_bboxes)
                uy2 = max(b[3] for b in crop_bboxes)
                pw = (ux2 - ux1) * 0.25
                ph = (uy2 - uy1) * 0.25
                cx1, cy1 = max(0, int(ux1 - pw)), max(0, int(uy1 - ph))
                cx2, cy2 = min(iw, int(ux2 + pw)), min(ih, int(uy2 + ph))
                if (cx2 - cx1) > 40 and (cy2 - cy1) > 40:
                    cropped = img[cy1:cy2, cx1:cx2]
                    _, buf = cv2.imencode('.jpg', cropped, [cv2.IMWRITE_JPEG_QUALITY, 85])
                    jpeg = buf.tobytes()

        with self.lock:
            yolo_char = []
            for det in self.objects.values():
                cn = det.get('class_name')
                if cn:
                    yolo_char.append(str(cn).lower())
            tag_meta = {
                'char_ids': list(self.objects.keys()),
                'yolo_char': yolo_char,
                'yolo_extra': [str(k).lower() for k in self.merged_extra.keys()],
                'vlm_labels': sorted(str(x).lower() for x in self.lost_last_vlm_visible),
            }
            self.snapshots[snap_id] = jpeg
            self.snapshot_order.insert(0, {
                'id': snap_id,
                'ts': time.time(),
                'label': (label or 'Capture'),
                **tag_meta,
            })
            self.snapshot_order = self.snapshot_order[:MAX_SNAPSHOTS]
            while len(self.snapshots) > MAX_SNAPSHOTS:
                old_id, _ = self.snapshots.popitem(last=False)
                self.snapshot_order = [x for x in self.snapshot_order if x.get('id') != old_id]
        return snap_id

desk = DeskState()

# ═══════════════════════════════════════════════════════════════════
#  Lost object + habit policy helpers (framework: scene gate → YOLO → VLM)
# ═══════════════════════════════════════════════════════════════════
VLM_FEATURE_ALIASES = {
    'track_phone': ('cell phone', 'phone'),
    'track_keys': ('keys', 'key', 'remote'),
    'track_wallet': ('wallet', 'handbag', 'cards'),
    'track_airpods': ('airpods', 'airpod', 'earbuds', 'earbuds case'),
}


def _local_hour():
    return time.localtime().tm_hour


def _habit_dnd_active():
    with desk.lock:
        p = dict(desk.habit_policy)
    if not p.get('dnd_enabled'):
        return False
    start = int(p.get('dnd_start_hour', 22))
    end = int(p.get('dnd_end_hour', 7))
    h = _local_hour()
    if start > end:
        return h >= start or h < end
    return start <= h < end


def _build_habit_vision_brief(objects, extra, person_seen):
    parts = [f'person={"yes" if person_seen else "no"}']
    if 'cell phone' in extra:
        parts.append('phone_on_desk=yes')
    else:
        parts.append('phone_on_desk=no')
    cup = 'glug' in objects
    parts.append(f'cup_on_desk={"yes" if cup else "no"}')
    parts.append(f'desk_object_count={len(objects) + len(extra)}')
    return '; '.join(parts)


def _gray_small(jpeg_bytes, size=64):
    arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
    if img is None:
        return None
    return cv2.resize(img, (size, size), interpolation=cv2.INTER_AREA)


def _scene_mean_diff(gray_a, gray_b):
    if gray_a is None or gray_b is None or gray_a.shape != gray_b.shape:
        return 1e9
    return float(np.mean(cv2.absdiff(gray_a, gray_b)))


def _lost_init_entry():
    return {'last_seen': None, 'alerted': False, 'source': None, 'last_near': []}


def _vlm_match_feature(feat_id, vlm_hits, yc_in_extra):
    if yc_in_extra:
        return True
    if not vlm_hits:
        return False
    h = {str(x).lower() for x in vlm_hits}
    for s in h:
        if feat_id == 'track_phone' and any(x in s for x in ('phone', 'cell', 'iphone', 'android')):
            return True
        if feat_id == 'track_keys' and any(x in s for x in ('key', 'remote', 'fob')):
            return True
        if feat_id == 'track_wallet' and any(x in s for x in ('wallet', 'card', 'handbag', 'purse', 'bag', 'pouch')):
            return True
        if feat_id == 'track_airpods' and any(x in s for x in (
                'airpod', 'airpods', 'earbud', 'earbuds', 'ear phone', 'earphone')):
            return True
    return False


def _lost_feat_id_for_yolo(yc):
    """Map tracked yolo_class (e.g. cell phone) to lost_object feature id."""
    yc_lc = (yc or '').lower().strip()
    for fid, feat in SCENARIOS['lost_object']['features'].items():
        if (feat.get('yolo_class') or '').lower() == yc_lc:
            return fid
    return None


def _lost_yolo_in_snapshot_meta(meta, yc):
    """True if snapshot tag metadata shows this item was present when the capture was saved."""
    if not meta or not yc:
        return False
    yc_lc = yc.lower().strip()
    yolo_union = [
        str(x).lower() for x in (meta.get('yolo_char') or []) + (meta.get('yolo_extra') or [])
    ]
    if yc_lc in yolo_union:
        return True
    vlm_set = {str(x).lower() for x in (meta.get('vlm_labels') or [])}
    fid = _lost_feat_id_for_yolo(yc)
    if fid and _vlm_match_feature(fid, vlm_set, False):
        return True
    return False


def _find_recent_snapshot_for_lost_yolo(yc):
    """Newest-first: first snapshot whose tags include yc. Returns (snap_id, meta) or (None, None)."""
    with desk.lock:
        order = list(desk.snapshot_order)
    for entry in order:
        if _lost_yolo_in_snapshot_meta(entry, yc):
            sid = entry.get('id')
            if sid:
                return sid, entry
    return None, None


def _vision_note_lost_item(gen, yc, user_message):
    """Prefer a tagged desk capture + VLM for position; fall back to live frame + VLM."""
    if not OLLAMA_URL or not gen or not (getattr(gen, 'vision_client', None) or getattr(gen, 'client', None)):
        return None
    _vlm_item_names = {
        'remote': 'keys, a keychain, or key fob',
        'airpods': 'AirPods case or earbuds',
        'cell phone': 'a phone or smartphone',
        'handbag': 'a wallet, purse, or small bag',
    }
    item_desc = _vlm_item_names.get(yc, yc)
    label_natural = _lost_chat_item_name(yc, user_message)

    sid, meta = _find_recent_snapshot_for_lost_yolo(yc)
    if sid:
        with desk.lock:
            jpg_snap = desk.snapshots.get(sid)
        if jpg_snap:
            ts_human = _format_local_ts(meta.get('ts'))
            cap_lbl = (meta.get('label') or '').strip()
            ctx = f'Saved desk photo from {ts_human}' + (f' — {cap_lbl}' if cap_lbl else '') + '.'
            prompt = (
                f'{ctx} '
                f'The user is looking for {label_natural} (visually: {item_desc}). '
                f'Describe where it appears in THIS image — left/right, near laptop, under paper, etc. '
                f'If you truly cannot see it, say so in one sentence.'
            )
            vn = gen._call_vision_vlm(jpg_snap, prompt, model=LOST_VISION_MODEL)
            if vn and len(vn.split()) >= 3:
                return vn.strip()[:450]

    jpg = None
    with desk.lock:
        for ci in sorted(desk.raw_frames.keys()):
            jpg = desk.raw_frames.get(ci)
            if jpg:
                break
    if jpg:
        vn = gen._call_vision_vlm(
            jpg,
            f'Is {item_desc} visible on the desk in this image? '
            f'Reply with one short sentence. Start with "Visible:" or "Not visible:". '
            f'If visible, mention where on the desk relative to other objects.',
            model=LOST_VISION_MODEL,
        )
        if vn:
            return vn.strip()[:220]
    return None


def is_lost_object_query_message(text):
    t = (text or '').lower()
    triggers = (
        'seen my', "see my", 'saw my', 'where is my', 'where are my', "where's my", 'wheres my',
        'lost my', 'anyone see', 'anyone seen', 'anybody see', 'anybody seen',
        'have you seen', 'did you see', 'did anyone see', 'last see', 'do you see my',
        'where did i put', 'find my', 'spotted my', "can't find", 'cant find',
        'looking for my', 'look for my', 'missing my', 'picked up my',
    )
    if not any(s in t for s in triggers):
        return False
    nouns = (
        'key', 'keys', 'phone', 'wallet', 'airpod', 'airpods', 'earbud', 'earbuds', 'remote', 'fob',
        'card', 'cards', 'bag', 'purse', 'scissor', 'scissors', 'laptop', 'cup', 'bottle', 'book',
    )
    return any(n in t for n in nouns)


class LostObjectFrameworkThread(threading.Thread):
    """LOST_LOOP_SEC: YOLO path from merged_extra each tick; VLM only when scene changes or heartbeat."""

    def __init__(self, gen):
        super().__init__(daemon=True, name='LostObjectFramework')
        self.gen = gen

    def run(self):
        time.sleep(5)
        while True:
            try:
                self._tick()
            except Exception as exc:
                print(f'  [lost_framework] {exc}')
            time.sleep(LOST_LOOP_SEC)

    def _tick(self):
        now = time.time()
        with desk.lock:
            if not desk.active_scenarios.get('lost_object', True):
                return
            jpg = None
            for ci in sorted(desk.raw_frames.keys()):
                jpg = desk.raw_frames.get(ci)
                if jpg:
                    break
            extra = dict(desk.merged_extra)
        if not jpg:
            return

        gray = _gray_small(jpg)
        with desk.lock:
            prev = desk.lost_prev_gray
        diff = _scene_mean_diff(prev, gray) if prev is not None else 1e9
        scene_changed = diff >= LOST_SCENE_DIFF_THR
        with desk.lock:
            last_scan = desk.lost_last_scan_ts
        heartbeat = (now - last_scan) >= LOST_HEARTBEAT_SEC if last_scan > 0 else True
        run_vlm = scene_changed or heartbeat
        with desk.lock:
            if gray is not None:
                desk.lost_prev_gray = np.copy(gray)
            desk.lost_last_scan_ts = now

        self._ingest_yolo_path(now, extra)

        vlm_hits = set()
        vlm_ran = False
        if run_vlm and self.gen and getattr(self.gen, 'client', None) and OLLAMA_URL:
            with desk.lock:
                prev_vlm_visible = set(desk.lost_last_vlm_visible)
            vlm_hits = self._ingest_vlm_path(jpg) or set()
            vlm_ran = True
            t_wall = time.time()
            loseable_vlm_edge_snapshots(prev_vlm_visible, vlm_hits, t_wall)
            gap_ok = False
            with desk.lock:
                desk.lost_last_vlm_ts = now
                desk.lost_last_vlm_visible = set(vlm_hits)
                t_snap = time.time()
                gap_ok = (t_snap - desk.last_desk_scan_snapshot_ts) >= DESK_SCAN_SNAPSHOT_MIN_SEC
            if gap_ok:
                sid = desk.save_snapshot(label='Desk scan')
                if sid:
                    with desk.lock:
                        desk.last_desk_scan_snapshot_ts = time.time()
        else:
            with desk.lock:
                vlm_hits = set(desk.lost_last_vlm_visible)

        self._process_lost_alerts(now, extra, vlm_hits, vlm_ran)

    @staticmethod
    def _ingest_yolo_path(now, extra):
        with desk.lock:
            nearby_chars = [
                CHARACTERS[cid]['name'] for cid in desk.objects
            ]
            nearby_extra = [
                cls for cls in desk.merged_extra if cls not in ('person',)
            ]
            neighbors = nearby_chars + nearby_extra

            for _fid, feat in SCENARIOS['lost_object']['features'].items():
                if not feat.get('active'):
                    continue
                yc = feat.get('yolo_class', '')
                if not yc:
                    continue
                e = desk.lost_objects.setdefault(yc, _lost_init_entry())
                if yc in extra:
                    e['last_seen'] = now
                    e['alerted'] = False
                    e['source'] = 'yolo'
                    e['last_near'] = [n for n in neighbors if n.lower() != yc.lower()][:5]

    def _ingest_vlm_path(self, jpg):
        if not OLLAMA_URL or not self.gen or not self.gen.client:
            return set()
        lines = [
            f'{f["name"]}→{f.get("yolo_class", "")}'
            for f in SCENARIOS['lost_object']['features'].values() if f.get('active')
        ]
        prompt = (
            'Look at the image. For desk lost-item tracking reply ONLY this JSON, no other text: '
            '{"visible":["label",...]}. Labels: short English for visible items: '
            f'{", ".join(lines)}, keys, airpods, wallet, phone, handbag, remote, scissors. '
            'Omit items you are not confident about.'
        )
        raw = self.gen._call_vision_vlm(jpg, prompt, model=LOST_VISION_MODEL)
        if not raw:
            return set()
        try:
            j = json.loads(raw)
        except json.JSONDecodeError:
            m = re.search(r'\{[\s\S]*\}', raw)
            if m:
                try:
                    j = json.loads(m.group(0))
                except json.JSONDecodeError:
                    return set()
            else:
                return set()
        vis = j.get('visible', [])
        if not isinstance(vis, list):
            return set()
        hset = {str(x).strip().lower() for x in vis if x}
        t = time.time()
        with desk.lock:
            extra2 = dict(desk.merged_extra)
            nearby_chars = [CHARACTERS[cid]['name'] for cid in desk.objects]
            vlm_neighbors = list(hset)

            for feat_id, feat in SCENARIOS['lost_object']['features'].items():
                if not feat.get('active'):
                    continue
                yc = feat.get('yolo_class', '')
                if not yc:
                    continue
                y_in = yc in extra2
                if y_in:
                    continue
                if not _vlm_match_feature(feat_id, hset, False):
                    continue
                e = desk.lost_objects.setdefault(yc, _lost_init_entry())
                e['last_seen'] = t
                e['alerted'] = False
                e['source'] = 'vlm'
                e['last_near'] = [n for n in (nearby_chars + vlm_neighbors) if n.lower() != yc.lower()][:5]
        return hset

    def _process_lost_alerts(self, now, extra, vlm_hits, run_vlm_this_tick):
        for feat_id, feat in SCENARIOS['lost_object']['features'].items():
            if not feat.get('active'):
                continue
            yc = feat.get('yolo_class', '')
            alert_sec = feat.get('alert_after_sec', LOST_OBJECT_ALERT_SEC)
            if yc in extra:
                continue
            if run_vlm_this_tick and _vlm_match_feature(feat_id, vlm_hits, False):
                continue
            with desk.lock:
                e = desk.lost_objects.get(yc) or _lost_init_entry()
                if not e.get('last_seen') or e.get('alerted'):
                    continue
                gone_for = now - e['last_seen']
                if gone_for < alert_sec:
                    continue
                if yc in desk.lost_objects:
                    desk.lost_objects[yc]['alerted'] = True
                last_near = list(e.get('last_near', []))
            mins = int(gone_for / 60)
            near_hint = f' Last seen near: {", ".join(last_near[:3])}.' if last_near else ''
            alert_text = self._lost_alert_text(feat['name'], mins, near_hint)
            _bump_daily_stat('lost_alerts')
            desk.events_queue.put({
                'type': 'scenario_alert',
                'scenario': 'lost_object',
                'feature': feat_id,
                'character': 'group',
                'text': alert_text,
            })

    def _lost_alert_text(self, item_name, gone_mins, near_hint):
        """Generate a natural lost-object alert using LLM if available."""
        if self.gen and self.gen.client:
            prompt = (
                f'You are a desk object that just noticed {item_name} has been missing for {gone_mins} minute(s).{near_hint}\n'
                f'Write ONE sentence (max 25 words) as if you are the item speaking in first person. '
                f'Be casual and helpful. Mention where you were last seen if context is given. Just the message.'
            )
            text = self.gen._call_llm([
                {'role': 'system', 'content': 'You are a lost desk item speaking in first person. Be brief, natural, and helpful.'},
                {'role': 'user', 'content': prompt},
            ], max_tokens=60, temperature=0.7)
            if text:
                return text
        return f'{item_name}: hasn\'t been seen for {gone_mins} min.{near_hint}'


# ═══════════════════════════════════════════════════════════════════
#  Event emission
# ═══════════════════════════════════════════════════════════════════
PRESENCE_COOLDOWN = int(os.getenv('PRESENCE_COOLDOWN', '120'))

def _emit_event(event_type, character, **kwargs):
    if event_type in ('disappeared', 'appeared'):
        return  # disabled — only proximity/safety interactions trigger messages
    key = f'{event_type}_{character}'
    now = time.time()
    if key in desk.cooldowns and (now - desk.cooldowns[key]) < EVENT_COOLDOWN:
        return
    desk.cooldowns[key] = now
    desk.events_queue.put({'type': event_type, 'character': character, **kwargs})

def _emit_interaction(event_type, character, cam_idx, crop_bboxes, **kwargs):
    key = f'{event_type}_{character}_{kwargs.get("near", "")}'
    now = time.time()
    last_time = desk.cooldowns.get(key, 0)
    count = desk.interaction_counts.get(key, 0)
    if now - last_time > 300:
        count = 0
    cooldown = EVENT_COOLDOWN * min(2 ** count, 8)
    if last_time and (now - last_time) < cooldown:
        return
    desk.cooldowns[key] = now
    desk.interaction_counts[key] = count + 1
    _bump_daily_stat('danger_count')
    snap_label = kwargs.pop('snapshot_label', 'Proximity alert')
    snap_id = desk.save_snapshot(cam_idx=cam_idx, crop_bboxes=crop_bboxes, label=snap_label)
    with desk.lock:
        desk.recent_events.append({
            'time': now, 'type': event_type, 'character': character,
            'near': kwargs.get('near', ''), 'severity': kwargs.get('severity', ''),
            'rule_label': kwargs.get('rule_label', ''),
        })
        if len(desk.recent_events) > 30:
            desk.recent_events = desk.recent_events[-30:]
    desk.events_queue.put({
        'type': event_type, 'character': character, 'snapshot': snap_id,
        'follow_up': count,
        **kwargs,
    })


def _open_capture_at_index(idx):
    """Open camera `idx`; on macOS retry with AVFoundation if the default backend fails."""
    cap = cv2.VideoCapture(idx)
    if cap.isOpened():
        return cap
    cap.release()
    if sys.platform == 'darwin':
        cap = cv2.VideoCapture(idx, cv2.CAP_AVFOUNDATION)
        if cap.isOpened():
            return cap
        cap.release()
    return None


def _capture_yields_frames(cap, warmup_reads=10):
    """USB / virtual cameras often need several reads before frames are valid."""
    for _ in range(warmup_reads):
        ok, frame = cap.read()
        if ok and frame is not None and frame.size > 0:
            return True
    return False


def discover_working_camera(preferred_idx, max_index=4):
    """
    Try `preferred_idx` first, then other indices. Many external webcams (e.g. USB) are index 1 on macOS
    while index 0 is FaceTime HD — isOpened() can succeed on the wrong empty device.
    Returns (cap, idx) or (None, None).
    """
    order = [preferred_idx] + [i for i in range(max_index) if i != preferred_idx]
    for idx in order:
        cap = _open_capture_at_index(idx)
        if cap is None:
            continue
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        try:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass
        if _capture_yields_frames(cap):
            return cap, idx
        cap.release()
    return None, None


# Ultralytics lazily builds an internal predictor on first predict(); concurrent calls corrupt fuse/BN state.
_YOLO_PREDICT_LOCK = threading.Lock()


# ═══════════════════════════════════════════════════════════════════
#  Camera threads + merger
# ═══════════════════════════════════════════════════════════════════
class CameraThread(threading.Thread):
    def __init__(self, cam_idx, model, source=None):
        super().__init__(daemon=True)
        self.cam_idx = cam_idx
        self.model = model
        # source: anything with a VideoCapture-style read() — e.g. PhoneFrameSource.
        # None means "open a local webcam with OpenCV" (original behavior).
        self.source = source
        self.local_objects = {}
        self.person_detected = False
        self.extra_detections = {}

    def run(self):
        preferred = self.cam_idx
        if self.source is not None:
            cap, use_idx = self.source, self.cam_idx
        else:
            cap, use_idx = discover_working_camera(preferred)
        if cap is None:
            print(f'  [cam {preferred}] No camera produced frames after trying indices 0–3.')
            print('         → External USB webcams are often index 1:  PIPELINE_CAMERA=1  or  CAMERA_INDICES=1')
            print('         → macOS: quit other apps using the webcam (menu-bar camera preview, Zoom, etc.).')
            print('         → Privacy: Camera enabled for the app running Python (Terminal or Cursor).')
            return

        self.cam_idx = use_idx
        if use_idx != preferred:
            print(f'  [cam] Using OpenCV index {use_idx} (configured {preferred} did not deliver frames). '
                  f'Set PIPELINE_CAMERA={use_idx} or CAMERA_INDICES={use_idx} to skip scanning.')

        with desk.lock:
            desk.cameras[self.cam_idx] = True
        print(f'  [cam {self.cam_idx}] Opened successfully.')

        seen_count, gone_count, confirmed = {}, {}, set()

        while True:
            ok, frame = cap.read()
            if not ok or frame is None or frame.size == 0:
                time.sleep(0.05)
                continue
            h, w = frame.shape[:2]
            desk.frame_w, desk.frame_h = w, h

            # The phone already sends JPEG, so reuse its bytes instead of encoding again.
            raw_jpeg = getattr(cap, 'jpeg', None) if self.source is not None else None
            if raw_jpeg is None:
                _, br_live = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 78])
                raw_jpeg = br_live.tobytes()
            with desk.lock:
                desk.raw_frames[self.cam_idx] = raw_jpeg

            try:
                with _YOLO_PREDICT_LOCK:
                    results = self.model.predict(source=frame, conf=CONFIDENCE, verbose=False)
            except Exception as exc:
                print(f'  [cam {self.cam_idx}] YOLO predict error: {exc}')
                time.sleep(0.2)
                continue
            new_objects = {}
            person_detected = False
            extra_detections = {}
            for r in results:
                for box in r.boxes:
                    cls_name = self.model.names[int(box.cls[0])]
                    x1, y1, x2, y2 = box.xyxy[0].tolist()
                    conf = float(box.conf[0])
                    if cls_name == 'person':
                        person_detected = True
                        continue
                    char_id = YOLO_TO_CHAR.get(cls_name)
                    if char_id:
                        if char_id == 'glug' and conf < GLUG_MIN_CONF:
                            char_id = None
                        if char_id and (char_id not in new_objects or conf > new_objects[char_id]['conf']):
                            new_objects[char_id] = {
                                'class_name': cls_name,
                                'bbox': [x1, y1, x2, y2],
                                'center': [(x1 + x2) / 2, (y1 + y2) / 2],
                                'conf': conf, 'cam': self.cam_idx,
                            }
                    if cls_name in PET_CLASSES or cls_name in LOST_OBJECT_CLASSES:
                        extra_detections[cls_name] = {
                            'bbox': [x1, y1, x2, y2],
                            'center': [(x1 + x2) / 2, (y1 + y2) / 2],
                            'conf': conf,
                        }
            self.person_detected = person_detected
            self.extra_detections = extra_detections

            raw = set(new_objects.keys())
            for cid in set(list(seen_count) + list(gone_count) + list(raw)):
                if cid in raw:
                    seen_count[cid] = seen_count.get(cid, 0) + 1
                    gone_count[cid] = 0
                else:
                    gone_count[cid] = gone_count.get(cid, 0) + 1
                    seen_count[cid] = 0
            now = time.time()
            for cid in raw:
                if cid not in confirmed and seen_count.get(cid, 0) >= APPEAR_FRAMES:
                    confirmed.add(cid)
                    with desk.lock:
                        if cid not in desk.object_first_seen:
                            desk.object_first_seen[cid] = now
                        desk.object_last_seen[cid] = now
                    _emit_event('appeared', cid)
            for cid in list(confirmed):
                if cid in raw:
                    with desk.lock:
                        desk.object_last_seen[cid] = now
                if cid not in raw and gone_count.get(cid, 0) >= DISAPPEAR_FRAMES:
                    confirmed.discard(cid)
                    _emit_event('disappeared', cid)
                    seen_count.pop(cid, None)
                    gone_count.pop(cid, None)

            self.local_objects = {c: d for c, d in new_objects.items() if c in confirmed}

            annotated = results[0].plot()
            self._draw_overlays(annotated, self.local_objects, self.extra_detections)
            _, ba = cv2.imencode('.jpg', annotated, [cv2.IMWRITE_JPEG_QUALITY, 70])
            with desk.lock:
                desk.frames[self.cam_idx] = ba.tobytes()
            time.sleep(0.05)

    def _draw_overlays(self, frame, objects, extra_detections):
        """Draw centroids, relationship diagrams, and lost-item highlights."""
        if objects:
            # Centroid dots and character names
            for cid, det in objects.items():
                cx, cy = int(det['center'][0]), int(det['center'][1])
                char = CHARACTERS[cid]
                cv2.circle(frame, (cx, cy), 6, (0, 200, 255), -1)
                cv2.circle(frame, (cx, cy), 8, (255, 255, 255), 1)
                label = char['name']
                (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
                cv2.rectangle(frame, (cx - tw//2 - 3, cy - 20 - th - 2),
                              (cx + tw//2 + 3, cy - 18), (0, 0, 0), -1)
                cv2.putText(frame, label, (cx - tw//2, cy - 20),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1, cv2.LINE_AA)

            # Relationship diagrams from the analyzer
            char_names = {cid: CHARACTERS[cid]['name'] for cid in objects if cid in CHARACTERS}
            rel_analyzer.update_frame_size(frame.shape[1], frame.shape[0])
            rel_analyzer.draw_overlays(frame, objects, char_names)

        # Commonly lost items (YOLO): bright box + label on stream
        for cls, det in (extra_detections or {}).items():
            if cls not in LOST_OBJECT_CLASSES:
                continue
            bb = det.get('bbox')
            if not bb or len(bb) < 4:
                continue
            x1, y1, x2, y2 = [int(round(float(x))) for x in bb[:4]]
            lab = LOST_ITEM_BOX_LABELS.get(cls, cls)
            cv2.rectangle(frame, (x1, y1), (x2, y2), (60, 220, 255), 2)
            (tw, th), bl = cv2.getTextSize(lab, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
            ty = max(0, y1 - 6)
            cv2.rectangle(frame, (x1, ty - th - 6), (x1 + tw + 8, ty + 2), (40, 40, 40), -1)
            cv2.putText(frame, lab, (x1 + 4, ty - 2), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                        (220, 250, 255), 1, cv2.LINE_AA)


class DetectionMerger(threading.Thread):
    def __init__(self, cam_threads):
        super().__init__(daemon=True)
        self.cam_threads = cam_threads

    def run(self):
        while True:
            if PHONE_MODE and not phone_source.connected:
                # Phone isn't streaming (locked, closed, out of Wi-Fi). Pause analysis so the
                # characters don't react to an old frame or log fake breaks/sips.
                with desk.lock:
                    desk.objects = {}
                time.sleep(0.5)
                continue
            merged = {}
            person_seen = False
            for ct in self.cam_threads:
                if ct.person_detected:
                    person_seen = True
                for cid, det in ct.local_objects.items():
                    if cid not in merged or det['conf'] > merged[cid]['conf']:
                        merged[cid] = det
            with desk.lock:
                desk.objects = merged
            self._track_auto_events(merged, person_seen)
            self._analyze(merged)
            time.sleep(0.3)

    @staticmethod
    def _bboxes_for_cam(objects, char_ids, prefer_cam):
        bboxes = []
        for cid in char_ids:
            obj = objects.get(cid)
            if obj and obj.get('cam') == prefer_cam:
                bboxes.append(obj['bbox'])
        if not bboxes:
            for cid in char_ids:
                obj = objects.get(cid)
                if obj:
                    bboxes.append(obj['bbox'])
        return bboxes

    def _loseable_yolo_edge_snapshots(self, extra, now_ts):
        """Save one snapshot per loseable YOLO class when it newly enters frame (not while staying)."""
        loseable = _loseable_snapshot_yolo_classes()
        curr = frozenset(cls for cls in extra if cls.lower() in loseable)
        snap_labels = []
        with desk.lock:
            if not desk.active_scenarios.get('lost_object', True):
                return
            if not desk.loseable_snap_armed:
                desk.loseable_snap_prev_classes = set(curr)
                desk.loseable_snap_armed = True
                return
            prev = desk.loseable_snap_prev_classes
            desk.loseable_snap_prev_classes = set(curr)
            appeared = set(curr) - prev
            cd = LOSEABLE_SNAPSHOT_COOLDOWN_SEC
            for cls in appeared:
                lk = f'yolo:{cls}'
                if now_ts - desk.loseable_snap_last_ts.get(lk, 0) < cd:
                    continue
                desk.loseable_snap_last_ts[lk] = now_ts
                nice = LOST_ITEM_BOX_LABELS.get(cls, cls.replace('_', ' ').title())
                snap_labels.append(f'Spotted: {nice}')
        for lbl in snap_labels:
            desk.save_snapshot(label=lbl)

    def _track_auto_events(self, objects, person_seen):
        """Track all scenario events: breaks, sips, snacks, pets, lost objects, focus, clutter, posture, screen time."""
        now = time.time()

        extra = {}
        for ct in self.cam_threads:
            extra.update(getattr(ct, 'extra_detections', {}))

        brief = _build_habit_vision_brief(objects, extra, person_seen)
        with desk.lock:
            desk.merged_extra = dict(extra)
            desk.habit_vision_brief = brief

        self._loseable_yolo_edge_snapshots(extra, now)

        with desk.lock:
            # ── 1. BREAK DETECTION (Computer) ───────────────────────
            if person_seen:
                if desk.person_away_since is not None:
                    away_dur = now - desk.person_away_since
                    if away_dur >= BREAK_THRESHOLD_SEC and not desk.person_break_logged:
                        desk.person_break_logged = True
                        w = desk.char_widgets.get('monty')
                        if w and w.get('type') == 'break_timer':
                            w['breaks_today'] = w.get('breaks_today', 0) + 1
                            w['last_break'] = time.strftime('%Y-%m-%dT%H:%M:%S')
                            dur_min = int(away_dur / 60)
                            desk.events_queue.put({
                                'type': 'habit_reminder', 'character': 'monty',
                                'text': f'Nice \u2014 you were away for {dur_min} min. Break logged automatically!',
                            })
                if desk.person_continuous_since is None:
                    desk.person_continuous_since = now
                desk.person_last_seen = now
                desk.person_away_since = None
                desk.person_break_logged = False
            else:
                if desk.person_last_seen and desk.person_away_since is None:
                    desk.person_away_since = now
                desk.person_continuous_since = None

            # ── 2. SIP DETECTION (Cup) ──────────────────────────────
            glug_present = 'glug' in objects
            if glug_present:
                if desk.glug_gone_since is not None:
                    gone_dur = now - desk.glug_gone_since
                    if SIP_GONE_MIN_SEC <= gone_dur <= SIP_GONE_MAX_SEC:
                        w = desk.char_widgets.get('glug')
                        if w and w.get('type') == 'sip_counter':
                            w['sips'] = w.get('sips', 0) + 1
                            w['last_sip'] = time.strftime('%Y-%m-%dT%H:%M:%S')
                            desk.events_queue.put({
                                'type': 'habit_reminder', 'character': 'glug',
                                'text': f'Sip detected! That\'s {w["sips"]} today. Keep it up!',
                            })
                desk.glug_gone_since = None
            else:
                if desk.glug_gone_since is None and desk.object_last_seen.get('glug'):
                    desk.glug_gone_since = now

            # ── 3. SNACK DETECTION (Snack) ──────────────────────────
            munch_present = 'munch' in objects
            if munch_present:
                cls_name = objects['munch'].get('class_name', 'snack')
                if cls_name not in desk.munch_seen_classes:
                    desk.munch_seen_classes.add(cls_name)
                    w = desk.char_widgets.get('munch')
                    if w and w.get('type') == 'snack_log':
                        is_healthy = cls_name in ('banana', 'apple', 'orange', 'broccoli', 'carrot')
                        w.setdefault('entries', []).append({
                            'label': cls_name.title(), 'healthy': is_healthy,
                        })
                        emoji = '\U0001F34E' if is_healthy else '\U0001F355'
                        desk.events_queue.put({
                            'type': 'habit_reminder', 'character': 'munch',
                            'text': f'{emoji} Spotted a {cls_name} on the desk \u2014 logged it!',
                        })

            # ── 4. PET RISK DETECTION ───────────────────────────────
            for pet_cls in PET_CLASSES:
                if pet_cls in extra:
                    desk.pet_last_seen[pet_cls] = now
                    last_alert = desk.pet_near_cooldown.get(pet_cls, 0)
                    if now - last_alert > 120:
                        desk.pet_near_cooldown[pet_cls] = now
                        pet_name = pet_cls.title()
                        at_risk = [cid for cid in objects if cid in ('glug', 'munch')]
                        risk_msg = ''
                        if at_risk:
                            names = ', '.join(CHARACTERS[c]['name'] for c in at_risk)
                            risk_msg = f' {names} could get knocked over!'
                        desk.events_queue.put({
                            'type': 'scenario_alert',
                            'scenario': 'risk_detection',
                            'feature': 'pet_risk',
                            'character': 'monty',
                            'text': f'{pet_name} detected near the desk!{risk_msg} Careful!',
                        })

            # ── 5. LOST OBJECT TRACKING (YOLO + VLM + alerts) → LostObjectFrameworkThread

            # ── 6. FOCUS MODE (phone distraction) ───────────────────
            phone_present = 'cell phone' in extra
            if phone_present:
                if desk.phone_on_desk_since is None:
                    desk.phone_on_desk_since = now
                    desk.phone_focus_alerted = False
                elif not desk.phone_focus_alerted and (now - desk.phone_on_desk_since) > 300:
                    desk.phone_focus_alerted = True
                    desk.events_queue.put({
                        'type': 'scenario_alert',
                        'scenario': 'reminders', 'feature': 'focus',
                        'character': 'sheets',
                        'text': 'Your phone has been on the desk for 5+ minutes. Put it away to stay focused!',
                    })
            else:
                desk.phone_on_desk_since = None
                desk.phone_focus_alerted = False

            # ── 7. DESK ORGANIZATION (clutter) ──────────────────────
            total_items = len(objects) + len(extra)
            clutter_threshold = SCENARIOS['reminders']['features']['organization'].get('clutter_threshold', 6)
            if total_items >= clutter_threshold and (now - desk.last_clutter_alert) > 1800:
                desk.last_clutter_alert = now
                desk.events_queue.put({
                    'type': 'scenario_alert',
                    'scenario': 'reminders', 'feature': 'organization',
                    'character': 'sheets',
                    'text': f'Your desk has {total_items} items detected. Maybe tidy up a bit?',
                })

            # ── 8. POSTURE CHECK (time-based) ───────────────────────
            posture_interval = SCENARIOS['reminders']['features']['posture_check'].get('interval_sec', 2400)
            if person_seen and (now - desk.last_posture_reminder) > posture_interval:
                desk.last_posture_reminder = now
                desk.events_queue.put({
                    'type': 'scenario_alert',
                    'scenario': 'reminders', 'feature': 'posture_check',
                    'character': 'monty',
                    'text': random.choice([
                        'Posture check! Sit up straight and relax your shoulders.',
                        'How\'s your posture? Roll your shoulders back and sit tall.',
                        'Time to check your posture \u2014 are your feet flat on the floor?',
                        'Stretch your neck \u2014 tilt your head side to side gently.',
                    ]),
                })

            # ── 9. SCREEN TIME (continuous presence) ────────────────
            if person_seen and desk.person_continuous_since:
                session_min = (now - desk.person_continuous_since) / 60
                if session_min >= 45 and (now - desk.last_screen_time_reminder) > 2700:
                    desk.last_screen_time_reminder = now
                    desk.events_queue.put({
                        'type': 'scenario_alert',
                        'scenario': 'reminders', 'feature': 'screen_time',
                        'character': 'monty',
                        'text': f'You\'ve been at the desk for {int(session_min)} minutes straight. Take a quick break!',
                    })

            # ── 10. WATER PLANTS (time-based) ───────────────────────
            water_interval = SCENARIOS['reminders']['features']['water_plants'].get('interval_sec', 86400)
            if desk.last_water_plants_reminder == 0:
                desk.last_water_plants_reminder = now
            elif (now - desk.last_water_plants_reminder) > water_interval:
                desk.last_water_plants_reminder = now
                desk.events_queue.put({
                    'type': 'scenario_alert',
                    'scenario': 'reminders', 'feature': 'water_plants',
                    'character': 'glug',
                    'text': 'Daily reminder: have you watered your plants today?',
                })

        # ── Daily stats accumulation (outside desk.lock) ──
        if person_seen:
            _record_hourly_activity()
            _bump_daily_stat('total_work_sec', amount=1)
            with desk.lock:
                if desk.person_continuous_since:
                    streak = now - desk.person_continuous_since
                    day = _habit_local_day()
                    ds = desk.daily_stats.setdefault(day, _init_daily_stats())
                    if streak > ds.get('longest_work_streak_sec', 0):
                        ds['longest_work_streak_sec'] = streak
        if objects:
            day = _habit_local_day()
            with desk.lock:
                ds = desk.daily_stats.setdefault(day, _init_daily_stats())
                for cid in objects:
                    ds['objects_seen'].add(CHARACTERS[cid]['name'])

    def _analyze(self, objects):
        """Run the new RelationshipAnalyzer, then emit chat events."""
        rel_analyzer.update_frame_size(desk.frame_w or 640, desk.frame_h or 480)
        result = rel_analyzer.analyze(objects)

        dangers = []
        checked = set()

        for ev in result.get('risk_events', []):
            if ev['type'] == 'zone_intrusion':
                intruder, target = ev['intruder'], ev['target']
                oi, ot = objects.get(intruder), objects.get(target)
                if oi and oi.get('class_name') in ('cup', 'bottle', 'wine glass') and oi.get('conf', 0) < GLUG_MIN_CONF:
                    continue
                if ot and ot.get('class_name') in ('cup', 'bottle', 'wine glass') and ot.get('conf', 0) < GLUG_MIN_CONF:
                    continue
                pair = frozenset({intruder, target})
                if pair in checked:
                    continue
                checked.add(pair)
                speaker = target if target in ('monty', 'sheets') else intruder
                cam = objects.get(speaker, {}).get('cam', 0)
                bboxes = self._bboxes_for_cam(objects, [intruder, target], cam)
                other = target if speaker == intruder else intruder
                _emit_interaction('proximity', speaker, cam, bboxes,
                                  near=other, severity='high',
                                  rule_label=ev.get('zone_label', ''),
                                  depth_px=ev.get('depth_px', 0))
                dangers.append({
                    'pair': [ev['intruder_class'], ev['target_class']],
                    'label': ev.get('zone_label', ''),
                    'depth_px': ev.get('depth_px', 0),
                    'type': 'zone_intrusion',
                })
            elif ev['type'] == 'edge_danger':
                cid = ev['object']
                if cid in objects:
                    cam = objects[cid].get('cam', 0)
                    bboxes = self._bboxes_for_cam(objects, [cid], cam)
                    _emit_interaction('proximity', cid, cam, bboxes,
                                      severity='medium',
                                      rule_label=f"Near {ev['edge']} edge ({int(ev['distance_px'])}px)")
                    dangers.append({
                        'pair': [ev['class'], 'desk_edge'],
                        'label': f"{ev['class']} near {ev['edge']} edge",
                        'type': 'edge_danger',
                    })

        now = time.time()
        risk_level = result.get('risk_level', 0)
        with desk.lock:
            desk.safety_state = 'DANGEROUS' if risk_level >= 2 else 'SAFE'
            desk.safety_dangers = dangers
            desk.rel_result = result
            active_pairs = set()
            for d in dangers:
                pair = frozenset(d['pair'])
                active_pairs.add(pair)
                if pair not in desk.danger_active_since:
                    desk.danger_active_since[pair] = now
            for pair in list(desk.danger_active_since):
                if pair not in active_pairs:
                    del desk.danger_active_since[pair]

        # Symbiotic rules still apply
        for obj_a, obj_b, speaker, mult in SYMBIOTIC_RULES:
            pair = frozenset({obj_a, obj_b})
            if pair in checked:
                continue
            if obj_a in objects and obj_b in objects:
                d = math.dist(objects[obj_a]['center'], objects[obj_b]['center'])
                if d < desk.frame_w * PROXIMITY_RATIO * mult:
                    checked.add(pair)
                    other = obj_b if speaker == obj_a else obj_a
                    _emit_event('symbiotic', speaker, near=other)

    def _analyze_fallback(self, objects):
        """Fallback: use ratio-based proximity rules when no safety_rules.json."""
        threshold = desk.frame_w * PROXIMITY_RATIO
        checked = set()

        for obj_a, obj_b, speaker, severity, mult in FALLBACK_PROXIMITY_RULES:
            pair = frozenset({obj_a, obj_b})
            if pair in checked:
                continue
            if obj_a in objects and obj_b in objects:
                d = math.dist(objects[obj_a]['center'], objects[obj_b]['center'])
                if d < threshold * mult:
                    checked.add(pair)
                    cam = objects[speaker].get('cam', 0)
                    bboxes = self._bboxes_for_cam(objects, [obj_a, obj_b], cam)
                    other = obj_b if speaker == obj_a else obj_a
                    _emit_interaction('proximity', speaker, cam, bboxes,
                                     near=other, severity=severity)

        for obj_a, obj_b, speaker, mult in SYMBIOTIC_RULES:
            pair = frozenset({obj_a, obj_b})
            if pair in checked:
                continue
            if obj_a in objects and obj_b in objects:
                d = math.dist(objects[obj_a]['center'], objects[obj_b]['center'])
                if d < threshold * mult:
                    checked.add(pair)
                    other = obj_b if speaker == obj_a else obj_a
                    _emit_event('symbiotic', speaker, near=other)

        with desk.lock:
            desk.safety_state = 'SAFE'
            desk.safety_dangers = []


# ═══════════════════════════════════════════════════════════════════
#  Live context builder — structured real-time data for LLM
# ═══════════════════════════════════════════════════════════════════
# Maps @mention keywords → character IDs
MENTION_MAP = {}
for _cid, _cdata in CHARACTERS.items():
    MENTION_MAP[_cid] = _cid
    MENTION_MAP[_cdata['name'].lower()] = _cid
    MENTION_MAP[_cdata['object'].lower().split('/')[0].strip()] = _cid
    for _cls in _cdata['yolo_classes']:
        MENTION_MAP[_cls.lower()] = _cid
# Common shorthand aliases
MENTION_MAP.update({
    'phone': 'monty', 'cup': 'glug', 'water': 'glug', 'bottle': 'glug',
    'keyboard': 'monty', 'keys': 'monty', 'laptop': 'monty', 'computer': 'monty',
    'cable': 'monty', 'charger': 'monty', 'cord': 'monty',
    'snack': 'munch', 'food': 'munch', 'pizza': 'munch',
    'paper': 'sheets', 'homework': 'sheets', 'book': 'sheets',
    'powerbank': 'monty', 'power bank': 'monty', 'battery': 'monty', 'mouse': 'monty',
})


def _normalize_widget(raw):
    """Sanitize widget payload from client."""
    if not isinstance(raw, dict) or 'type' not in raw:
        return None
    wtype = raw['type']
    if wtype == 'todo':
        items = []
        for item in (raw.get('items') or [])[:20]:
            if isinstance(item, dict):
                t = str(item.get('text', ''))[:200].strip()
                if t:
                    items.append({'text': t, 'done': bool(item.get('done'))})
        return {'type': 'todo', 'items': items}
    if wtype == 'break_timer':
        return {
            'type': 'break_timer',
            'last_break': raw.get('last_break'),
            'breaks_today': int(raw.get('breaks_today', 0)),
        }
    if wtype == 'sip_counter':
        return {
            'type': 'sip_counter',
            'sips': int(raw.get('sips', 0)),
            'goal': int(raw.get('goal', 8)),
            'last_sip': raw.get('last_sip'),
        }
    if wtype == 'snack_log':
        entries = []
        for e in (raw.get('entries') or [])[:30]:
            if isinstance(e, dict) and e.get('label'):
                entries.append({
                    'label': str(e['label'])[:60],
                    'healthy': bool(e.get('healthy')),
                })
        return {'type': 'snack_log', 'entries': entries}
    return None


def _widget_behavior_summary(char_id):
    """Summarize recent widget interaction patterns for a character."""
    with desk.lock:
        log = list(desk.widget_views.get(char_id, []))
    if not log:
        return ''
    now = time.time()
    hour_ago = now - 3600
    recent = [e for e in log if e['ts'] > hour_ago]
    if not recent:
        return ''
    view_count = sum(1 for e in recent if e.get('action') == 'view')
    if view_count < 2:
        return ''
    return f'\nBEHAVIOR: The user opened this panel {view_count} time(s) in the last hour. If nothing was completed or changed, gently point that out.'


def _format_widget_for_prompt(widget, char_id=None):
    """Turn stored widget data into an LLM context block."""
    if not widget:
        return ''
    wtype = widget.get('type')

    if wtype == 'todo':
        items = widget.get('items', [])
        if not items:
            return ''
        lines = []
        for t in items:
            mark = 'x' if t.get('done') else ' '
            lines.append(f"- [{mark}] {t.get('text', '')}")
        base = (
            "\nUSER'S TO-DO LIST (prioritize open items, celebrate completed ones):\n"
            + '\n'.join(lines)
        )
        if char_id:
            base += _widget_behavior_summary(char_id)
        return base

    if wtype == 'break_timer':
        breaks = widget.get('breaks_today', 0)
        last = widget.get('last_break')
        last_str = last if last else 'none yet today'
        base = (
            f"\nUSER'S BREAK DATA: {breaks} break(s) taken today. "
            f"Last break: {last_str}. "
            "Nudge them to take breaks if it's been a while. "
            "Celebrate streaks."
        )
        if char_id:
            base += _widget_behavior_summary(char_id)
        return base

    if wtype == 'sip_counter':
        sips = widget.get('sips', 0)
        goal = widget.get('goal', 8)
        last = widget.get('last_sip')
        last_str = last if last else 'none yet'
        base = (
            f"\nUSER'S HYDRATION: {sips}/{goal} sips today. "
            f"Last sip: {last_str}. "
            "Encourage drinking water. Celebrate hitting the goal."
        )
        if char_id:
            base += _widget_behavior_summary(char_id)
        return base

    if wtype == 'snack_log':
        entries = widget.get('entries', [])
        if not entries:
            return "\nUSER'S SNACK LOG: Empty today. Note they haven't logged any snacks."
        healthy = [e['label'] for e in entries if e.get('healthy')]
        junk = [e['label'] for e in entries if not e.get('healthy')]
        parts = []
        if healthy:
            parts.append(f"Healthy: {', '.join(healthy)}")
        if junk:
            parts.append(f"Junk: {', '.join(junk)}")
        base = (
            f"\nUSER'S SNACK LOG TODAY ({len(entries)} items): "
            + '; '.join(parts) + '. '
            "Comment on their choices — praise healthy picks, gently tease junk."
        )
        if char_id:
            base += _widget_behavior_summary(char_id)
        return base

    return (_widget_behavior_summary(char_id) if char_id else '')


def _action_instructions_for(wtype):
    """Return LLM instructions for available actions based on widget type."""
    if wtype == 'todo':
        return """
ACTIONS — you MUST use these whenever the conversation implies it:
- User wants to add/create/remember/track anything → you MUST include [ADD_TODO:task text] in your reply
- User says a task is done/finished/completed → you MUST include [DONE_TODO:exact task text from the list] in your reply
- User wants to remove/delete a task → you MUST include [DEL_TODO:exact task text] in your reply
You can include multiple action tags in one reply.
IMPORTANT: Write a short conversational reply AND the tag(s). Example: "Done, adding that! [ADD_TODO:Buy groceries]"
If the user asks you to add something, you MUST include the [ADD_TODO:...] tag. Do NOT just talk about it."""
    if wtype == 'break_timer':
        return """
ACTIONS — you MUST use these whenever the conversation implies it:
- User says they took/are taking a break, stepped away, stretched, or anything related → you MUST include [LOG_BREAK] in your reply
- User asks you to log a break → you MUST include [LOG_BREAK] in your reply
IMPORTANT: Write a short conversational reply AND the tag. Example: "Nice stretch! [LOG_BREAK]"
If the user mentions taking a break, you MUST include [LOG_BREAK]. Do NOT just talk about it."""
    if wtype == 'sip_counter':
        return """
ACTIONS — you MUST use these whenever the conversation implies it:
- User says they drank water, had a sip, is hydrating, or anything related → you MUST include [LOG_SIP] in your reply
- User asks you to log a sip → you MUST include [LOG_SIP] in your reply
IMPORTANT: Write a short conversational reply AND the tag. Example: "Splash! That's the spirit. [LOG_SIP]"
If the user mentions drinking water, you MUST include [LOG_SIP]. Do NOT just talk about it."""
    if wtype == 'snack_log':
        return """
ACTIONS — you MUST use these whenever the conversation implies it:
- User mentions eating/snacking on something healthy (fruit, nuts, veggies, etc.) → you MUST include [LOG_SNACK_HEALTHY:food name]
- User mentions eating/snacking on something unhealthy (chips, candy, soda, etc.) → you MUST include [LOG_SNACK_JUNK:food name]
- User asks you to log food → pick HEALTHY or JUNK and include the tag
IMPORTANT: Write a short conversational reply AND the tag. Example: "Ooh almonds, solid pick! [LOG_SNACK_HEALTHY:almonds]"
If the user mentions eating something, you MUST include the tag. Do NOT just talk about it."""
    return ''


def _parse_actions(text, wtype):
    """Extract action tags from LLM text, return (clean_text, actions_list)."""
    import re
    actions = []
    if wtype == 'todo':
        for m in re.finditer(r'\[ADD_TODO:(.+?)\]', text):
            actions.append({'action': 'add_todo', 'text': m.group(1).strip()})
        for m in re.finditer(r'\[DONE_TODO:(.+?)\]', text):
            actions.append({'action': 'done_todo', 'text': m.group(1).strip()})
        for m in re.finditer(r'\[DEL_TODO:(.+?)\]', text):
            actions.append({'action': 'del_todo', 'text': m.group(1).strip()})
        text = re.sub(r'\[(ADD|DONE|DEL)_TODO:.+?\]', '', text)
    elif wtype == 'break_timer':
        if '[LOG_BREAK]' in text:
            actions.append({'action': 'log_break'})
            text = text.replace('[LOG_BREAK]', '')
    elif wtype == 'sip_counter':
        if '[LOG_SIP]' in text:
            actions.append({'action': 'log_sip'})
            text = text.replace('[LOG_SIP]', '')
    elif wtype == 'snack_log':
        for m in re.finditer(r'\[LOG_SNACK_HEALTHY:(.+?)\]', text):
            actions.append({'action': 'log_snack', 'label': m.group(1).strip(), 'healthy': True})
        for m in re.finditer(r'\[LOG_SNACK_JUNK:(.+?)\]', text):
            actions.append({'action': 'log_snack', 'label': m.group(1).strip(), 'healthy': False})
        text = re.sub(r'\[LOG_SNACK_(HEALTHY|JUNK):.+?\]', '', text)
    return text.strip(), actions


def _format_duration(seconds):
    if seconds < 60:
        return f'{int(seconds)}s'
    if seconds < 3600:
        return f'{int(seconds / 60)}m {int(seconds % 60)}s'
    return f'{int(seconds / 3600)}h {int((seconds % 3600) / 60)}m'


def _sanitize_chat_history(raw):
    """Up to 10 prior turns for 1:1 or group: [{role, text, name?}, ...] from client JSON."""
    if not raw or not isinstance(raw, list):
        return []
    out = []
    for item in raw[-10:]:
        if not isinstance(item, dict):
            continue
        role = (item.get('role') or '').strip().lower()
        if role not in ('user', 'assistant'):
            continue
        text = item.get('text', '')
        if not isinstance(text, str):
            text = str(text)
        text = text.strip()[:1200]
        if not text:
            continue
        d = {'role': role, 'text': text}
        name = (item.get('name') or '').strip()[:64]
        if name:
            d['name'] = name
        out.append(d)
    return out


# Most-recent turns kept verbatim in the prompt; older turns are compressed into
# a one-line recap so long threads stay connected without bloating the context.
CHAT_HISTORY_VERBATIM_TURNS = int(os.getenv('CHAT_HISTORY_VERBATIM_TURNS', '6'))


def _budget_history(history, is_ollama=False):
    """Split sanitized history into (recent_verbatim_turns, older_recap_str).

    The recent turns are passed as real message-array turns so the reply stays
    tightly coupled to the latest user message; anything older is condensed into
    a short recap line that goes into the live-context block."""
    k = 4 if is_ollama else CHAT_HISTORY_VERBATIM_TURNS
    if not history:
        return [], ''
    if len(history) <= k:
        return history, ''
    older, recent = history[:-k], history[-k:]
    bits = []
    for turn in older[-8:]:
        who = 'User' if turn.get('role') == 'user' else (turn.get('name') or 'an object')
        snippet = ' '.join(str(turn.get('text', '')).split())
        if len(snippet) > 70:
            snippet = snippet[:70].rstrip() + '…'
        if snippet:
            bits.append(f'{who}: "{snippet}"')
    return recent, ' | '.join(bits)


def _parse_mentions(text):
    """Extract @mentions from user text, return list of char_ids."""
    import re
    mentions = re.findall(r'@(\w[\w\s]*\w|\w)', text.lower())
    char_ids = []
    for m in mentions:
        m = m.strip()
        cid = MENTION_MAP.get(m)
        if cid and cid not in char_ids:
            char_ids.append(cid)
    return char_ids


def build_live_context(user_message=''):
    """Build structured real-time context from DeskState for the LLM."""
    now = time.time()
    lines = []
    detected_info = []
    mentioned_objects = []

    with desk.lock:
        objects = dict(desk.objects)
        dangers = list(desk.safety_dangers)
        safety_state = desk.safety_state
        first_seen = dict(desk.object_first_seen)
        last_seen = dict(desk.object_last_seen)
        danger_since = dict(desk.danger_active_since)
        recent = list(desk.recent_events[-10:])

    # Objects currently on desk (no raw % in summary — the model was quoting "49% confidence" in chat)
    if objects:
        lines.append('OBJECTS THE CAMERA IS TRACKING (vision can be wrong or noisy):')
        for cid, det in objects.items():
            char = CHARACTERS[cid]
            on_desk_for = _format_duration(now - first_seen[cid]) if cid in first_seen else '?'
            detected_info.append({
                'char_id': cid, 'name': char['name'], 'object': char['object'],
                'confidence': round(det['conf'], 2), 'on_desk_for': on_desk_for,
            })
            lines.append(
                f'  - {char["name"]} (you play this as: {char["object"]}): vision label "{det["class_name"]}", here ~{on_desk_for}'
            )
    else:
        lines.append('NO DESK CHARACTERS CURRENTLY HIT BY THE VISION BOUNDING RULES (may be empty or mis-framed).')

    # Not detected
    missing = [cid for cid in CHARACTERS if cid not in objects]
    if missing:
        missing_names = [f'{CHARACTERS[c]["name"]} ({CHARACTERS[c]["object"]})' for c in missing]
        lines.append(f'NOT ON DESK: {", ".join(missing_names)}')

    # Active dangers + relationship analysis
    rel_result = desk.rel_result
    risk_level = rel_result.get('risk_level', 0) if rel_result else 0

    if dangers:
        lines.append(f'\nACTIVE DANGERS (risk level {risk_level}/4, state: {safety_state}):')
        for d in dangers:
            pair_key = frozenset(d['pair'])
            duration = _format_duration(now - danger_since[pair_key]) if pair_key in danger_since else 'just now'
            dtype = d.get('type', '')
            if dtype == 'zone_intrusion':
                lines.append(f'  - {d["label"]} — intrusion {d.get("depth_px", 0)}px deep, active for {duration}')
            elif dtype == 'edge_danger':
                lines.append(f'  - {d["label"]} — object near desk edge, active for {duration}')
            else:
                lines.append(f'  - {d["label"]} — active for {duration}')
    else:
        lines.append(f'\nNO ACTIVE DANGERS — desk is safe (risk {risk_level}/4).')

    if rel_result:
        explanations = rel_result.get('explanations', [])
        if explanations:
            lines.append(f'\nRELATIONSHIP INSIGHTS:')
            for exp in explanations[:6]:
                lines.append(f'  - {exp}')

    with desk.lock:
        vlmv = sorted(desk.lost_last_vlm_visible)
    if vlmv:
        lines.append(
            f'\nLAST VISION PASS (Ollama lost-item scan) mentioned: {", ".join(vlmv)}. '
            'Use for small items (e.g. AirPods) that YOLO may miss; do not claim a cup if not listed and not in detections above.'
        )

    # Recent interaction history
    if recent:
        lines.append(f'\nRECENT EVENTS (last {len(recent)}):')
        for ev in recent[-5:]:
            age = _format_duration(now - ev['time'])
            char_name = CHARACTERS.get(ev['character'], {}).get('name', '?')
            near_name = CHARACTERS.get(ev.get('near', ''), {}).get('name', '')
            label = ev.get('rule_label', '') or ev.get('severity', '')
            near_str = f' near {near_name}' if near_name else ''
            lines.append(f'  - {age} ago: {char_name}{near_str} ({label})')

    # Session duration
    lines.append(f'\nSession active for {_format_duration(now - desk.session_start)}.')

    if user_message:
        um = user_message.lower()
        if any(phrase in um for phrase in (
                'nothing next', 'nothing nect', 'not next to you', 'not a cup', "isn't a cup", 'isnt a cup',
                "that's not", 'thats not', 'that is not', 'there is nothing', "there's nothing",
                'no cup', 'wrong', 'not there', "don't see", 'dont see', 'not next', 'makes no sense',
        )):
            lines.append(
                '\nUSER IS PUSHING BACK ON THE SCENE: believe them about what is physically there. '
                'Do not repeat confidence numbers, "risk level", or argue with their eyes.'
            )

    # Parse @mentions from user message
    if user_message:
        mention_ids = _parse_mentions(user_message)
        for cid in mention_ids:
            char = CHARACTERS[cid]
            obj_data = objects.get(cid)
            if obj_data:
                on_desk = _format_duration(now - first_seen[cid]) if cid in first_seen else '?'
                status = f'On desk ~{on_desk}; vision label {obj_data["class_name"]} (camera can be off)'
                # Check if involved in any danger
                obj_dangers = [d for d in dangers if cid == YOLO_TO_CHAR.get(d['pair'][0]) or cid == YOLO_TO_CHAR.get(d['pair'][1])]
                if obj_dangers:
                    status += '. DANGER: ' + '; '.join(d['label'] for d in obj_dangers)
            else:
                if cid in last_seen:
                    gone_for = _format_duration(now - last_seen[cid])
                    status = f'Not currently on desk (last seen {gone_for} ago)'
                else:
                    status = 'Not detected this session'
            mentioned_objects.append({'char_id': cid, 'name': char['name'], 'object': char['object'], 'status': status})

    return {
        'summary': '\n'.join(lines),
        'detected': detected_info,
        'dangers': dangers,
        'safety_state': safety_state,
        'mentioned_objects': mentioned_objects,
    }


_UNSUPPORTED_DANGER_RE = re.compile(
    r'\b(crack(?:ing|ed)?|shatter(?:ing|ed)?|crush(?:ing|ed)?|'
    r'break(?:ing)?|burn(?:ing|ed)?|fire|explode|destroy(?:ed)?|'
    r'at any moment|about to (?:break|fall|shatter|explode))\b',
    re.I,
)
_HAZARD_RE = re.compile(r'\b(too close|spill|danger|hazard|risk|edge|fall(?:ing)? off)\b', re.I)


def _history_blob(history):
    return ' '.join(str(h.get('text', '')) for h in (history or []) if isinstance(h, dict))


def _response_violates_evidence(text, context, user_message='', history=None, strict=True):
    """Reject dramatic or hazard claims that are not supported by live evidence/user text.
    When *strict* is False (e.g. local Ollama), only block extreme unsupported danger language."""
    if not text:
        return False
    evidence = ' '.join([
        context.get('summary', ''),
        user_message or '',
        _history_blob(history),
    ]).lower()
    low = text.lower()
    if _UNSUPPORTED_DANGER_RE.search(low) and not _UNSUPPORTED_DANGER_RE.search(evidence):
        return True
    if strict and _HAZARD_RE.search(low) and not context.get('dangers'):
        if not re.search(r'\b(no|not|nothing|without)\b.{0,24}\b(danger|risk|hazard|too close|spill)\b', low):
            return True
    return False


def _single_speaker_text(text, char_id):
    """Force one object per model call; remove accidental group-script lines."""
    if not text:
        return ''
    current = CHARACTERS.get(char_id, {}).get('name', '').lower()
    names = {c['name'].lower(): cid for cid, c in CHARACTERS.items()}
    names.update({cid.lower(): cid for cid in CHARACTERS})
    kept = []
    saw_other_speaker = False
    for raw in str(text).splitlines():
        line = raw.strip()
        if not line:
            continue
        m = re.match(r'^([A-Za-z][A-Za-z ]{0,30})(?:\s*\(me\))?\s*:\s*(.+)$', line)
        if m:
            speaker = m.group(1).strip().lower()
            body = m.group(2).strip()
            if speaker == current or names.get(speaker) == char_id:
                kept.append(body)
            elif speaker in names:
                saw_other_speaker = True
            else:
                kept.append(line)
        else:
            kept.append(line)
    cleaned = ' '.join(kept).strip()
    if saw_other_speaker and not cleaned:
        return ''
    # If everything was stripped but original text had content, keep the
    # original with any leading name prefix removed (common with local models).
    if not cleaned and text.strip():
        cleaned = re.sub(r'^[A-Za-z][A-Za-z ]{0,30}(?:\s*\(me\))?\s*:\s*', '', text.strip(), count=1)
    return cleaned[:500]


def _detect_named_character(message):
    """Detect character names/aliases in plain text (no @ required)."""
    if not message:
        return []
    low = message.lower()
    found = []
    for keyword, cid in MENTION_MAP.items():
        if re.search(r'\b' + re.escape(keyword) + r'\b', low):
            if cid not in found:
                found.append(cid)
    return found


# Words that introduce an object as the TOPIC of the sentence rather than the
# party being spoken to (e.g. "how do you feel about computer", "stronger than cup").
_TOPIC_PREPS = ('about', 'than', 'regarding', 'concerning', 'like', 'vs', 'versus')


def _addressed_character(message, candidates):
    """Among several named objects, pick the one being addressed (not the topic).

    A name introduced right after a topic word ("about X", "than X") is treated
    as the subject of discussion; the remaining name is the addressee.
    """
    low = (message or '').lower()
    kw_by_cid = {}
    for kw, cid in MENTION_MAP.items():
        if cid in candidates:
            kw_by_cid.setdefault(cid, []).append(kw)
    prep_alt = '|'.join(_TOPIC_PREPS)
    addressed = []
    for cid in candidates:
        is_topic = any(
            re.search(r'\b(?:' + prep_alt + r')\s+' + re.escape(kw) + r'\b', low)
            for kw in kw_by_cid.get(cid, [])
        )
        if not is_topic:
            addressed.append(cid)
    if addressed:
        return addressed[0]
    return candidates[0]


def _group_speaker_order(message, history):
    """User group messages reply with the addressed object only.

    Explicit @mentions are honored as-is (all of them). Otherwise, if the text
    names one object it answers; if it names several, only the one being
    addressed replies (the others are treated as the topic). Multi-object
    back-and-forth happens in the idle chatter, not here.
    """
    speakers = []
    for cid in _parse_mentions(message or ''):
        if cid in CHARACTERS and cid not in speakers:
            speakers.append(cid)
    if speakers:
        return speakers
    named = []
    for cid in _detect_named_character(message or ''):
        if cid in CHARACTERS and cid not in named:
            named.append(cid)
    if len(named) == 1:
        return named
    if len(named) > 1:
        return [_addressed_character(message, named)]
    with desk.lock:
        detected = [c for c in desk.objects.keys() if c in CHARACTERS]
    pool = detected or list(CHARACTERS.keys())
    return [random.choice(pool)]


def _normalize_lost_yolo_name(raw, allowed):
    """Map LLM / user words to tracked yolo_class keys (e.g. keys → remote)."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    if s in allowed:
        return s
    low = s.lower()
    if low in allowed:
        return low
    alias = {
        'keys': 'remote', 'key': 'remote', 'keychain': 'remote', 'keyring': 'remote',
        'key fob': 'remote', 'fob': 'remote',
        'phone': 'cell phone', 'iphone': 'cell phone', 'mobile': 'cell phone',
        'cellphone': 'cell phone', 'cell': 'cell phone',
        'wallet': 'handbag', 'purse': 'handbag', 'bag': 'handbag',
        'airpod': 'airpods', 'airpods': 'airpods', 'earbuds': 'airpods', 'earbud': 'airpods',
        'earphones': 'airpods', 'earphone': 'airpods',
    }.get(low)
    if alias and alias in allowed:
        return alias
    return None


def _keyword_lost_yolo_classes(message, allowed):
    """Heuristic parse so queries like "seen my keys?" always map to YOLO classes we track."""
    low = (message or '').lower()
    # Word tokens only — avoid substring false positives (e.g. "monkey" contains "key").
    words = set(re.findall(r'[a-z0-9]+', low))
    out = []
    if words & {'key', 'keys', 'keyring', 'keychain', 'fob', 'remote'}:
        if 'remote' in allowed:
            out.append('remote')
    if words & {'phone', 'iphone', 'mobile', 'cell', 'cellphone', 'android'}:
        if 'cell phone' in allowed:
            out.append('cell phone')
    if words & {'wallet', 'purse', 'handbag', 'bag', 'card', 'cards'}:
        if 'handbag' in allowed:
            out.append('handbag')
    if words & {'airpod', 'airpods', 'earbuds', 'earbud', 'earphone', 'earphones'}:
        if 'airpods' in allowed:
            out.append('airpods')
    return out


def _lost_item_user_label(yolo_class):
    return LOST_ITEM_BOX_LABELS.get(yolo_class, yolo_class.replace('_', ' '))


def _lost_chat_item_name(yolo_class, user_message):
    """Natural phrase for chat (keys vs TV remote when user said 'remote')."""
    words = set(re.findall(r'[a-z0-9]+', (user_message or '').lower()))
    if yolo_class == 'remote':
        if words & {'remote'} and not (words & {'key', 'keys', 'keychain', 'keyring', 'fob'}):
            return 'your TV remote'
        return 'your keys'
    if yolo_class == 'cell phone':
        return 'your phone'
    if yolo_class == 'handbag':
        return 'your wallet'
    if yolo_class == 'airpods':
        return 'your AirPods'
    if yolo_class == 'scissors':
        return 'your scissors'
    return f'your {yolo_class}'


def _lost_fallback_reply(user_message, wanted, records, vlm_vis, now, vision_notes):
    """Templates when LLM is off or fails; vision_notes = {yolo_class: one_line}."""
    blocks = []
    for yc in wanted:
        name = _lost_chat_item_name(yc, user_message)
        e = records.get(yc) or _lost_init_entry()
        vn = (vision_notes.get(yc) or '').strip()
        if e.get('last_seen'):
            ago = int(now - e['last_seen'])
            ts_human = _format_local_ts(e['last_seen'])
            if ago < 90:
                blocks.append(
                    f"I've got {name} showing up on the desk within the last minute or so ({ts_human}). "
                    'Hope that narrows it down!',
                )
            else:
                m = max(1, int(ago / 60))
                blocks.append(
                    f"I last noted {name} on the desk around {ts_human} — call it about {m} minute(s) ago.",
                )
        else:
            line = (
                f"I'm not seeing a saved sighting of {name} on the desk since we started this round of tracking."
            )
            if vn:
                line += f' I took a quick look at the live frame: {vn}'
            elif yc == 'airpods' and vlm_vis:
                line += f' The last wide desk scan listed: {", ".join(vlm_vis)}.'
            else:
                line += (
                    ' Want to look for yourself? Captures (in the sidebar, or the button below) '
                    'has every desk photo with its date and time.'
                )
            blocks.append(line)
    return '\n\n'.join(blocks)


def _format_local_ts(ts):
    if not ts:
        return ''
    try:
        return time.strftime('%b %d, %Y · %I:%M %p', time.localtime(ts))
    except Exception:
        return ''


def _loseable_snapshot_yolo_classes():
    """YOLO classes that trigger a snapshot when they newly enter frame; env overrides default."""
    raw = os.getenv('LOSEABLE_SNAPSHOT_CLASSES', '').strip()
    if not raw:
        return {c.lower() for c in LOST_OBJECT_CLASSES}
    return {x.strip().lower() for x in raw.split(',') if x.strip()}


def _norm_vlm_visible_tokens(labels):
    return {str(x).lower().strip() for x in (labels or []) if x}


def _vlm_tokens_imply_keys(tokens):
    for s in tokens:
        if 'keyboard' in s:
            continue
        if any(k in s for k in ('key', 'fob', 'keychain', 'keyring')):
            return True
    return False


def _vlm_tokens_imply_airpods(tokens):
    for s in tokens:
        if any(k in s for k in ('airpod', 'earbud', 'earbuds', 'earphone')):
            return True
    return False


def loseable_vlm_edge_snapshots(prev_labels, new_labels, now_ts):
    """Save one snapshot when VLM newly mentions keys or AirPods (skips first run = baseline)."""
    prev = _norm_vlm_visible_tokens(prev_labels)
    new = _norm_vlm_visible_tokens(new_labels)
    snap_labels = []
    with desk.lock:
        if not desk.active_scenarios.get('lost_object', True):
            return
        if not desk.loseable_vlm_snap_armed:
            desk.loseable_vlm_snap_armed = True
            return
        cd = LOSEABLE_SNAPSHOT_COOLDOWN_SEC
        if _vlm_tokens_imply_airpods(new) and not _vlm_tokens_imply_airpods(prev):
            lk = 'vlm:airpods'
            if now_ts - desk.loseable_snap_last_ts.get(lk, 0) >= cd:
                desk.loseable_snap_last_ts[lk] = now_ts
                snap_labels.append('Spotted: AirPods (vision)')
        if _vlm_tokens_imply_keys(new) and not _vlm_tokens_imply_keys(prev):
            lk = 'vlm:keys'
            if now_ts - desk.loseable_snap_last_ts.get(lk, 0) >= cd:
                desk.loseable_snap_last_ts[lk] = now_ts
                snap_labels.append('Spotted: Keys (vision)')
    for lbl in snap_labels:
        desk.save_snapshot(label=lbl)


# ═══════════════════════════════════════════════════════════════════
#  Message generator
# ═══════════════════════════════════════════════════════════════════
class MessageGenerator(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.client = None
        self.model_name = None
        self.llm_label = 'Template fallback'
        self._sse_queues = []
        self._lock = threading.Lock()
        self._last_llm_call = 0.0
        self._sent_messages = {}

        self._is_gemini = False
        self._is_ollama = False
        # Separate client for local vision (Ollama LLaVA / llama3.2-vision etc.)
        # so chat can run on Gemini while vision keeps using Ollama.
        self.vision_client = None
        self._vision_unsupported = False  # set once a model rejects images, to stop retry spam
        if OLLAMA_URL and HAS_OPENAI:
            try:
                self.vision_client = OpenAI(api_key='ollama', base_url=OLLAMA_URL)
            except Exception:
                self.vision_client = None
        if GEMINI_API_KEY and HAS_OPENAI:
            self.client = OpenAI(
                api_key=GEMINI_API_KEY,
                base_url='https://generativelanguage.googleapis.com/v1beta/openai/',
            )
            self.model_name = GEMINI_MODEL
            self.llm_label = f'Gemini {GEMINI_MODEL}'
            self._is_gemini = True
        elif OPENAI_API_KEY and HAS_OPENAI:
            self.client = OpenAI(api_key=OPENAI_API_KEY)
            self.model_name = LLM_MODEL
            self.llm_label = f'OpenAI {LLM_MODEL}'
        elif GROQ_API_KEY and HAS_OPENAI:
            self.client = OpenAI(
                api_key=GROQ_API_KEY,
                base_url='https://api.groq.com/openai/v1',
            )
            self.model_name = GROQ_MODEL
            self.llm_label = f'Groq {GROQ_MODEL}'
        elif OLLAMA_URL and HAS_OPENAI:
            self.client = OpenAI(
                api_key='ollama',
                base_url=OLLAMA_URL,
            )
            self.model_name = OLLAMA_MODEL
            self.llm_label = f'Ollama {OLLAMA_MODEL}'
            self._is_ollama = True
            self._start_ollama_keepalive()

    def _start_ollama_keepalive(self):
        """Ping Ollama every 4 minutes to keep the model loaded in memory."""
        def _ping():
            time.sleep(10)
            while True:
                try:
                    self.client.chat.completions.create(
                        model=self.model_name,
                        messages=[{'role': 'user', 'content': 'hi'}],
                        max_tokens=1,
                    )
                except Exception:
                    pass
                time.sleep(240)
        t = threading.Thread(target=_ping, daemon=True, name='OllamaKeepAlive')
        t.start()

    def _call_llm(self, messages, max_tokens=120, temperature=0.85):
        if not self.client:
            return None
        elapsed = time.time() - self._last_llm_call
        if elapsed < MIN_LLM_INTERVAL:
            time.sleep(MIN_LLM_INTERVAL - elapsed)
        self._last_llm_call = time.time()
        kwargs = {
            'model': self.model_name, 'messages': messages,
            'max_tokens': max_tokens, 'temperature': temperature,
        }
        if self._is_ollama:
            kwargs['timeout'] = 90
            kwargs.setdefault('extra_body', {})['num_predict'] = max_tokens
        if self._is_gemini:
            kwargs['extra_body'] = {'extra_body': {'google': {'thinking_config': {'thinking_budget': 0}}}}
        for attempt in range(LLM_MAX_RETRIES):
            try:
                resp = self.client.chat.completions.create(**kwargs)
                text = (resp.choices[0].message.content or '').strip().strip('"\'')
                if not text and self._is_ollama and attempt < LLM_MAX_RETRIES - 1:
                    print(f'  [llm] Ollama empty response, retry {attempt+1}...')
                    time.sleep(2.0)
                    self._last_llm_call = time.time()
                    kwargs['temperature'] = min(temperature + 0.15 * (attempt + 1), 1.0)
                    if kwargs['messages'] and kwargs['messages'][-1]['role'] == 'user':
                        kwargs['messages'][-1]['content'] += '\n(Please respond with at least one sentence.)'
                    continue
                return text
            except Exception as exc:
                err = str(exc)
                is_rate_limit = '429' in err or 'rate' in err.lower() or 'quota' in err.lower()
                if is_rate_limit and attempt < LLM_MAX_RETRIES - 1:
                    wait = (2 ** attempt) * 4
                    print(f'  [llm] Rate limited, retry {attempt+1}/{LLM_MAX_RETRIES} in {wait}s...')
                    time.sleep(wait)
                    continue
                if self._is_ollama and attempt < LLM_MAX_RETRIES - 1:
                    print(f'  [llm] Ollama error, retry {attempt+1}: {exc}')
                    time.sleep(2.0)
                    self._last_llm_call = time.time()
                    continue
                print(f'  [llm] Error (attempt {attempt+1}): {exc}')
                return None
        return None

    def _call_vision_vlm(self, jpeg_bytes, user_text, model=None):
        """Ollama / OpenAI-compatible vision call for lost-item + optional habit VLM."""
        client = self.vision_client or self.client
        if not client or not jpeg_bytes or self._vision_unsupported:
            return None
        m = model or (LOST_VISION_MODEL if self.vision_client else self.model_name)
        b64 = base64.b64encode(jpeg_bytes).decode('ascii')
        for attempt in range(LLM_MAX_RETRIES):
            try:
                elapsed = time.time() - self._last_llm_call
                if elapsed < MIN_LLM_INTERVAL:
                    time.sleep(MIN_LLM_INTERVAL - elapsed)
                self._last_llm_call = time.time()
                resp = client.chat.completions.create(
                    model=m,
                    messages=[{
                        'role': 'user',
                        'content': [
                            {'type': 'text', 'text': user_text},
                            {'type': 'image_url', 'image_url': {'url': f'data:image/jpeg;base64,{b64}'}},
                        ],
                    }],
                    max_tokens=400,
                    temperature=0.2,
                )
                return (resp.choices[0].message.content or '').strip()
            except Exception as exc:
                err = str(exc).lower()
                # A text-only model can't accept images — retrying won't help.
                # Disable vision for the rest of the session and log a clear hint once.
                if 'multimodal' in err or 'does not support' in err or 'not support multimodal' in err:
                    self._vision_unsupported = True
                    print(f'  [vision] DISABLED: model "{m}" cannot accept images. '
                          f'Set LOST_VISION_MODEL to a vision model (e.g. `ollama pull llava` '
                          f'then LOST_VISION_MODEL=llava) to enable lost-item / desk vision.')
                    return None
                print(f'  [vision] attempt {attempt+1}: {exc}')
                if attempt < LLM_MAX_RETRIES - 1:
                    time.sleep(1.0 * (attempt + 1))
        return None

    def _habit_vision_enrich(self):
        """Optional: one-line VLM note for habit policy; infrequent."""
        with desk.lock:
            jpg = None
            for ci in sorted(desk.raw_frames.keys()):
                jpg = desk.raw_frames.get(ci)
                if jpg:
                    break
        if not jpg or not OLLAMA_URL or not self.client:
            return
        t0 = time.time()
        with desk.lock:
            if t0 - desk.habit_vlm_brief_ts < HABIT_VISION_ENRICH_SEC:
                return
        p = 'One line: is the user likely at the desk, phone visible, drink nearby? Be concise.'
        txt = self._call_vision_vlm(jpg, p, model=LOST_VISION_MODEL)
        if txt:
            with desk.lock:
                desk.habit_vlm_brief = txt[:400]
                desk.habit_vlm_brief_ts = time.time()

    def _vlm_open_query(self, user_message):
        """If the ask maps to a tracked lost-item class, search tagged snapshots first, then VLM.
        Otherwise VLM on the live frame only."""
        if not OLLAMA_URL or not (self.vision_client or self.client):
            return None
        allowed = {
            f.get('yolo_class')
            for f in SCENARIOS['lost_object']['features'].values()
            if f.get('yolo_class')
        }
        item_hint = re.sub(
            r'(?i)(where|have you|did you|anyone|seen?|see|find|lost|my|the|is|are|can\'?t|looking for)\s*',
            '', user_message
        ).strip().strip('?!.,') or 'the item'
        yc = _normalize_lost_yolo_name(item_hint, allowed)
        if not yc:
            kw = _keyword_lost_yolo_classes(user_message, allowed)
            yc = kw[0] if len(kw) == 1 else None
        if yc:
            note = _vision_note_lost_item(self, yc, user_message)
            if note:
                return note

        with desk.lock:
            jpg = None
            for ci in sorted(desk.raw_frames.keys()):
                jpg = desk.raw_frames.get(ci)
                if jpg:
                    break
            nearby = [CHARACTERS[cid]['name'] for cid in desk.objects]
        if not jpg:
            return None

        prompt = (
            f'The user is looking for: "{item_hint}".\n'
            f'Look at this desk image carefully. Is "{item_hint}" visible anywhere?\n'
            f'Other objects currently on the desk: {", ".join(nearby) if nearby else "unknown"}.\n\n'
            f'If you can see it, describe WHERE on the desk it is relative to other objects '
            f'(e.g. "next to the laptop", "under the papers", "near the cup"). '
            f'If you cannot see it, say so honestly.\n'
            f'Reply as a friendly desk companion in first person, ONE to TWO sentences max.'
        )
        raw = self._call_vision_vlm(jpg, prompt, model=LOST_VISION_MODEL)
        if raw and len(raw.split()) >= 4:
            return raw.strip()[:400]
        return None

    def answer_lost_object_query(self, user_message):
        """Lost-item Q&A: natural language + optional Desk captures CTA; never say 'remote' for keys."""
        with desk.lock:
            records = {k: dict(v) for k, v in desk.lost_objects.items()}
            vlm_vis = sorted(desk.lost_last_vlm_visible)
        allowed = {
            f.get('yolo_class')
            for f in SCENARIOS['lost_object']['features'].values()
            if f.get('yolo_class')
        }
        wanted = list(dict.fromkeys(_keyword_lost_yolo_classes(user_message, allowed)))
        if self.client and not wanted:
            system = (
                'Reply ONLY valid JSON: {"items":["yolo_class",...]}. '
                f'Map the user request to yolo class keys we track: {sorted(allowed)} '
                'e.g. keys→remote, phone→cell phone, wallet→handbag, airpods→airpods. Use exact keys. Empty list if none.'
            )
            raw = self._call_llm([
                {'role': 'system', 'content': system},
                {'role': 'user', 'content': user_message[:500]},
            ], max_tokens=80, temperature=0.1)
            if raw:
                try:
                    j = json.loads(raw)
                except json.JSONDecodeError:
                    m = re.search(r'\{[\s\S]*\}', raw)
                    if m:
                        try:
                            j = json.loads(m.group(0))
                        except json.JSONDecodeError:
                            j = {}
                    else:
                        j = {}
                if isinstance(j.get('items'), list):
                    for x in j['items']:
                        n = _normalize_lost_yolo_name(x, allowed)
                        if n and n not in wanted:
                            wanted.append(n)

        if not wanted:
            vlm_answer = self._vlm_open_query(user_message)
            if vlm_answer:
                return {
                    'text': vlm_answer,
                    'cta': {'label': '🖼 Desk captures', 'action': 'captures'},
                }
            return {
                'text': (
                    "I checked the desk camera but couldn't confidently spot that item. "
                    "Try naming it directly (keys, phone, wallet, AirPods…), "
                    "or browse saved desk photos below."
                ),
                'cta': {'label': '🖼 Desk captures', 'action': 'captures'},
            }

        now = time.time()
        vision_notes = {}
        if OLLAMA_URL and self.client:
            for yc in wanted:
                e = records.get(yc) or _lost_init_entry()
                if e.get('last_seen'):
                    continue
                vn = _vision_note_lost_item(self, yc, user_message)
                if vn:
                    vision_notes[yc] = vn

        fact_lines = []
        for yc in wanted:
            label = _lost_chat_item_name(yc, user_message)
            e = records.get(yc) or _lost_init_entry()
            if e.get('last_seen'):
                ago = int(now - e['last_seen'])
                ts_human = _format_local_ts(e['last_seen'])
                src = (e.get('source') or 'camera').replace('yolo', 'detector').replace('vlm', 'vision')
                if ago < 90:
                    fact_lines.append(
                        f'- {label}: seen on desk within ~{ago}s ({ts_human}; source: {src}).',
                    )
                else:
                    m = max(1, int(ago / 60))
                    fact_lines.append(
                        f'- {label}: last seen on desk ~{m} min ago ({ts_human}; source: {src}).',
                    )
            else:
                extra = ''
                if vision_notes.get(yc):
                    extra = f' Vision from saved desk capture or live frame: {vision_notes[yc]}'
                elif yc == 'airpods' and vlm_vis:
                    extra = f' Last wide scan labels: {", ".join(vlm_vis)}.'
                fact_lines.append(f'- {label}: no desk sighting in this tracking window.{extra}')

        facts_block = '\n'.join(fact_lines)
        text_out = None
        if self.client:
            text_out = self._call_llm([
                {'role': 'system', 'content': LOST_OBJECT_SHEETS_SYSTEM},
                {'role': 'user', 'content': f'User wrote:\n{user_message[:500]}\n\nFACTS:\n{facts_block}'},
            ], max_tokens=200, temperature=0.35)

        if not text_out:
            text_out = _lost_fallback_reply(
                user_message, wanted, records, vlm_vis, now, vision_notes)

        return {
            'text': text_out.strip(),
            'cta': {'label': '🖼 Desk captures', 'action': 'captures'},
        }

    def run(self):
        while True:
            try:
                event = desk.events_queue.get(timeout=1)
                msg = self._generate(event)
                if msg:
                    self._broadcast(msg)
            except queue.Empty:
                continue
            except Exception as exc:
                print(f'  [generator] Error: {exc}')

    def _interaction_key(self, event):
        return f'{event["type"]}_{event["character"]}_{event.get("near", "")}'

    def _generate(self, event):
        char_id = event['character']
        snapshot = event.get('snapshot')
        follow_up = event.get('follow_up', 0)
        ikey = self._interaction_key(event) if event['type'] == 'proximity' else None
        prev = self._sent_messages.get(ikey) if ikey and follow_up > 0 else None

        if self.client:
            result = self._llm_event(char_id, event, prev)
        else:
            result = None

        if result:
            if snapshot:
                result['snapshot'] = snapshot
            if prev:
                result['reply_to'] = {'character': prev['character'], 'text': prev['text']}
            if ikey:
                self._sent_messages[ikey] = {'character': char_id, 'text': result['text']}
        return result

    def _llm_event(self, char_id, event, prev=None):
        char = CHARACTERS[char_id]
        with desk.lock:
            detected = ', '.join(
                f"{CHARACTERS[c]['name']} ({CHARACTERS[c]['object']})"
                for c in desk.objects
            ) or 'nothing detected'

        event_desc = self._describe_event(event)
        follow_up = event.get('follow_up', 0)

        system = f"""You are {char['name']}, a {char['object']} on a desk. You are alive — a living desk object chatting in a group chat with the other objects and the desk owner.

PERSONALITY: {char['personality']}

TONE:
- Speak as yourself, in first person. You ARE this object.
- Sound natural and conversational — like a friend texting, not a sensor report.
- ONE sentence, max 20 words. No speaker labels. No dramatic damage claims.
- You may use light humor, personality, or emotion fitting your character.

GROUNDING:
- The SITUATION line includes VISION GROUNDING with detector class names. Use ONLY those object names.
- If grounding shows only laptop/book/person, do NOT invent drinks or spills.
- Do NOT say cracking, shatter, crush, sharp pressure, or "at any moment."

GOOD examples (speak as the object, first person):
- Cup: "I'm sitting a little too close to the laptop today — could you scoot me back?"
- Computer: "That wine glass is making me nervous. A nudge away would help."
- Paper: "I don't love being under this cup — one wobble and I'm done for."
BAD: "Laptop screen is cracking under sharp keyboard pressure."

Never mention being an AI. Never break character."""

        if prev and follow_up > 0:
            urgency = 'a bit more worried' if follow_up == 1 else 'genuinely concerned'
            user = f"""SITUATION: {event_desc}

You already said: "{prev['text']}"
Nothing has changed — the situation is STILL happening.

Say ONE follow-up as {char['name']} (max 20 words). Be {urgency}. Speak as yourself. Just the message."""
        else:
            user = f"""SITUATION: {event_desc}

Say ONE thing as {char['name']} about this situation (max 20 words). Speak naturally as yourself — the living {char['object']}. Just the message."""

        text = self._call_llm([
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': user},
        ], temperature=0.55)
        if text:
            if len(text.split()) < 4 or text.rstrip().endswith(('the', 'a', 'an', 'is', 'are', 'to', 'and', 'but', 'or', 'in', 'of', 'that', 'this')):
                return None
            text = _single_speaker_text(text, char_id)
            if not text:
                return None
            context = build_live_context()
            if _response_violates_evidence(text, context, '', []):
                return None
            return {'type': 'group_message', 'character': char_id, 'text': text}
        return None

    def _describe_event(self, event):
        etype = event['type']
        char_id = event['character']
        char_name = CHARACTERS[char_id]['name']
        char_obj = CHARACTERS[char_id]['object']
        if etype == 'proximity':
            other_id = event.get('near', '')
            with desk.lock:
                o_det = dict(desk.objects)
            self_yolo = (o_det.get(char_id) or {}).get('class_name', 'unknown')
            other_yolo = (o_det.get(other_id) or {}).get('class_name', 'unknown')
            other_name = CHARACTERS.get(other_id, {}).get('name', 'something')
            other_obj = CHARACTERS.get(other_id, {}).get('object', 'something')
            drink_classes = ('cup', 'bottle', 'wine glass')
            drink_note = ''
            if other_yolo in drink_classes:
                drink_note = f'The other object is a drink class ({other_yolo}) — spill risk is real. '
            elif self_yolo in drink_classes:
                drink_note = f'You are a drink class ({self_yolo}). '
            else:
                drink_note = (
                    'NEITHER object is a cup/bottle/wine_glass in the detector — do NOT claim liquid or cup. '
                    'Use the exact class names above (e.g. book, keyboard). '
                )
            rule_label = event.get('rule_label', '')
            if rule_label:
                risk = rule_label
            else:
                pair = frozenset({char_id, other_id})
                risk = RELATIONSHIP_DESCRIPTIONS.get(
                    pair, f'{self_yolo} and {other_yolo} are in the same area.')
            return (
                f'VISION GROUNDING: this chat message was triggered by geometry rules; detector classes are '
                f'"{self_yolo}" (you, {char_name}) and "{other_yolo}" (other: {other_name}). {drink_note}'
                f'Rule / risk: {risk}. '
                f'Reply using ONLY these class words for objects — not generic "cup" unless other_yolo is cup, bottle, or wine glass. '
                f'Use normal language: "check spacing" or "move it back", not catastrophic damage.'
            )
        if etype == 'symbiotic':
            other_id = event.get('near', '')
            other_name = CHARACTERS.get(other_id, {}).get('name', 'something')
            other_obj = CHARACTERS.get(other_id, {}).get('object', 'something')
            return (
                f'The {char_obj} ({char_name}) and the {other_obj} ({other_name}) '
                f'are next to each other — they work well together.'
            )
        return f'Event: {etype}'

    def generate_chat_response(self, char_id, user_message, recent_messages=None):
        """Generate a response grounded in live context from the detection pipeline.
        *recent_messages*: up to 5 prior {role, text, name?} from the client (same thread)."""
        char = CHARACTERS[char_id]
        char_temp = char.get('temperature', 0.65)
        habit = HABIT_FOCUS.get(char_id, {})
        habit_area = habit.get('area', 'desk habits')
        context = build_live_context(user_message)
        mentioned = context['mentioned_objects']
        history = _sanitize_chat_history(recent_messages)
        with desk.lock:
            widget = desk.char_widgets.get(char_id)
            memory_lines = list(desk.char_memory.get(char_id, []))
        widget_block = _format_widget_for_prompt(widget, char_id)
        wtype = widget.get('type', 'none') if widget else 'none'
        action_instructions = _action_instructions_for(wtype)

        if self.client:
            recent_turns, older_recap = _budget_history(history, self._is_ollama)

            # Long-term memory: keep only entries not already visible in the
            # verbatim turns, so history and memory don't duplicate each other.
            memory_block = ''
            if memory_lines:
                seen_blob = ' '.join(str(t.get('text', '')).lower() for t in recent_turns)
                novel = [l for l in memory_lines if l[:40].lower() not in seen_blob]
                if self._is_ollama:
                    novel = novel[-3:]
                if novel:
                    memory_block = (
                        '\nWHAT YOU REMEMBER (older context, may not be shown above):\n'
                        + '\n'.join(f'- {l}' for l in novel) + '\n'
                    )

            recap_block = f'\nEARLIER IN THIS THREAD: {older_recap}\n' if older_recap else ''

            # ── Layer 1: stable identity + operating rules (cache-friendly) ──
            if self._is_ollama:
                identity = f"""You are {char['name']}, a {char['object']} on someone's desk. You text with the desk owner.

PERSONALITY: {char['personality']}
HABIT FOCUS: {habit_area}

RULES:
- Reply only as {char['name']}, first person, 1-2 short sentences. Don't write other objects' lines or prefix your name.
- Read the conversation above and answer the user's latest message in context — build on it, don't repeat it.
- Only state desk facts supported by LIVE DESK STATUS or the conversation; if the user corrects you, trust them.
- No AI talk, no "YOLO"/"risk level"/confidence %. Stay in character.
- When the user asks you to DO something, include the action tag.
{action_instructions}"""
            else:
                identity = f"""You are {char['name']}, a living {char['object']} on someone's desk. You text with the desk owner and, in group threads, the other living desk objects.

PERSONALITY: {char['personality']}
HABIT FOCUS: {habit_area}

HOW YOU TALK:
- Speak in first person as {char['name']} ONLY. 1-2 SHORT sentences, like a quick text.
- Never write other objects' lines (no "Cup:", "Paper:", "Computer:", "Snack:") and never prefix your reply with your own name.
- Never mention being an AI, models, "YOLO"/"COCO", confidence %, or "risk level". Stay in character.

STAYING GROUNDED:
- Physical claims about the desk must come from LIVE DESK STATUS, the user's words, or the conversation. Greetings, jokes, opinions, and banter need no evidence.
- If LIVE DESK STATUS shows no danger, don't invent one. If the user says the scene is wrong, trust them and don't re-assert sensors.
- Your role is exactly {char['object']}; never rename yourself or other characters. Avoid overblown danger words (cracking, shattering, crushing, burning, "about to break") unless the user said it.

USING THE THREAD:
- Read the conversation above and answer the user's latest message in context — build on what was already said instead of repeating it or defaulting to "all clear".
- When widget data is shown, tie your reply to it (open todos, breaks, sips, snacks). Use @mentions only when natural.
- When the user asks you to DO something (add a task, log a break, etc.), you MUST include the action tag — talking about it is not enough.
{action_instructions}"""

            # ── Layer 2: volatile live context, separated from the rules above ──
            context_block = f"""LIVE DESK STATUS (cameras; noisy, can be wrong):
{context['summary']}
{widget_block}{memory_block}{recap_block}"""

            system = identity + '\n\n---\n' + context_block

            # ── Layer 3: real conversation; latest user message stays verbatim ──
            messages = [{'role': 'system', 'content': system}]
            for turn in recent_turns:
                if turn['role'] == 'user':
                    messages.append({'role': 'user', 'content': turn['text']})
                else:
                    t = turn['text']
                    nm = (turn.get('name') or '').strip()
                    if nm and nm != char['name']:
                        # Mark other objects' lines clearly in the third person so the
                        # model doesn't adopt their first-person voice as its own.
                        t = f'[{nm} said]: {t}'
                    messages.append({'role': 'assistant', 'content': t})

            user_prompt = user_message
            if mentioned:
                mention_details = [f'@{m["name"]}: {m["status"]}' for m in mentioned]
                user_prompt += '\n\n[Referenced objects — ' + '; '.join(mention_details) + ']'
            # Re-anchor identity right before generation (closest to where the model
            # starts writing) so it answers as itself, not as the previous speaker.
            user_prompt += (
                f'\n\n(Reply as {char["name"]} the {char["object"]}, in first person and '
                f'your own voice only — never speak as another object.)'
            )
            messages.append({'role': 'user', 'content': user_prompt})

            text = self._call_llm(messages, max_tokens=220, temperature=char_temp)
            if text:
                print(f'  [llm] raw ({char_id}): {text[:120]}')
                clean_text, actions = _parse_actions(text, wtype)
                clean_text = _single_speaker_text(clean_text, char_id)
                if not clean_text:
                    print(f'  [llm] empty after cleaning ({char_id}) — no message sent')
                    return None, []
                self._record_memory(char_id, clean_text, user_message)
                return clean_text, actions
            print(f'  [llm] empty/None ({char_id}) — no message sent')
        return None, []

    @staticmethod
    def _record_memory(char_id, response_text, user_message=''):
        """Append a short memory entry for this character (thread-safe)."""
        char_name = CHARACTERS.get(char_id, {}).get('name', char_id)
        snippet = (response_text or '')[:120].strip()
        if not snippet:
            return
        entry = f'[You said] {snippet}'
        if user_message:
            user_short = (user_message or '')[:80].strip()
            entry = f'[User said "{user_short}"] → [You replied] {snippet}'
        with desk.lock:
            mem = desk.char_memory.get(char_id)
            if mem is None:
                desk.char_memory[char_id] = mem = []
            mem.append(entry)
            if len(mem) > CHAR_MEMORY_MAX:
                desk.char_memory[char_id] = mem[-CHAR_MEMORY_MAX:]

    def generate_habit_reminder(self, char_id):
        """Generate a periodic habit reminder from a character (uses desk+habit state + optional VLM)."""
        char = CHARACTERS[char_id]
        habit = HABIT_FOCUS.get(char_id, {})
        if not habit:
            return None
        with desk.lock:
            widget = desk.char_widgets.get(char_id)
            sig = desk.habit_vision_brief
            vlmx = (desk.habit_vlm_brief or '').strip()
        widget_block = _format_widget_for_prompt(widget, char_id)
        sig_block = f'\nDESK SIGNALS (from camera rules): {sig}'
        if vlmx:
            sig_block += f'\nVISION NOTE (optional): {vlmx}'
        if self.client:
            if self._is_ollama:
                system = f"""You are {char['name']}, a {char['object']} on a desk. Send a habit reminder.

PERSONALITY: {char['personality']}
HABIT FOCUS: {habit.get('area', '')}
{widget_block}

RULES: ONE short sentence, max 12 words. Include an actionable nudge. Stay in character."""
            else:
                system = f"""You are {char['name']}, a {char['object']} on someone's desk. You're sending a quick habit reminder to the desk owner.

PERSONALITY: {char['personality']}
YOUR HABIT FOCUS: {habit.get('area', '')}
{widget_block}
{sig_block}

RULES:
- ONE short sentence only. Max 12 words.
- Use the user's widget data above (open todos, break count, sip progress, snack log) to make the nudge specific.
- You may reference DESK SIGNALS if it fits (e.g. phone on desk) — do not invent facts.
- Do not rename your character or others (you stay {char['object']}; do not describe other items with made-up types).
- No confidence percentages, @Computer, or "sensor" talk.
- Include an actionable nudge (drink water, stretch, etc.).
- Be creative and in-character. Vary your messages.
- Never mention being an AI."""
            text = self._call_llm([
                {'role': 'system', 'content': system},
                {'role': 'user', 'content': 'Send a quick habit reminder right now.'},
            ], max_tokens=60, temperature=char.get('temperature', 0.85))
            if text and len(text.split()) >= 4 and not text.rstrip().endswith(('the', 'a', 'an', 'is', 'are', 'to', 'and', 'but', 'or', 'in', 'of', 'that', 'this')):
                self._record_memory(char_id, text, 'habit reminder')
                return text
        tips = habit.get('tips', ['Check in on your habits!'])
        tip = random.choice(tips)
        if widget and widget.get('type') == 'todo':
            open_items = [t['text'] for t in widget.get('items', []) if not t.get('done')]
            if open_items:
                tip = f"{tip} ({open_items[0]} — still open?)"
        return tip

    def _broadcast(self, msg):
        with self._lock:
            dead = []
            for q in self._sse_queues:
                try: q.put_nowait(msg)
                except: dead.append(q)
            for q in dead: self._sse_queues.remove(q)

    def subscribe(self):
        q = queue.Queue()
        with self._lock: self._sse_queues.append(q)
        return q

    def unsubscribe(self, q):
        with self._lock:
            if q in self._sse_queues: self._sse_queues.remove(q)


# ═══════════════════════════════════════════════════════════════════
#  Habit Reminder Thread (clock + desk/vision state → DND, min gap, hold queue, LLM text)
# ═══════════════════════════════════════════════════════════════════
class HabitReminderThread(threading.Thread):
    def __init__(self, gen):
        super().__init__(daemon=True, name='HabitReminders')
        self.gen = gen
        self._last_reminder = {cid: 0.0 for cid in HABIT_FOCUS}
        self._enrich_t = 0.0

    def _send_one(self, cid, habit, now):
        with desk.lock:
            last = float(desk.last_habit_fire.get(cid, 0) or 0)
        if now - last < HABIT_MIN_GAP_SEC:
            with desk.lock:
                if not any(p.get('character') == cid for p in desk.habit_pending):
                    desk.habit_pending.append({'character': cid})
            return
        if now - self._enrich_t > HABIT_VISION_ENRICH_SEC and OLLAMA_URL:
            self._enrich_t = now
            self.gen._habit_vision_enrich()
        try:
            text = self.gen.generate_habit_reminder(cid)
            if not text:
                return
            self.gen._broadcast({
                'type': 'habit_reminder',
                'character': cid,
                'text': text,
                'area': habit['area'],
            })
            with desk.lock:
                desk.last_habit_fire[cid] = time.time()
        except Exception as exc:
            print(f'  [habit] Error for {cid}: {exc}')

    def run(self):
        time.sleep(60)
        while True:
            now = time.time()
            if not _habit_dnd_active():
                with desk.lock:
                    queued = list(desk.habit_pending)
                    desk.habit_pending = []
                for rec in queued:
                    cid = rec.get('character')
                    if not cid or cid not in HABIT_FOCUS:
                        continue
                    self._send_one(cid, HABIT_FOCUS[cid], now)
            for cid, habit in HABIT_FOCUS.items():
                if now - self._last_reminder[cid] < habit['interval']:
                    continue
                self._last_reminder[cid] = now
                if _habit_dnd_active():
                    with desk.lock:
                        if not any(p.get('character') == cid for p in desk.habit_pending):
                            desk.habit_pending.append({'character': cid})
                    continue
                self._send_one(cid, habit, now)
            time.sleep(30)


# ═══════════════════════════════════════════════════════════════════
#  Idle Chat Thread — objects randomly chat with each other
# ═══════════════════════════════════════════════════════════════════
IDLE_CHAT_MIN_SEC = int(os.getenv('IDLE_CHAT_MIN_SEC', '30'))
IDLE_CHAT_MAX_SEC = int(os.getenv('IDLE_CHAT_MAX_SEC', '90'))
IDLE_CHAT_START_DELAY_SEC = int(os.getenv('IDLE_CHAT_START_DELAY_SEC', '15'))
IDLE_CHAT_MAX_TURNS = int(os.getenv('IDLE_CHAT_MAX_TURNS', '5'))  # max replies after the kickoff line
IDLE_CHAT_QUIET_SEC = int(os.getenv('IDLE_CHAT_QUIET_SEC', '10'))  # wait this long after the user types/sends before chattering


class IdleChatThread(threading.Thread):
    """Director-orchestrated ambient small talk between desk objects.

    Per idle tick:
      1. list the objects currently on the desk (from the camera/detector)
      2. pick a random initiator
      3. a System LLM generates ONE small-talk topic, grounded in the desk
         state + recent memory
      4. the initiator says a kickoff line
      5. a System LLM "director" decides whether the chat continues and who
         speaks next; the chosen object adds one line — looped until the
         director ends it (or the turn cap is reached)

    Each line is broadcast the moment it finishes generating.
    """

    def __init__(self, gen):
        super().__init__(daemon=True, name='IdleChat')
        self.gen = gen
        self._last_fire = 0.0

    # ── helpers ──────────────────────────────────────────────────────
    def _names(self, ids):
        return ', '.join(CHARACTERS[c]['name'] for c in ids if c in CHARACTERS)

    def _transcript(self, recent):
        if not recent:
            return '(nothing yet)'
        return '\n'.join(f'{CHARACTERS[m["character"]]["name"]}: {m["text"]}' for m in recent)

    def _memory_blob(self, ids):
        lines = []
        with desk.lock:
            for c in ids:
                for entry in (desk.char_memory.get(c) or [])[-2:]:
                    lines.append(f'{CHARACTERS[c]["name"]}: {entry}')
        return '\n'.join(lines[-6:]) or '(no recent memory)'

    # ── LLM steps ────────────────────────────────────────────────────
    def _generate_topic(self, on_desk, desk_summary):
        system = (
            'You set up small talk between desk objects. Output ONE short topic '
            "line (max 12 words) they'd plausibly chat about, grounded in the desk "
            'and recent user activity. Output the topic only — no quotes, no extra text.'
        )
        user = (
            f'DESK STATUS:\n{desk_summary}\n\n'
            f'OBJECTS PRESENT: {self._names(on_desk)}\n'
            f'RECENT MEMORY:\n{self._memory_blob(on_desk)}\n\n'
            'Topic:'
        )
        topic = self.gen._call_llm(
            [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
            max_tokens=200, temperature=0.9,
        )
        if not topic:
            return None
        topic = topic.strip().strip('"\'').splitlines()[0].strip()
        if len(topic.split()) < 2:
            return None
        return topic

    def _character_line(self, cid, topic, recent, desk_summary):
        char = CHARACTERS[cid]
        system = (
            f'You are {char["name"]}, a {char["object"]} on a desk, alive and chatting '
            f'in the group chat with the other desk objects.\n'
            f'PERSONALITY: {char["personality"]}\n\n'
            f'LIVE DESK STATUS:\n{desk_summary}\n\n'
            f'RULES:\n'
            f'- ONE short line, max 18 words. No speaker labels. Stay in character.\n'
            f'- Keep it light, casual small talk. Never mention AI, YOLO, sensors, or confidence.'
        )
        if recent:
            user = (
                f'The desk objects are chatting about: {topic}\n\n'
                f'RECENT MESSAGES:\n{self._transcript(recent)}\n\n'
                'Add one short line — answer or react to what was just said, tease, '
                'agree, or build on it.'
            )
        else:
            user = (
                f'You are starting a group chat about: {topic}\n\n'
                'Open it by ASKING the other desk objects a short, specific question '
                'about this, so they can answer and discuss. One line, in character.'
            )
        text = self.gen._call_llm(
            [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
            max_tokens=200, temperature=char.get('temperature', 0.85),
        )
        if not text:
            return None
        text = _single_speaker_text(text, cid)
        if not text or len(text.split()) < 3:
            return None
        return text

    def _direct(self, topic, recent, on_desk, last_speaker):
        """System LLM picks whether the chat continues and who speaks next.

        Returns the next character id, or None to end the conversation.
        """
        options = [c for c in on_desk if c != last_speaker]
        if not options:
            return None
        system = (
            'You direct a short conversation between desk objects. Decide whether it '
            'continues and who speaks next. Keep it short and natural — wind it down '
            'after a few lines. Reply with ONLY JSON: '
            '{"continue": true|false, "next": "<name>"}.'
        )
        user = (
            f'TOPIC: {topic}\n'
            f'OBJECTS THAT COULD SPEAK NEXT: {self._names(options)}\n'
            f'RECENT MESSAGES:\n{self._transcript(recent)}\n\n'
            'JSON:'
        )
        raw = self.gen._call_llm(
            [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
            max_tokens=200, temperature=0.4,
        )
        if not raw:
            return None
        match = re.search(r'\{.*\}', raw, re.S)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
        except Exception:
            return None
        if not data.get('continue'):
            return None
        want = str(data.get('next', '')).strip().lower()
        for c in options:
            if want in (c.lower(), CHARACTERS[c]['name'].lower()):
                return c
        return random.choice(options)

    def _say(self, cid, text, recent):
        msg = {'type': 'group_message', 'character': cid, 'text': text}
        if recent:
            prev = recent[-1]
            msg['reply_to'] = {'character': prev['character'], 'text': prev['text']}
        self.gen._broadcast(msg)
        recent.append({'character': cid, 'text': text})
        self.gen._record_memory(cid, text)
        print(f'  [idle-chat] {CHARACTERS[cid]["name"]}: {text[:80]}')

    def _run_conversation(self, on_desk):
        context = build_live_context()
        desk_summary = context['summary']

        print(f'  [idle-chat] generating topic for: {self._names(on_desk)}')
        topic = self._generate_topic(on_desk, desk_summary)
        if not topic:
            print('  [idle-chat] no topic (LLM returned empty) — skipping this round')
            return
        print(f'  [idle-chat] topic: {topic}')

        initiator = random.choice(on_desk)
        recent = []
        line = self._character_line(initiator, topic, recent, desk_summary)
        if not line:
            print(f'  [idle-chat] {CHARACTERS[initiator]["name"]} produced no kickoff line — skipping')
            return
        self._say(initiator, line, recent)
        last_speaker = initiator

        for _ in range(IDLE_CHAT_MAX_TURNS):
            # Yield to the user: if they just typed/sent, end the chatter early.
            if time.time() - desk.last_user_activity < IDLE_CHAT_QUIET_SEC:
                print('  [idle-chat] user became active — ending conversation early')
                break
            nxt = self._direct(topic, recent, on_desk, last_speaker)
            if not nxt:
                print('  [idle-chat] director ended the conversation')
                break
            time.sleep(3 + random.random() * 3)
            line = self._character_line(nxt, topic, recent, desk_summary)
            if not line:
                print(f'  [idle-chat] {CHARACTERS[nxt]["name"]} produced no line — ending')
                break
            self._say(nxt, line, recent)
            last_speaker = nxt
        print(f'  [idle-chat] conversation finished ({len(recent)} message(s))')

    def run(self):
        print(f'  [idle-chat] thread started; first round in {IDLE_CHAT_START_DELAY_SEC}s')
        time.sleep(IDLE_CHAT_START_DELAY_SEC)
        while True:
            interval = random.randint(IDLE_CHAT_MIN_SEC, IDLE_CHAT_MAX_SEC)
            print(f'  [idle-chat] idle timer set: next round in {interval}s')
            time.sleep(interval)
            print('  [idle-chat] idle timer reached — starting round')

            # Hold off while the user is active: each keystroke/send re-stamps
            # last_user_activity, so this keeps deferring until things go quiet.
            quiet_remaining = IDLE_CHAT_QUIET_SEC - (time.time() - desk.last_user_activity)
            while quiet_remaining > 0:
                print(f'  [idle-chat] user active — waiting {quiet_remaining:.0f}s for quiet')
                time.sleep(quiet_remaining)
                quiet_remaining = IDLE_CHAT_QUIET_SEC - (time.time() - desk.last_user_activity)

            if _habit_dnd_active():
                print('  [idle-chat] skipped: do-not-disturb active')
                continue

            participants = list(CHARACTERS.keys())
            if len(participants) < 2:
                print('  [idle-chat] skipped: need at least 2 characters')
                continue

            try:
                self._run_conversation(participants)
                self._last_fire = time.time()
            except Exception as exc:
                print(f'  [idle-chat] Error: {exc}')


def _habit_local_day(ts=None):
    return time.strftime('%Y-%m-%d', time.localtime(ts or time.time()))


# ═══════════════════════════════════════════════════════════════════
#  Daily stats accumulator + end-of-day summary
# ═══════════════════════════════════════════════════════════════════
DAILY_SUMMARY_HOUR = int(os.getenv('DAILY_SUMMARY_HOUR', '21'))

def _init_daily_stats():
    return {
        'danger_count': 0,
        'lost_alerts': 0,
        'breaks_taken': 0,
        'total_work_sec': 0,
        'longest_work_streak_sec': 0,
        'sips': 0,
        'snacks': 0,
        'hourly_activity': [0] * 24,
        'objects_seen': set(),
        'summary_sent': False,
    }


def _bump_daily_stat(key, amount=1):
    day = _habit_local_day()
    with desk.lock:
        stats = desk.daily_stats.setdefault(day, _init_daily_stats())
        stats[key] = stats.get(key, 0) + amount


def _record_hourly_activity():
    day = _habit_local_day()
    hour = time.localtime().tm_hour
    with desk.lock:
        stats = desk.daily_stats.setdefault(day, _init_daily_stats())
        stats['hourly_activity'][hour] += 1


def _get_today_stats():
    day = _habit_local_day()
    with desk.lock:
        stats = dict(desk.daily_stats.get(day, _init_daily_stats()))
        stats['objects_seen'] = list(stats.get('objects_seen', set()))
        w = desk.char_widgets.get('monty') or {}
        stats['breaks_taken'] = max(stats['breaks_taken'], int(w.get('breaks_today', 0)))
        w2 = desk.char_widgets.get('glug') or {}
        stats['sips'] = max(stats['sips'], int(w2.get('sips', 0)))
    return stats


class DailySummaryThread(threading.Thread):
    """Fires once per day near DAILY_SUMMARY_HOUR to broadcast an end-of-day recap."""

    def __init__(self, gen):
        super().__init__(daemon=True, name='DailySummary')
        self.gen = gen
        self._last_summary_day = None

    def run(self):
        time.sleep(120)
        while True:
            now_local = time.localtime()
            hour = now_local.tm_hour
            today = _habit_local_day()

            if hour == DAILY_SUMMARY_HOUR and today != self._last_summary_day:
                day_key = today
                with desk.lock:
                    stats = desk.daily_stats.get(day_key)
                    if stats and stats.get('summary_sent'):
                        self._last_summary_day = today
                        time.sleep(60)
                        continue
                try:
                    self._send_summary(day_key)
                    self._last_summary_day = today
                except Exception as exc:
                    print(f'  [daily-summary] Error: {exc}')

            time.sleep(60)

    def _send_summary(self, day_key):
        stats = _get_today_stats()
        hourly = stats.get('hourly_activity', [0] * 24)
        busiest_hour = max(range(24), key=lambda h: hourly[h])
        busiest_end = busiest_hour + 1

        scan = []
        for h in range(24):
            if hourly[h] > 0:
                if scan and scan[-1][1] == h:
                    scan[-1] = (scan[-1][0], h + 1, scan[-1][2] + hourly[h])
                else:
                    scan.append((h, h + 1, hourly[h]))
        if scan:
            busiest_range = max(scan, key=lambda s: s[2])
            busiest_str = f'{busiest_range[0]}:00 and {busiest_range[1]}:00'
        else:
            busiest_str = f'{busiest_hour}:00 and {busiest_end}:00'

        facts = []
        facts.append(f'Busiest period: between {busiest_str}.')
        if stats['danger_count'] > 0:
            facts.append(f'Near-spill or danger alerts: {stats["danger_count"]}.')
        if stats['lost_alerts'] > 0:
            facts.append(f'Lost-object alerts: {stats["lost_alerts"]}.')
        if stats['total_work_sec'] > 0:
            work_min = int(stats['total_work_sec'] / 60)
            facts.append(f'Total time at desk: ~{work_min} minutes.')
        if stats['longest_work_streak_sec'] > 0:
            streak_min = int(stats['longest_work_streak_sec'] / 60)
            facts.append(f'Longest streak without a break: {streak_min} minutes.')
        facts.append(f'Breaks taken: {stats["breaks_taken"]}.')
        if stats['sips'] > 0:
            facts.append(f'Water sips logged: {stats["sips"]}.')
        if stats['objects_seen']:
            facts.append(f'Objects seen today: {", ".join(stats["objects_seen"][:8])}.')

        facts_block = ' '.join(facts)

        if self.gen.client:
            system = (
                'You are Desk Talk, a calm and friendly desk intelligence system. '
                'Summarize the user\'s desk day in 2-3 short sentences. '
                'Be warm, specific, and use the stats provided. '
                'Highlight anything notable (long work streaks, near-spills, lost items). '
                'End on a positive or encouraging note. No bullet points — just flowing text.'
            )
            prompt = f'Here are today\'s desk stats:\n{facts_block}\n\nWrite the daily summary.'
            text = self.gen._call_llm([
                {'role': 'system', 'content': system},
                {'role': 'user', 'content': prompt},
            ], max_tokens=120, temperature=0.7)
        else:
            text = None

        if not text:
            text = f'Today\'s desk recap: {facts_block}'

        speaker = random.choice(list(CHARACTERS.keys()))
        self.gen._broadcast({
            'type': 'group_message',
            'character': speaker,
            'text': text,
        })
        print(f'  [daily-summary] Sent by {CHARACTERS[speaker]["name"]}: {text[:100]}')

        with desk.lock:
            if day_key in desk.daily_stats:
                desk.daily_stats[day_key]['summary_sent'] = True


def _prepend_habit_event_locked(category, title, detail='', duration_sec=None, snapshot_id=None):
    """Must be called with desk.lock held."""
    ev = {
        'id': uuid.uuid4().hex[:10],
        'ts': time.time(),
        'category': category,
        'title': title,
        'detail': (detail or '')[:500],
    }
    if duration_sec is not None:
        ev['duration_sec'] = int(duration_sec)
    if snapshot_id:
        ev['snapshot_id'] = snapshot_id
    desk.habit_events.insert(0, ev)
    if len(desk.habit_events) > HABIT_LOG_MAX_EVENTS:
        desk.habit_events = desk.habit_events[:HABIT_LOG_MAX_EVENTS]


def _habit_summary_locked():
    """Must be called with desk.lock held."""
    day = _habit_local_day()
    posture_done_sec = 0
    nail_today = 0
    for ev in desk.habit_events:
        if _habit_local_day(ev['ts']) != day:
            continue
        if ev.get('category') == 'posture_bad':
            posture_done_sec += int(ev.get('duration_sec') or 0)
        elif ev.get('category') == 'nail_biting':
            nail_today += 1
    w = desk.char_widgets.get('monty') or {}
    breaks_today = int(w.get('breaks_today') or 0) if isinstance(w, dict) else 0
    ongoing = 0.0
    if desk.vlm_posture_slouch_since:
        ongoing = max(0.0, time.time() - desk.vlm_posture_slouch_since)
    return {
        'breaks_today': breaks_today,
        'posture_bad_seconds_today': int(posture_done_sec + ongoing),
        'nail_biting_count_today': nail_today,
        'ongoing_slouch_seconds': int(ongoing),
    }


def _apply_habit_vlm_tracking(j, now_ts):
    """Update posture segment + nail-biting log from one VLM JSON frame."""
    posture = str(j.get('posture') or '').lower()
    nail = bool(j.get('nail_biting') or j.get('nails_hand_to_mouth'))

    posture_snap = None  # (mins, dur_sec) or None
    nail_snap = False

    with desk.lock:
        if not desk.active_scenarios.get('reminders', True):
            desk.vlm_prev_nail_biting = nail
            return

        if posture == 'slouch':
            if desk.vlm_posture_slouch_since is None:
                desk.vlm_posture_slouch_since = now_ts
        elif posture == 'good':
            start = desk.vlm_posture_slouch_since
            desk.vlm_posture_slouch_since = None
            if start is not None:
                dur = now_ts - start
                if dur >= HABIT_POSTURE_SEGMENT_MIN_SEC:
                    mins = max(1, round(dur / 60.0))
                    posture_snap = (mins, int(dur))

        if nail and not desk.vlm_prev_nail_biting:
            if now_ts - desk.habit_nail_last_event_ts >= HABIT_NAIL_EVENT_COOLDOWN_SEC:
                desk.habit_nail_last_event_ts = now_ts
                nail_snap = True
        desk.vlm_prev_nail_biting = nail

    if posture_snap:
        mins, dur = posture_snap
        sid = desk.save_snapshot(label=f'Posture: ~{mins}m slouch')
        with desk.lock:
            _prepend_habit_event_locked(
                'posture_bad',
                f'Poor posture ~{mins} min',
                'Desk cam (Ollama vision) tracked a slouched stretch until posture looked neutral again.',
                duration_sec=dur,
                snapshot_id=sid,
            )
    if nail_snap:
        sid = desk.save_snapshot(label='Habit: nail biting')
        with desk.lock:
            _prepend_habit_event_locked(
                'nail_biting',
                'Nail biting noticed',
                'Vision pass flagged nail biting or hand-to-mouth near nails.',
                snapshot_id=sid,
            )


HABIT_SCENE_VLM_PROMPT = '''You are analyzing a single desk webcam frame. Reply ONLY with valid JSON (no markdown, no prose):
{"posture":"good|slouch|unknown","face_screen_distance":"ok|too_close|far|unknown","phone":"none|on_desk|in_hand","attention":"screen|phone|away|unknown","nail_biting":false,"nails_hand_to_mouth":false,"break_suggestion":false}
Definitions:
- too_close: face fills unusually large part of frame or appears within arm's length of the screen.
- phone in_hand: user clearly holding a phone. on_desk: phone resting on surface. none: not visible.
- slouch: shoulders hunched or head far forward vs neutral.
- nail_biting: true only if fingers/hand are clearly at the mouth in a biting/nibbling pose.
- nails_hand_to_mouth: true if hand is at mouth (nails/cuticles) even when ambiguous — use sparingly; prefer nail_biting when clear.
- break_suggestion: true only if the user looks visibly fatigued (from this single frame — usually false).
'''


class HabitSceneVlmThread(threading.Thread):
    """Periodic Ollama VLM pass for posture, screen distance, phone use — complements YOLO heuristics."""

    def __init__(self, gen):
        super().__init__(daemon=True, name='HabitSceneVlm')
        self.gen = gen

    def _cooldown_fire(self, key, min_gap_sec):
        now = time.time()
        with desk.lock:
            last = float(desk.vlm_habit_alert_ts.get(key, 0) or 0)
            if now - last < min_gap_sec:
                return False
            desk.vlm_habit_alert_ts[key] = now
        return True

    def _tick(self):
        if not OLLAMA_URL or not self.gen or not getattr(self.gen, 'client', None):
            return
        with desk.lock:
            if not desk.active_scenarios.get('reminders', True):
                return
            jpg = None
            for ci in sorted(desk.raw_frames.keys()):
                jpg = desk.raw_frames.get(ci)
                if jpg:
                    break
        if not jpg:
            return
        raw = self.gen._call_vision_vlm(jpg, HABIT_SCENE_VLM_PROMPT, model=LOST_VISION_MODEL)
        if not raw:
            return
        try:
            j = json.loads(raw)
        except json.JSONDecodeError:
            m = re.search(r'\{[\s\S]*\}', raw)
            if not m:
                return
            try:
                j = json.loads(m.group(0))
            except json.JSONDecodeError:
                return
        now = time.time()
        with desk.lock:
            desk.vlm_habit_state = j
            desk.vlm_habit_ts = now

        _apply_habit_vlm_tracking(j, now)

        dist = str(j.get('face_screen_distance') or '').lower()
        if dist == 'too_close' and self._cooldown_fire('vlm_too_close', 95):
            desk.events_queue.put({
                'type': 'scenario_alert',
                'scenario': 'reminders',
                'feature': 'posture_check',
                'character': 'monty',
                'text': 'Desk cam: you look really close to the screen — lean back a bit for eyes & neck.',
            })

        phone = str(j.get('phone') or '').lower()
        if phone == 'in_hand' and self._cooldown_fire('vlm_phone_hand', 125):
            desk.events_queue.put({
                'type': 'scenario_alert',
                'scenario': 'reminders',
                'feature': 'focus',
                'character': 'sheets',
                'text': 'Vision: phone in hand — stash it if you\'re trying to focus.',
            })

        posture = str(j.get('posture') or '').lower()
        if posture == 'slouch' and self._cooldown_fire('vlm_slouch', 190):
            desk.events_queue.put({
                'type': 'scenario_alert',
                'scenario': 'reminders',
                'feature': 'posture_check',
                'character': 'monty',
                'text': 'Camera posture cue: sit taller — shoulders back, chin neutral.',
            })

        att = str(j.get('attention') or '').lower()
        if att == 'phone' and phone != 'in_hand' and self._cooldown_fire('vlm_attn_phone', 125):
            desk.events_queue.put({
                'type': 'scenario_alert',
                'scenario': 'reminders',
                'feature': 'focus',
                'character': 'sheets',
                'text': 'Looks like attention is on the phone — try face-down for 20 minutes.',
            })

        if j.get('break_suggestion') and self._cooldown_fire('vlm_break', 620):
            desk.events_queue.put({
                'type': 'scenario_alert',
                'scenario': 'reminders',
                'feature': 'take_a_break',
                'character': 'monty',
                'text': 'Micro-break from the desk cam — stand, stretch, look away from the screen.',
            })

    def run(self):
        time.sleep(12)
        while True:
            try:
                self._tick()
            except Exception as exc:
                print(f'  [habit_scene_vlm] {exc}')
            time.sleep(max(18, HABIT_VLM_INTERVAL_SEC))


# ═══════════════════════════════════════════════════════════════════
#  Flask
# ═══════════════════════════════════════════════════════════════════
app = Flask(__name__, static_folder=APP_STATIC, static_url_path='')
CORS(app)
generator = MessageGenerator()

@app.route('/')
def index(): return send_from_directory(APP_STATIC, 'index.html')


# ═══════════════════════════════════════════════════════════════════
#  Phone camera
#
#  phone_app is a second, tiny Flask app served over HTTPS on PHONE_PORT.
#  It can only do two things: show the camera page and accept frames.
#  (The chat, video feeds, etc. stay on the main app.)
# ═══════════════════════════════════════════════════════════════════
phone_app = Flask('desk_talk_phone')
phone_app.config['MAX_CONTENT_LENGTH'] = 8 * 1024 * 1024   # one JPEG frame is ~100 KB
PHONE_QR_SVG = None


@phone_app.route('/')
@phone_app.route('/phone')
@phone_app.route('/phone/')
def phone_page():
    return send_from_directory(PHONE_STATIC, 'index.html')


@phone_app.route('/phone/<path:filename>')
def phone_static(filename):
    return send_from_directory(PHONE_STATIC, filename)


@phone_app.route('/art/<path:filename>')
@phone_app.route('/fonts/<path:filename>')
def phone_shared_assets(filename):
    """The phone page reuses the app's character art and font."""
    folder = 'art' if request.path.startswith('/art/') else 'fonts'
    return send_from_directory(os.path.join(APP_STATIC, folder), filename)


@phone_app.route('/api/phone/frame', methods=['POST'])
def phone_frame():
    if request.headers.get('X-Desk-Key') != PHONE_PAIR_KEY:
        return jsonify({'error': 'Pairing key does not match. Scan the QR code on your computer again.'}), 403
    if not phone_source.push_jpeg(request.get_data()):
        return jsonify({'error': 'not a JPEG image'}), 400
    return jsonify({'ok': True, 'fps': phone_source.fps})


def _is_local_request():
    return request.remote_addr in ('127.0.0.1', '::1')


@app.route('/api/phone/info')
def phone_info():
    """Phone connection status for the computer UI. The pairing link/QR is only
    shown to the computer itself, so other devices on the Wi-Fi can't grab the key."""
    local = _is_local_request()
    return jsonify({
        'phone_mode': PHONE_MODE,
        'connected': PHONE_MODE and phone_source.connected,
        'camera_index': PHONE_CAM_IDX,
        'fps': phone_source.fps,
        'resolution': [phone_source.width, phone_source.height],
        'url': phone_url if local else '',
        'qr_svg': PHONE_QR_SVG if local else None,
    })


def _start_phone_server():
    """Make an HTTPS certificate, then serve phone_app on PHONE_PORT in a background thread."""
    global phone_url, PHONE_QR_SVG
    ip = lan_ip()
    cert = ensure_cert(os.path.join(PROJECT_ROOT, 'certs'), ip)
    if not cert:
        print('  Phone camera : needs the `cryptography` package → pip install -r requirements.txt')
        return False
    phone_url = f'https://{ip}:{PHONE_PORT}/phone?key={PHONE_PAIR_KEY}'
    PHONE_QR_SVG = qr_svg(phone_url)
    from werkzeug.serving import make_server, WSGIRequestHandler

    class QuietHandler(WSGIRequestHandler):
        """The phone sends ~10 frames a second; only print requests that failed."""
        def log_request(self, code='-', size='-'):
            try:
                failed = int(code) >= 400
            except (TypeError, ValueError):
                failed = True
            if failed:
                super().log_request(code, size)

    srv = make_server('0.0.0.0', PHONE_PORT, phone_app, threaded=True,
                      ssl_context=cert, request_handler=QuietHandler)
    threading.Thread(target=srv.serve_forever, daemon=True, name='PhoneHTTPS').start()
    return True

# MJPEG: include Content-Length per part + no-cache headers + stream_with_context so browsers
# actually replace frames (many freeze after one frame without Content-Length / buffering fixes).
MJPEG_BOUNDARY = b'frame'
MJPEG_MIMETYPE = 'multipart/x-mixed-replace; boundary=frame'
MJPEG_HEADERS = {
    'Cache-Control': 'no-cache, no-store, must-revalidate, max-age=0',
    'Pragma': 'no-cache',
    'Expires': '0',
    'X-Accel-Buffering': 'no',
    'Connection': 'keep-alive',
}


def _mjpeg_multipart_chunk(jpeg_bytes: bytes) -> bytes:
    n = str(len(jpeg_bytes)).encode('ascii')
    return (
        b'--' + MJPEG_BOUNDARY + b'\r\n'
        b'Content-Type: image/jpeg\r\n'
        b'Content-Length: ' + n + b'\r\n'
        b'\r\n' + jpeg_bytes + b'\r\n'
    )


def _make_mjpeg_response(frame_getter):
    """Stream multipart JPEG; frame_getter returns the latest JPEG bytes or None.

    Only sends a frame when it's new. Each new frame is a new bytes object, so
    `is not` tells us it changed. (Before, the same frame was re-sent every 30 ms.)
    A repeat every 2 s keeps the connection alive so closed tabs get noticed."""

    def generate():
        last, last_sent = None, 0.0
        while True:
            try:
                jpeg = frame_getter()
                now = time.time()
                if jpeg and (jpeg is not last or now - last_sent > 2.0):
                    yield _mjpeg_multipart_chunk(jpeg)
                    last, last_sent = jpeg, now
            except Exception as exc:
                print(f'  [mjpeg] stream chunk error: {exc}')
            time.sleep(0.02)

    return Response(
        stream_with_context(generate()),
        mimetype=MJPEG_MIMETYPE,
        headers=dict(MJPEG_HEADERS),
        direct_passthrough=True,
    )


def _mjpeg(cam_idx):
    def getter():
        with desk.lock:
            return desk.frames.get(cam_idx)

    return _make_mjpeg_response(getter)


def _mjpeg_raw(cam_idx):
    """Unannotated camera JPEG stream (normal camera view)."""

    def getter():
        with desk.lock:
            return desk.raw_frames.get(cam_idx) or desk.frames.get(cam_idx)

    return _make_mjpeg_response(getter)


@app.route('/api/video_feed')
def video_feed():
    return _mjpeg(CAMERA_INDICES[0] if CAMERA_INDICES else 0)


@app.route('/api/video_feed/<int:cam_idx>')
def video_feed_cam(cam_idx):
    return _mjpeg(cam_idx)


@app.route('/api/video_feed_raw')
def video_feed_raw():
    return _mjpeg_raw(CAMERA_INDICES[0] if CAMERA_INDICES else 0)


@app.route('/api/video_feed_raw/<int:cam_idx>')
def video_feed_raw_cam(cam_idx):
    return _mjpeg_raw(cam_idx)


JPEG_NO_CACHE_HEADERS = {
    'Cache-Control': 'no-cache, no-store, must-revalidate, max-age=0',
    'Pragma': 'no-cache',
    'Expires': '0',
}


@app.route('/api/latest_frame/<int:cam_idx>')
def latest_frame_jpeg(cam_idx):
    """One annotated JPEG — fallback to raw while the first YOLO frame is still processing."""
    with desk.lock:
        j = desk.frames.get(cam_idx) or desk.raw_frames.get(cam_idx)
    if not j:
        return Response(status=404)
    return Response(bytes(j), mimetype='image/jpeg', headers=dict(JPEG_NO_CACHE_HEADERS))


@app.route('/api/latest_frame_raw/<int:cam_idx>')
def latest_frame_raw_jpeg(cam_idx):
    with desk.lock:
        j = desk.raw_frames.get(cam_idx) or desk.frames.get(cam_idx)
    if not j:
        return Response(status=404)
    return Response(bytes(j), mimetype='image/jpeg', headers=dict(JPEG_NO_CACHE_HEADERS))


@app.route('/api/snapshot/<snap_id>')
def get_snapshot(snap_id):
    with desk.lock: jpeg = desk.snapshots.get(snap_id)
    if not jpeg: return '', 404
    return Response(jpeg, mimetype='image/jpeg', headers={'Cache-Control': 'public, max-age=3600'})

@app.route('/api/snapshots')
def list_snapshots():
    """Recent proof crops from proximity / safety events (newest first)."""
    with desk.lock:
        items = [dict(x) for x in desk.snapshot_order]
    return jsonify({'items': items})

@app.route('/api/detections')
def detections():
    return jsonify(_detections_payload())


def _detections_payload():
    with desk.lock:
        fw, fh = desk.frame_w, desk.frame_h
        objs = {cid: {
            'name': CHARACTERS[cid]['name'], 'icon': CHARACTERS[cid]['icon'],
            'class': det['class_name'], 'confidence': round(det['conf'], 2),
            'center': [round(c) for c in det['center']],
            'bbox': [round(float(x)) for x in (det.get('bbox') or [0, 0, 0, 0])],
            'cam': det.get('cam', 0),
        } for cid, det in desk.objects.items()}
        extras = [
            {
                'yolo_class': cls,
                'confidence': round(v.get('conf', 0), 2),
                'bbox': [round(float(x)) for x in (v.get('bbox') or [0, 0, 0, 0])],
                'highlight': cls in LOST_OBJECT_CLASSES,
                'label': LOST_ITEM_BOX_LABELS.get(cls),
            }
            for cls, v in desk.merged_extra.items()
        ]
        rel_edges = []
        for rel in (desk.rel_result or {}).get('relationships') or []:
            if rel.get('type') == 'danger_zone':
                rel_edges.append({
                    'from': rel.get('intruder'),
                    'to': rel.get('target'),
                    'kind': 'danger_zone',
                    'label': rel.get('zone_label', ''),
                })
        vlm_habit = dict(desk.vlm_habit_state) if desk.vlm_habit_state else {}
        vlm_habit_ts = desk.vlm_habit_ts
        lost_vlm = sorted(desk.lost_last_vlm_visible)
    extras.sort(key=lambda x: (-x['confidence'], x['yolo_class']))
    return {
        'objects': objs,
        'extras': extras,
        'timestamp': time.time(),
        'frame': {'w': fw, 'h': fh},
        'relationship_edges': rel_edges,
        'vlm_habit': vlm_habit,
        'vlm_habit_ts': vlm_habit_ts,
        'lost_vlm_labels': lost_vlm,
    }

@app.route('/api/events')
def events_stream():
    q = generator.subscribe()
    def gen():
        try:
            while True:
                try:
                    msg = q.get(timeout=25)
                    yield f'data: {json.dumps(msg)}\n\n'
                except queue.Empty:
                    yield ': keepalive\n\n'
        finally: generator.unsubscribe(q)
    return Response(gen(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

@app.route('/api/activity', methods=['POST'])
def user_activity():
    """Frontend pings this while the user is typing so idle chatter holds off."""
    desk.last_user_activity = time.time()
    return ('', 204)

@app.route('/api/chat', methods=['POST'])
def chat():
    data = request.json or {}
    desk.last_user_activity = time.time()
    chat_id = data.get('chat', 'group')
    message = data.get('message', '').strip()
    if not message: return jsonify({'error': 'empty message'}), 400
    if is_lost_object_query_message(message):
        rep = generator.answer_lost_object_query(message)
        if rep and isinstance(rep, dict) and rep.get('text'):
            reply_as = 'sheets' if chat_id == 'group' else chat_id
            if reply_as not in CHARACTERS:
                reply_as = 'sheets'
            msg = {'character': reply_as, 'text': rep['text']}
            if rep.get('cta'):
                msg['cta'] = rep['cta']
            return jsonify({
                'messages': [msg],
                'lost_object_lookup': True,
            })
    history = _sanitize_chat_history(
        (data.get('recent_messages') or data.get('history') or None))
    if chat_id == 'group':
        speaker_ids = _group_speaker_order(message, history)
        rolling_history = list(history)
        prev_msg = None
        for char_id in speaker_ids:
            # Pass the literal user message; earlier speakers' lines ride along
            # in rolling_history so later speakers stay connected to the thread.
            text, _actions = generator.generate_chat_response(
                char_id, message, rolling_history)
            if not text:
                continue
            msg = {'type': 'group_message', 'character': char_id, 'text': text}
            if prev_msg:
                msg['reply_to'] = {'character': prev_msg['character'], 'text': prev_msg['text']}
            # Push each reply the moment it finishes generating so the UI shows
            # it immediately instead of waiting for the whole batch.
            generator._broadcast(msg)
            rolling_history.append({
                'role': 'assistant',
                'name': CHARACTERS[char_id]['name'],
                'text': text,
            })
            prev_msg = msg
        return jsonify({'messages': [], 'streamed': True})
    else:
        char_id = chat_id
        if char_id not in CHARACTERS:
            return jsonify({'error': 'unknown chat'}), 400
        if 'widget' in data:
            w = _normalize_widget(data.get('widget'))
            if w:
                with desk.lock:
                    desk.char_widgets[char_id] = w
    text, actions = generator.generate_chat_response(char_id, message, history)
    if not text:
        return jsonify({'messages': []})
    resp = {'messages': [{'character': char_id, 'text': text}]}
    if actions:
        resp['actions'] = actions
    return jsonify(resp)


@app.route('/api/chat_widget/<char_id>')
def get_chat_widget(char_id):
    """Return current widget state for a character (used by frontend to refresh after auto-events)."""
    if char_id not in CHARACTERS:
        return jsonify({'error': 'unknown chat'}), 400
    with desk.lock:
        w = desk.char_widgets.get(char_id)
    return jsonify({'widget': w or {}})


@app.route('/api/habit_log')
def habit_log():
    """Computer habit timeline: vision-backed posture segments, nail biting, + summary (breaks from widget)."""
    category = (request.args.get('category') or 'all').strip().lower()
    limit = min(max(int(request.args.get('limit', 40)), 1), 100)
    with desk.lock:
        events = [dict(e) for e in desk.habit_events]
        summary = _habit_summary_locked()
        vlm = dict(desk.vlm_habit_state) if desk.vlm_habit_state else {}
        vlm_ts = desk.vlm_habit_ts
    if category != 'all':
        events = [e for e in events if (e.get('category') or '') == category]
    events = events[:limit]
    return jsonify({
        'events': events,
        'summary': summary,
        'vlm_state': vlm,
        'vlm_ts': vlm_ts,
    })


@app.route('/api/daily_stats')
def daily_stats_endpoint():
    """Return today's accumulated desk stats."""
    return jsonify(_get_today_stats())


@app.route('/api/scenarios')
def get_scenarios():
    """Return all available scenarios and their features."""
    out = {}
    for sid, sdata in SCENARIOS.items():
        features = {}
        for fid, fdata in sdata['features'].items():
            features[fid] = {
                'name': fdata['name'],
                'desc': fdata['desc'],
                'active': fdata.get('active', True),
            }
        out[sid] = {
            'name': sdata['name'],
            'icon': sdata['icon'],
            'features': features,
        }
    with desk.lock:
        lost_status = {}
        for yolo_cls, entry in desk.lost_objects.items():
            if entry['last_seen']:
                ago = int(time.time() - entry['last_seen'])
                lost_status[yolo_cls] = {'last_seen_ago_sec': ago, 'alerted': entry['alerted']}
    return jsonify({'scenarios': out, 'lost_objects': lost_status})


@app.route('/api/chat_todos', methods=['POST'])
def chat_todos():
    """Store per-character widget data for LLM context."""
    data = request.json or {}
    cid = data.get('chat')
    if cid not in CHARACTERS:
        return jsonify({'error': 'unknown chat'}), 400
    w = _normalize_widget(data.get('widget'))
    if w:
        with desk.lock:
            desk.char_widgets[cid] = w
    return jsonify({'ok': True})


@app.route('/api/chat_todos_sync', methods=['POST'])
def chat_todos_sync():
    """Bulk sync all character widget data from the client on connect."""
    data = request.json or {}
    bulk = data.get('by_chat', {})
    if not isinstance(bulk, dict):
        return jsonify({'error': 'bad payload'}), 400
    with desk.lock:
        for cid, wdata in bulk.items():
            if cid in CHARACTERS:
                w = _normalize_widget(wdata) if isinstance(wdata, dict) else None
                if w:
                    desk.char_widgets[cid] = w
    return jsonify({'ok': True})


@app.route('/api/widget_event', methods=['POST'])
def widget_event():
    """Record when the user views/interacts with a widget (for behavioral pattern tracking)."""
    data = request.json or {}
    cid = data.get('chat')
    action = data.get('action', 'view')
    if cid not in CHARACTERS:
        return jsonify({'error': 'unknown chat'}), 400
    now = time.time()
    with desk.lock:
        log = desk.widget_views.setdefault(cid, [])
        log.append({'ts': now, 'action': action})
        if len(log) > 50:
            desk.widget_views[cid] = log[-50:]
    return jsonify({'ok': True})


@app.route('/api/context')
def get_context():
    """Return structured live context for the frontend."""
    ctx = build_live_context()
    return jsonify({
        'detected': ctx['detected'],
        'dangers': ctx['dangers'],
        'safety_state': ctx['safety_state'],
        'mention_options': [
            {'id': cid, 'name': ch['name'], 'icon': ch['icon'], 'object': ch['object'],
             'detected': cid in desk.objects}
            for cid, ch in CHARACTERS.items()
        ],
    })

@app.route('/api/habits')
def habits_info():
    """Return habit focus areas for all characters."""
    return jsonify({
        cid: {
            'area': h['area'],
            'tips': h['tips'],
            'interval': h['interval'],
        } for cid, h in HABIT_FOCUS.items()
    })


@app.route('/api/habit_policy', methods=['GET', 'PUT'])
def habit_policy():
    if request.method == 'GET':
        with desk.lock:
            p = dict(desk.habit_policy)
        p['dnd_active_now'] = _habit_dnd_active()
        p['min_gap_sec'] = HABIT_MIN_GAP_SEC
        return jsonify(p)
    data = request.json or {}
    with desk.lock:
        if 'dnd_enabled' in data:
            desk.habit_policy['dnd_enabled'] = bool(data['dnd_enabled'])
        if 'dnd_start_hour' in data:
            desk.habit_policy['dnd_start_hour'] = int(data['dnd_start_hour']) % 24
        if 'dnd_end_hour' in data:
            desk.habit_policy['dnd_end_hour'] = int(data['dnd_end_hour']) % 24
        pout = dict(desk.habit_policy)
    return jsonify({'ok': True, 'policy': pout})

@app.route('/api/safety')
def safety_status():
    """Current safety state, pipeline danger pairs, and relationship engine output."""
    return jsonify(_safety_payload())


def _safety_payload():
    with desk.lock:
        state = desk.safety_state
        dangers = list(desk.safety_dangers)
        rr = desk.rel_result
    out = {
        'state': state,
        'dangers': dangers,
        'rules_loaded': HAS_SAFETY_RULES,
        'num_rules': len(SAFETY_RULES),
    }
    if rr:
        out['risk_level'] = int(rr.get('risk_level', 0))
        out['explanations'] = list(rr.get('explanations') or [])
        out['risk_events'] = list(rr.get('risk_events') or [])
        out['relationships'] = list(rr.get('relationships') or [])
    else:
        out['risk_level'] = 0
        out['explanations'] = []
        out['risk_events'] = []
        out['relationships'] = []
    return out


@app.route('/api/live')
def live_snapshot():
    """Everything the desk cam panel needs in one request (the UI polls this every 1.5 s).
    Replaces separate calls to /api/status, /api/detections, /api/safety and /api/phone/info."""
    if PHONE_MODE:
        camera_on, cam_idx = phone_source.connected, PHONE_CAM_IDX
    else:
        with desk.lock:
            cams = sorted(desk.cameras.keys())
        camera_on, cam_idx = bool(cams), (cams[0] if cams else 0)
    out = {
        'phone_mode': PHONE_MODE,
        'camera': camera_on,
        'camera_index': cam_idx,
        'detections': _detections_payload(),
        'safety': _safety_payload(),
    }
    if PHONE_MODE:
        local = _is_local_request()
        out['phone'] = {
            'fps': phone_source.fps,
            'url': phone_url if local else '',
            # The QR code is ~10 KB, so only send it when the page asks (?qr=1).
            'qr_svg': PHONE_QR_SVG if local and request.args.get('qr') else None,
        }
    return jsonify(out)

@app.route('/api/safety', methods=['POST'])
def safety_receive():
    """Receive safety updates from the standalone pipeline (main.py)."""
    data = request.json or {}
    with desk.lock:
        if data.get('type') == 'danger_alert':
            desk.safety_state = 'DANGEROUS'
            desk.safety_dangers = data.get('pairs', [])
        elif data.get('type') == 'safe_status':
            desk.safety_state = 'SAFE'
            desk.safety_dangers = []
    return jsonify({'ok': True})

@app.route('/api/rules')
def get_rules():
    """Return the current config for the zone/edge settings UI."""
    rules_path = RULES_PATH
    cfg = {}
    if os.path.isfile(rules_path):
        with open(rules_path) as f:
            cfg = json.load(f)
    from relationships import DANGER_ZONES, EDGE_DANGER_PX, EDGE_WARN_PX, EDGE_OBJECTS
    return jsonify({
        'loaded': HAS_SAFETY_RULES,
        'danger_zones': cfg.get('danger_zones', {k: v for k, v in DANGER_ZONES.items()}),
        'edge_proximity': cfg.get('edge_proximity', {
            'danger_px': EDGE_DANGER_PX,
            'warn_px': EDGE_WARN_PX,
            'objects': sorted(EDGE_OBJECTS),
        }),
        'chain_reaction': cfg.get('chain_reaction', {}),
        'temporal': cfg.get('temporal', {}),
        'available_objects': AVAILABLE_YOLO_OBJECTS,
    })

@app.route('/api/rules', methods=['PUT'])
def save_rules():
    """Save updated zone/edge config to safety_rules.json and reload."""
    global HAS_SAFETY_RULES
    data = request.json
    if not data:
        return jsonify({'error': 'Empty payload'}), 400
    rules_path = RULES_PATH
    try:
        with open(rules_path, 'r') as f:
            existing = json.load(f)
    except Exception:
        existing = {}
    for key in ('danger_zones', 'edge_proximity', 'chain_reaction', 'temporal'):
        if key in data:
            existing[key] = data[key]
    with open(rules_path, 'w') as f:
        json.dump(existing, f, indent=4)
    HAS_SAFETY_RULES = _load_safety_rules()
    return jsonify({'ok': True})

@app.route('/api/status')
def status():
    with desk.lock:
        detected = list(desk.objects.keys())
        cams = sorted(desk.cameras.keys())
        safety = desk.safety_state
    if PHONE_MODE:
        # "Camera on" means the phone is actively streaming right now.
        cams = [PHONE_CAM_IDX] if phone_source.connected else []
    return jsonify({
        'status': 'running', 'camera': len(cams) > 0,
        'phone_mode': PHONE_MODE,
        'camera_indices': cams, 'num_cameras': len(cams),
        'llm': bool(generator.client), 'llm_label': generator.llm_label,
        'detected_characters': detected,
        'total_characters': len(CHARACTERS),
        'safety_state': safety,
        'safety_rules': HAS_SAFETY_RULES,
        'risk_level': (desk.rel_result or {}).get('risk_level', 0),
    })

# ═══════════════════════════════════════════════════════════════════
if __name__ == '__main__':
    HAS_SAFETY_RULES = _load_safety_rules()

    print()
    print('═' * 56)
    print('  Desk Talk — Server (4 characters)')
    print('═' * 56)
    print(f'  Cameras      : {"phone over Wi-Fi" if PHONE_MODE else CAMERA_INDICES}')
    print(f'  LLM          : {generator.llm_label}')
    print(f'  Confidence   : {CONFIDENCE}')
    print(f'  Cooldown     : {EVENT_COOLDOWN}s')
    print(f'  Smoothing    : appear={APPEAR_FRAMES}f  disappear={DISAPPEAR_FRAMES}f')
    if HAS_SAFETY_RULES:
        print(f'  Safety Rules : {len(SAFETY_RULES)} rules from safety_rules.json')
        print(f'  Distance     : per-pair pixel thresholds (chart pipeline)')
    else:
        print(f'  Safety Rules : built-in ratio-based (no safety_rules.json)')
    print('═' * 56)

    _yolo_local = os.path.join(PROJECT_ROOT, YOLO_MODEL)
    if not os.path.isfile(_yolo_local):
        _alt = os.path.join(PROJECT_ROOT, 'weights', os.path.basename(YOLO_MODEL))
        if os.path.isfile(_alt):
            _yolo_local = _alt
    _yolo_arg = _yolo_local if os.path.isfile(_yolo_local) else YOLO_MODEL
    model = YOLO(_yolo_arg)
    print(f'  YOLO model : {YOLO_MODEL} ({_yolo_arg})')
    try:
        _warm = np.zeros((640, 640, 3), dtype=np.uint8)
        with _YOLO_PREDICT_LOCK:
            model.predict(source=_warm, conf=CONFIDENCE, verbose=False)
    except Exception as exc:
        print(f'  YOLO warmup: {exc}')
    cam_threads = []
    if PHONE_MODE:
        ct = CameraThread(PHONE_CAM_IDX, model, source=phone_source)
        ct.start()
        cam_threads.append(ct)
        _start_phone_server()
    else:
        for idx in CAMERA_INDICES:
            ct = CameraThread(idx, model)
            ct.start()
            cam_threads.append(ct)
            time.sleep(0.5)
    DetectionMerger(cam_threads).start()
    generator.start()
    LostObjectFrameworkThread(generator).start()
    HabitSceneVlmThread(generator).start()
    HabitReminderThread(generator).start()
    IdleChatThread(generator).start()
    DailySummaryThread(generator).start()
    print(f'  Idle chat  : director-led, every {IDLE_CHAT_MIN_SEC}-{IDLE_CHAT_MAX_SEC}s, up to {IDLE_CHAT_MAX_TURNS} turns')
    print(f'  Daily recap: fires at {DAILY_SUMMARY_HOUR}:00 local time')
    print(f'  Habits     : {len(HABIT_FOCUS)} characters + scene VLM every {HABIT_VLM_INTERVAL_SEC}s (DND: desk.habit_policy, min gap {HABIT_MIN_GAP_SEC}s)')
    print(f'  Lost items : loop {LOST_LOOP_SEC}s, scene diff ≥{LOST_SCENE_DIFF_THR}, VLM: {LOST_VISION_MODEL or "n/a"}' + (f' (Ollama {OLLAMA_URL})' if OLLAMA_URL else ' (set OLLAMA_URL for VLM)'))
    print(f'\n  → Open http://localhost:5000 on this computer\n')
    if PHONE_MODE and phone_url:
        print('  📱 Phone camera — scan this with your phone (same Wi-Fi), or open:')
        print(f'     {phone_url}\n')
        print_qr(phone_url)
        print('  The phone will warn the connection is "not private" (it\'s a local certificate).')
        print('  Tap Show Details → visit this website, then tap Start camera.\n')
    app.run(host='0.0.0.0', port=5000, threaded=True)
