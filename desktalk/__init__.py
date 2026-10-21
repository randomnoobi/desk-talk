"""
Desk Talk: desk objects become chat characters.

Package layout (each folder has one job):

    desktalk/
      bootstrap.py     builds every part from the settings and connects them (uv run python server.py)
      config.py        reads config/*.yaml and .env into typed settings
      ports.py         the small interfaces the parts talk through
      domain/          the plain data they pass: detections, events, agents, habits
      desk_state.py    the shared desk state (what's on the desk, photos, trackers)
      util.py          small helpers (times, durations, names)
      core/            what Desk Talk does, with no network, camera or files:
        vision/          the producer, the desk rules, Lens's camera checks, the live view
        agents/          agent profiles (config/agents.yaml) and the roster (join / leave)
        habits/          the Protocols habit rules, the habit tracker, reminders
        chat/            the AI's replies, alerts, small talk, lost items, the daily recap
        events/          the event stream (producer → stream → everyone who listens)
      adapters/        the real outside things: cameras, the YOLO detector, the AI, files on disk
      runtime/         the clock, the scheduler, the Supervisor that keeps background jobs running, logging
      web/             the FastAPI servers: the app (web_app/), the Pi's uploads and the phone camera page
"""
