"""Every background chat job is a PeriodicWorker the Supervisor can run: a name, and tick() gives the next wait."""

from __future__ import annotations

import pytest

from desktalk.config import (
    AiReplies,
    AiReply,
    DailySummarySettings,
    IdleChatSettings,
    LensSettings,
    LensWaits,
    LostItemsSettings,
    PiSettings,
    SnapshotsSettings,
    VisionHabitsSettings,
    VisionNudgeRepeats,
)
from desktalk.core.chat.workers import DailySummary, IdleChat, Lens, LostItems, VisionHabits
from desktalk.core.vision.proof_photos import ProofPhotos
from desktalk.desk_state import DeskState
from desktalk.ports import PeriodicWorker
from desktalk.runtime import GuardedWorker
from tests.fakes import (
    FakeCameraHealth,
    FakeChatModel,
    FakeClock,
    FakeDeskStatus,
    FakeFrameSource,
    FakeHabits,
    FakePreferences,
    FakeProfiles,
    InMemoryKeyValueStore,
    RecordingNotifier,
    StaticRoster,
    SyncEventBus,
)

REPLY = AiReply(max_tokens=100, temperature=0.5)
REPLIES = AiReplies(**{name: REPLY for name in AiReplies.model_fields})


class Layout:
    def has_outline(self):
        return True

    def reference_gravity(self):
        return None


def all_workers(clock):
    ai, bus, desk, roster, profiles = FakeChatModel(), SyncEventBus(), DeskState(), StaticRoster(), FakeProfiles()
    notifier, status = RecordingNotifier(clock), FakeDeskStatus()
    photos = ProofPhotos(desk, clock, SnapshotsSettings(max_kept=150, crop_padding=0.25, crop_min_px=40,
                                                        jpeg_quality=85))
    return [
        IdleChat(ai, notifier, status, roster, profiles, desk, lambda: '', IdleChatSettings(
            min_sec=720, max_sec=1500, start_delay_sec=15, max_turns=3, quiet_after_typing_sec=10,
            pause_between_lines_sec=3, pause_random_sec=3, memory_per_agent=2, memory_lines=6, topic_min_words=2,
            line_min_words=3), REPLIES, memory_kept=8, memory_chars=120, default_temperature=0.8,
            reply_max_chars=500),
        LostItems(clock, bus, desk, roster, profiles, FakeHabits(), ai, None, LostItemsSettings(
            check_every_sec=60, scene_change=8.0, rescan_every_sec=300, snapshot_every_sec=120,
            spotted_photo_cooldown_sec=75, start_delay_sec=5, compare_size=64, min_confidence=0.3, just_seen_sec=90,
            detector_fresh_sec=5, nearby_kept=5, nearby_mentioned=3, answer_min_words=3, items={}),
            REPLIES, photos=photos, default_temperature=0.8),
        Lens(FakeCameraHealth(), FakeFrameSource('pi', clock), Layout(), FakePreferences(), notifier, bus, photos,
             LensSettings.model_construct(check_every_sec=2.0, repeat_after_min=20.0, fixed_after_sec=5.0,
                                          wait_before_saying_sec=LensWaits.model_construct(pi=10, pi_detector=60)),
             PiSettings.model_construct(hot_warning_c=75.0, clock_off_warning_sec=5.0)),
        DailySummary(ai, notifier, roster, FakeHabits(), InMemoryKeyValueStore(), DailySummarySettings(
            hour=21, start_delay_sec=120, check_every_sec=60, objects_listed=8), REPLIES, default_temperature=0.8),
        VisionHabits(None, bus, desk, roster, profiles, status, VisionHabitsSettings(
            every_sec=42, slouch_log_min_sec=90, nail_log_cooldown_sec=50, max_log_events=120, start_delay_sec=12,
            min_every_sec=18, repeat_after_sec=VisionNudgeRepeats(too_close=1200, slouch=1500, phone=1500)), clock,
            photos),
    ]


@pytest.mark.parametrize('index', range(5), ids=['idle chat', 'lost items', 'lens', 'daily recap', 'vision habits'])
def test_the_job_runs_under_the_supervisors_guard(index):
    clock = FakeClock()
    worker = all_workers(clock)[index]
    assert isinstance(worker, PeriodicWorker)
    guarded = GuardedWorker(worker, clock, first_retry_sec=1, max_retry_sec=300)
    wait = guarded.step()
    assert wait > 0 and guarded.status()['failures_in_row'] == 0


def test_the_jobs_have_different_names():
    names = [w.name for w in all_workers(FakeClock())]
    assert len(set(names)) == len(names)
