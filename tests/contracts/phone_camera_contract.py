"""What the phone as a camera must do (desktalk.ports.PhoneCamera and PhoneControls)."""

from __future__ import annotations

TILT = (0.1, -9.7, 0.4)


class PhoneCameraContract:
    """Subclass and give a `phone` fixture (nothing sent yet)."""

    def test_nothing_known_at_first(self, phone):
        assert phone.recent_gravity() is None
        assert not phone.stopped_by_user

    def test_the_tilt_the_phone_sent_is_kept(self, phone):
        phone.set_motion(TILT, 90)
        assert phone.recent_gravity() == TILT

    def test_saying_nothing_keeps_the_old_tilt(self, phone):
        phone.set_motion(TILT)
        phone.set_motion(None, 180)
        phone.set_motion()
        assert phone.recent_gravity() == TILT

    def test_stop_on_the_phone(self, phone):
        phone.mark_stopped()
        assert phone.stopped_by_user
