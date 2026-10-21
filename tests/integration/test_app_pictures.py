"""
The whole app built by bootstrap, from pictures to chat messages:
pictures → the producer → detection events → roster / desk monitor / habits / event log → messages on the hub.
Ported from test_app_flow.py, plus Pi pictures in each detector state (ok, busy, off).
"""

from __future__ import annotations

from desktalk.domain import BBox, Detection, DetectionEvent, DetectorState, UploadInfo
from tests.integration.app_kit import CUP_FAR, CUP_NEAR, LAPTOP, PERSON, DeskRig, desk_jpeg

STAPLER = Detection('stapler', 0.9, BBox(100, 100, 160, 140))
CAT = Detection('cat', 0.9, BBox(0, 300, 150, 470))


def test_objects_join_the_chat_warn_log_a_sip_and_leave(tmp_path):
    rig = DeskRig(tmp_path)
    app = rig.app

    # 1. A laptop and a cup appear: both join after 3 seconds and say hello.
    rig.watch(4, LAPTOP, CUP_FAR)
    msgs = rig.messages()
    roster = [m for m in msgs if m['type'] == 'roster']
    assert {(m['agent']['id'], m['action']) for m in roster} == {('computer', 'joined'), ('cup', 'joined')}
    assert {m['text'] for m in roster} == {'Computer joined the chat', 'Cup joined the chat'}
    assert {m['character'] for m in msgs if m.get('hello')} == {'computer', 'cup'}
    assert set(app.roster.member_ids()) == set(app.desk.objects) == {'computer', 'cup'}

    # 2. The cup slides next to the laptop: Computer (fragile) warns, with a proof photo.
    rig.watch(2, LAPTOP, CUP_NEAR)
    alerts = [m for m in rig.messages() if m.get('alert') == 'proximity']
    assert alerts and alerts[0]['character'] == 'computer'
    assert 'Cup' in alerts[0]['text'] and app.desk.snapshots.get(alerts[0]['snapshot'])
    assert app.habits.today()['danger_count'] >= 1

    # 3. The cup is picked up for 8 seconds and put back: a sip.
    rig.watch(8, LAPTOP)
    rig.watch(3, LAPTOP, CUP_FAR)
    sips = [m for m in rig.messages() if m.get('habit') == 'hydration' and m['type'] == 'group_message']
    assert sips and 'Sip detected' in sips[0]['text'] and sips[0]['character'] == 'cup'
    assert app.habits.today()['hydration']['sips'] == 1

    # 4. The cup goes away for good: "Cup left the chat".
    rig.watch(50, LAPTOP)
    left = [m for m in rig.messages() if m['type'] == 'roster' and m['action'] == 'left']
    assert [m['agent']['id'] for m in left] == ['cup']
    assert app.roster.member_ids() == ['computer']

    # 5. The event log has every picture, then compaction turns them into intervals.
    day = app.event_log.days()[0]
    raw = app.event_log.recent_detections(day, 10_000)
    assert len(raw) == len({e['frame_id'] for e in raw}) > 150
    types = [e['type'] for e in app.event_log.events(day, None, 1000)]
    assert types.count('agent_joined') == 2 and types.count('agent_left') == 1
    assert 'alert' in types and 'habit' in types
    assert app.event_log.compact(rig.clock.now())['frames'] == len(raw)
    intervals = app.event_log.presence(day, include_live=False)
    cups = [i for i in intervals if i['label'] == 'cup']
    laptops = [i for i in intervals if i['label'] == 'laptop']
    assert len(laptops) == 1 and len(cups) == 2           # the cup was away for 8 s, then gone
    assert laptops[0]['interval'][1] - laptops[0]['interval'][0] > 60


def test_an_unknown_object_gets_a_personality_from_the_ai(tmp_path):
    rig = DeskRig(tmp_path, ai_reply='A tiny bodybuilder who holds papers together and brags about it.')
    rig.watch(4, STAPLER)
    profile = rig.app.profiles.get('stapler')
    assert profile and profile.learned and 'bodybuilder' in profile.personality
    assert any(m['type'] == 'roster' and m['agent']['name'] == 'Stapler' for m in rig.messages())
    assert 'bodybuilder' in rig.app.store.load('learned_personalities')['stapler']


def test_a_pet_near_the_desk_gets_a_warning(tmp_path):
    rig = DeskRig(tmp_path)
    rig.watch(4, LAPTOP, CUP_FAR)
    rig.watch(1, LAPTOP, CUP_FAR, CAT)
    pets = [m for m in rig.messages() if m.get('alert') == 'pet_risk']
    assert pets and pets[0]['text'].startswith('Cat detected near the desk!') and 'Cup' in pets[0]['text']


def test_with_no_pictures_nobody_leaves(tmp_path):
    """No pictures = no events: agents don't all leave just because the Pi went quiet."""
    rig = DeskRig(tmp_path)
    rig.watch(4, LAPTOP)
    rig.clock.advance(600)
    rig.app.producer.tick(rig.clock.now())          # nothing new from the camera
    assert rig.app.roster.member_ids() == ['computer']


def test_a_page_that_opens_later_gets_the_recent_messages(tmp_path):
    rig = DeskRig(tmp_path)
    rig.watch(4, LAPTOP)
    recent = rig.app.hub.recent()
    assert recent[0]['type'] == 'roster' and recent[0]['text'] == 'Computer joined the chat'


# ── Pi pictures in each detector state ───────────────────────────────
def from_pi(rig: DeskRig, state: DetectorState, *found: Detection, number: int, reason: str = '',
            taken_at: float | None = None) -> UploadInfo:
    return UploadInfo(frame_id=f'pi-{number:06d}', taken_at=rig.clock.now() if taken_at is None else taken_at,
                      detector=state, detector_reason=reason, detections=found,
                      detect_ms=240.0 if state is DetectorState.OK else None)


def pi_pictures(rig: DeskRig, seconds: float, state: DetectorState, *found: Detection, reason: str = '') -> None:
    for number in range(1, round(seconds * 3) + 1):
        rig.detector.detections = [*found, PERSON]
        rig.picture(from_pi(rig, state, *found, PERSON, number=number, reason=reason) if state is DetectorState.OK
                    else from_pi(rig, state, number=number, reason=reason))
        rig.clock.advance(1 / 3)


def detection_events(rig: DeskRig) -> list[dict]:
    return list(reversed(rig.app.event_log.recent_detections(rig.app.event_log.days()[0], 10_000)))


def test_ok_pictures_use_the_pis_own_detections(tmp_path):
    rig = DeskRig(tmp_path)
    pi_pictures(rig, 4, DetectorState.OK, LAPTOP)
    assert rig.detector.calls == 0
    assert rig.app.roster.member_ids() == ['computer']
    events = detection_events(rig)
    assert len(events) == 12 and {e['detected_on'] for e in events} == {'pi'}
    assert rig.app.producer.last_detect_ms == 240.0


def test_busy_pictures_only_update_the_live_view(tmp_path):
    rig = DeskRig(tmp_path)
    pi_pictures(rig, 4, DetectorState.BUSY)
    assert rig.detector.calls == 0 and rig.app.event_log.days() == []
    assert rig.app.roster.member_ids() == [] and rig.app.producer.pictures_analysed == 0
    with rig.app.desk.lock:
        assert rig.app.desk.raw_frames[0] == rig.jpeg


def test_off_pictures_are_detected_on_this_computer_and_the_pi_says_why(tmp_path):
    rig = DeskRig(tmp_path)
    pi_pictures(rig, 4, DetectorState.OFF, reason='the detector container is not answering')
    assert rig.detector.calls == 12
    assert {e['detected_on'] for e in detection_events(rig)} == {'computer'}
    assert rig.app.roster.member_ids() == []                  # only a person was seen
    status = rig.app.pi_source.status()
    assert (status['detector'], status['detector_reason']) == ('off', 'the detector container is not answering')


def test_an_ok_picture_that_arrives_after_later_busy_ones_is_analysed_but_not_shown(tmp_path):
    rig = DeskRig(tmp_path)
    later, earlier = desk_jpeg(200), desk_jpeg(60)
    taken = rig.clock.now()
    rig.picture(from_pi(rig, DetectorState.BUSY, number=8, taken_at=taken + 0.3), jpeg=later)
    rig.clock.advance(0.3)
    rig.picture(from_pi(rig, DetectorState.OK, LAPTOP, number=7, taken_at=taken), jpeg=earlier)
    with rig.app.desk.lock:
        assert rig.app.desk.raw_frames[0] == later
    assert [e['frame_id'] for e in detection_events(rig)] == ['pi-000007']


def test_a_busy_picture_never_pushes_out_an_ok_one_not_analysed_yet(tmp_path):
    rig = DeskRig(tmp_path)
    assert rig.app.source.push(rig.jpeg, from_pi(rig, DetectorState.OK, LAPTOP, number=1))
    assert rig.app.source.push(rig.jpeg, from_pi(rig, DetectorState.BUSY, number=2))
    rig.app.producer.tick(rig.clock.now())
    [event] = detection_events(rig)
    assert event['frame_id'] == 'pi-000001' and event['detected_obj'][0]['label'] == 'laptop'


def test_each_analysed_picture_is_one_detection_event(tmp_path):
    rig = DeskRig(tmp_path)
    seen: list[DetectionEvent] = []
    rig.app.bus.subscribe('test', seen.append, types=[DetectionEvent])
    rig.watch(1, LAPTOP)
    assert [e.detections for e in seen] == [(LAPTOP, PERSON)] * 3
