# Architecture

How a picture on the Raspberry Pi turns into "Cup joined the chat".

```
 Raspberry Pi                    This computer (python server.py)
 ────────────                    ─────────────────────────────────────────────────────────────
 camera_node.py                  /api/pi/frame
 3 pictures/s ── JPEG over ───▶  PiFrameSource ──▶ DetectionProducer (YOLO, 3/s)
 saved on the Pi    Wi-Fi                                 │  publishes one "detection" event per picture
 deleted every 10 min                                     ▼
                                                    EventStream  (producer → stream → consumers)
                                    ┌─────────────┬──────┴──────┬──────────────┬──────────────┐
                                    ▼             ▼             ▼              ▼              ▼
                                EventLog       Roster      DeskMonitor    HabitTracker    LostItems
                              data/events/   join/leave   danger zones,   water, breaks,  where your
                              (compacted     → agent_     edges, pets     light, phone    keys were
                              every 10 min)   joined/left → alert         → habit
                                                    │             │              │
                                                    └──────┬──────┴──────────────┘
                                                           ▼
                                                  MessageGenerator (the AI) ──SSE──▶ the app (web_app/)
 Threads: reminders (Protocols nudges), Lens (camera health), idle chat, vision checks, daily recap.
 Scheduler: compact the event log every 10 minutes, delete days older than 30.
```

## The pieces

| Folder / file | Job |
|---|---|
| `raspberry_pi/camera_node.py` | Runs on the Pi: takes, saves, sends and deletes pictures. Standard library only. |
| `desktalk/vision/camera_sources.py` | Where pictures come from: `PiFrameSource`, `PhoneFrameSource`, `WebcamSource`. Same `read()` as OpenCV. |
| `desktalk/vision/detector.py` | The YOLO model (`detector.model` in settings). Downloads it into `models/` the first time. |
| `desktalk/vision/producer.py` | **Producer.** Newest picture → YOLO → one `detection` event, 3 times a second. Also keeps the live view pictures. |
| `desktalk/events/stream.py` | **Event stream.** Publish / subscribe inside the program. Each consumer has its own queue and thread, so a slow AI call never holds up the camera ("decoupling"). |
| `desktalk/events/log.py` | **Event log** on this computer, `data/events/<day>/`. Compaction turns per-picture events into intervals. |
| `desktalk/scheduler.py` | Runs jobs every N minutes (compaction). |
| `desktalk/agents/` | `profiles.py` reads `config/agents.yaml`; `roster.py` decides who joins and leaves the chat. |
| `desktalk/vision/desk_monitor.py` | Safety alerts from desk geometry (`relationships.py`), pets, clutter. |
| `desktalk/habits/` | `protocols.py` (the book's rules, as plain functions), `tracker.py` (today's numbers), `reminders.py` (when to nudge, who says it). |
| `desktalk/chat/` | The AI: replies, alerts, idle chat, lost items, Lens, recap. All prompts are in `prompts.py`. |
| `desktalk/web/` | Flask routes: `routes.py` for the app and the Pi, `phone_server.py` for the phone camera, `video_stream.py` for the live view. |
| `web_app/` | The app you open at http://localhost:5000 (HTML, CSS, JavaScript, drawings, font). |
| `phone_camera/` | The page a phone opens to be the camera instead of the Pi (only with `camera.source: phone`). |
| `config/` | Every setting: `settings.yaml`, `agents.yaml`, `protocols.yaml`, `safety_rules.json`. |

## Events

Every event is a dict with `id`, `ts` (when it happened, seconds), `type`, and data:

| type | made by | example data |
|---|---|---|
| `detection` | producer | `frame_id`, `source`, `size`, `detected_obj: [{label, conf, bbox, center}]`, `person`, `moved`, `light: {brightness, lux}` |
| `agent_joined` / `agent_left` | roster | `agent`, `name`, `label`, `conf` / `stayed_sec` |
| `alert` | desk monitor, lost items, vision | `kind: proximity` + `agent`, `near`, `rule_label`, `snapshot`; or `kind: scenario` + `text` |
| `habit` | habit tracker | `habit: hydration`, `action: sip`, `say: "Sip detected!..."` |
| `nudge` | reminders | `habit`, `rule`, `agent`, `evidence`, `text` |
| `camera` | Lens | `key: dark`, `kind: problem` |
| `compaction` | event log | `frames`, `intervals`, `from`, `to` |

## The event log (discrete → continuous)

```
data/events/2026-10-18/
  detections.jsonl   one line per picture (3 a second), until the next compaction
  presence.jsonl     {"label": "cup", "interval": [1792345678.1, 1792347461.9], "frames": 5310, ...}
  events.jsonl       joins, leaves, alerts, habits, nudges, camera problems, compactions
```

Every 10 minutes the scheduler runs `EventLog.compact()`: per-picture detections
become one interval per object per stretch on the desk (a gap longer than
`event_log.merge_gap_sec` starts a new interval), intervals that touch the last
compaction's are joined, and the per-picture lines are deleted. The Event log page
in the app shows the intervals as a timeline, plus the other events.

The pictures themselves stay on the Pi and are deleted every 10 minutes
(`pi.delete_every_min`). The computer keeps only a few proof photos in memory.

## Agents

See [AGENT_SYSTEM.md](AGENT_SYSTEM.md).

## Tests

```bash
uv sync --all-packages
uv run pytest                    # all (about a minute)
uv run pytest tests/unit         # fast unit tests
uv run pytest -m "not slow"      # skip the real-YOLO tests
```

`tests/unit/` checks each piece on its own (settings, event stream, event log,
roster, Protocols rules, habit tracker, geometry, Lens, chat helpers, the Pi
node). `tests/integration/` runs them together: scripted pictures through the
whole pipeline, the web API with a fake AI, the Pi node streaming to a real
server over HTTP, reminders and Lens, and the real YOLO model on sample photos.
