"""What the Pi as a camera must tell the rest of the app (desktalk.ports.PiStatus and PiUploads)."""

from __future__ import annotations

from desktalk.domain import DetectorState, PiHealth, UploadInfo

STATUS_KEYS = {'last_frame_id', 'upload_format', 'detector', 'detector_reason', 'detect_ms', 'model', 'clock_off_sec'}


def an_upload(frame_id: str = 'pi-000007') -> UploadInfo:
    return UploadInfo(frame_id=frame_id, taken_at=1_760_000_002.0, detector=DetectorState.OFF,
                      detector_reason='the detector is starting', detect_ms=410.0, model='yolo26n.pt',
                      health=PiHealth(camera='picamera2', cpu_temp_c=61.5))


class PiStatusContract:
    """Subclass and give two fixtures: `pi` (no picture yet) and `upload`, which hands `pi` one picture with the
    UploadInfo it is given: upload(pi, info)."""

    def test_nothing_known_before_the_first_picture(self, pi):
        assert pi.latest_upload() is None
        assert pi.clock_off_sec() is None
        assert pi.pi_info() == {'last_frame_id': ''}
        status = pi.status()
        assert set(status) >= STATUS_KEYS
        assert status['upload_format'] is None and status['clock_off_sec'] is None

    def test_the_newest_upload_and_when_it_came(self, pi, upload):
        info = an_upload()
        upload(pi, info)
        got, arrived_at = pi.latest_upload()
        assert got == info and isinstance(arrived_at, float)
        assert isinstance(pi.clock_off_sec(), float)

    def test_what_the_pi_said_about_itself(self, pi, upload):
        upload(pi, an_upload())
        info = pi.pi_info()
        assert info['last_frame_id'] == 'pi-000007'
        assert info['camera'] == 'picamera2' and info['cpu_temp_c'] == 61.5
        status = pi.status()
        assert {k: status[k] for k in info} == info
        assert (status['upload_format'], status['detector'], status['detector_reason']) == (
            an_upload().version, 'off', 'the detector is starting')
        assert (status['detect_ms'], status['model']) == (410.0, 'yolo26n.pt')
