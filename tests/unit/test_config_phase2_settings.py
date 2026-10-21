"""Settings the Phase 2 parts asked for (see docs/plans/2026-10-21-phase3-notes.md)."""

from __future__ import annotations

from pathlib import Path

from desktalk.config import load_config

ROOT = Path(__file__).resolve().parents[2]


def test_settings_the_new_parts_need_are_in_settings_yaml():
    c = load_config(root=ROOT, environ={})
    assert (c.detector.end2end, c.detector.rect) == (False, False)
    assert c.camera.pair_key_bytes == 6
    assert c.storage.folder == Path('data')
    assert c.agents.personality_max_chars == 400
    assert c.chat.reply_max_chars == 500
    assert c.lens.wait_before_saying_sec.pi == 10
    assert (c.pi.camera_warmup_sec, c.pi.pause_after_error_sec, c.pi.status_every_sec, c.pi.health_every_sec,
            c.pi.settings_check_every_sec) == (1, 0.5, 30, 3, 60)


def test_the_producers_old_error_pause_is_gone():
    assert not hasattr(load_config(root=ROOT, environ={}).camera, 'pause_after_error_sec')
