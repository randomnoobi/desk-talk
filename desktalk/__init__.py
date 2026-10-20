"""
Desk Talk: desk objects become chat characters.

Package layout (each folder has one job):

    desktalk/
      settings.py      reads config/*.yaml and .env
      paths.py         where every folder is
      app.py           builds every part and starts the threads (python server.py)
      desk_state.py    the shared desk state (what's on the desk, photos, trackers)
      scheduler.py     runs jobs every N minutes (event-log compaction)
      util.py          small helpers (times, durations, names)
      events/          the event stream (producer → stream → consumers) and the event log
      vision/          camera sources, the YOLO detector, the producer, desk geometry, Lens
      agents/          agent profiles (config/agents.yaml) and the roster (join / leave)
      habits/          the Protocols habit rules, the habit tracker, reminders
      chat/            the AI: replies, alerts, idle chat, lost items, daily recap
      web/             the Flask routes for the app (web_app/), the Pi and the phone camera
"""
