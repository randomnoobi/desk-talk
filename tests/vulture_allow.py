"""Names the dead code check (vulture) can't see being used. This file is only read by vulture, never run.

Run the check with: uv run vulture
Add a name here only when something really uses it in a way vulture can't follow (a pydantic field read
by name, a field the web page reads from the JSON, a flag another thread reads). Say what uses it.
"""

from uvicorn import Server

from desktalk.config import AiReplies, LensWaits, RiskWeights, VisionNudgeRepeats
from desktalk.domain.events import AgentLeft, Compaction, DetectionEvent, NudgeSent
from desktalk.ports import EventStore, PictureInbox
from desktalk.web.schemas import PreferenceChanges
from desktalk_shared.wire import PiHealth

_ = (
    # Settings read by name with getattr (the risk score adds up each weight, Lens looks up each wait).
    RiskWeights.edge_danger, RiskWeights.edge_warning, RiskWeights.zone_intrusion, RiskWeights.crowd,
    RiskWeights.repeated_intrusion,
    AiReplies.alert,
    VisionNudgeRepeats.slouch,
    LensWaits.lost, LensWaits.covered, LensWaits.dark, LensWaits.glare, LensWaits.blurry, LensWaits.bumped,
    LensWaits.slow,
    # Request fields read through model_dump().
    PreferenceChanges.quiet_enabled, PreferenceChanges.quiet_start_hour, PreferenceChanges.quiet_end_hour,
    PreferenceChanges.dnd_enabled, PreferenceChanges.dnd_start_hour, PreferenceChanges.dnd_end_hour,
    # Event and status fields the web page (web_app/app.js) reads from the JSON.
    AgentLeft.stayed_sec, Compaction.from_ts, PiHealth.last_cleanup_ts,
    # Written to the picture log to show where the detector ran (pi or computer); the Log tab's
    # /api/log/detections sends it, and the tests check it to tell a Pi detection from one done here.
    DetectionEvent.detected_on,
    # Written with every habit reminder; the rule engine (PR #6) reads it.
    NudgeSent.rule,
    # Interfaces with no user yet: the web layer starts using them in Task 10 of the cleanup plan.
    EventStore, PictureInbox,
    # Pydantic's own settings attribute, and the flag uvicorn's thread reads to stop.
    PreferenceChanges.model_config, Server(config=None).should_exit,  # type: ignore[arg-type]
)
