"""Domain types: typed events (same JSON as today's dict events), pictures, errors."""

from __future__ import annotations

import numpy as np
import pytest

from desktalk.domain import (
    AgentJoined,
    AgentLeft,
    Alert,
    BBox,
    CameraNotice,
    Compaction,
    Detection,
    DetectionEvent,
    DetectorState,
    Frame,
    HabitEvent,
    Light,
    NudgeSent,
    ObjectStateChanged,
    event_from_dict,
)
from desktalk_shared.wire import UploadInfo

CUP = Detection('cup', 0.81, BBox(10, 20, 50, 100))


def detection_event(**kw):
    base = dict(ts=1_760_000_000.0, frame_id='pi-000123', source='pi', size=(1280, 720), detections=(CUP,),
                person=False, moved=False, light=Light(brightness=112.4, lux=240.0), taken_at=1_759_999_999.9,
                detected_on='pi')
    base.update(kw)
    return DetectionEvent(**base)


def test_detection_event_dict_matches_todays_shape():
    d = detection_event(id='1-abc').to_dict()
    assert d == {'id': '1-abc', 'ts': 1_760_000_000.0, 'type': 'detection', 'frame_id': 'pi-000123', 'source': 'pi',
                 'size': [1280, 720], 'detected_obj': [CUP.to_dict()], 'person': False, 'moved': False,
                 'light': {'brightness': 112.4, 'lux': 240.0}, 'taken_at': 1_759_999_999.9, 'detected_on': 'pi'}


@pytest.mark.parametrize('event', [
    detection_event(),
    AgentJoined(ts=1.0, agent='cup', name='Cup', label='cup', conf=0.8),
    AgentLeft(ts=2.0, agent='cup', name='Cup', label='cup', stayed_sec=31.5),
    Alert(ts=3.0, kind='scenario', scenario='lost_object', feature='keys-1', agent='lens', text='Keys by the lamp'),
    Alert(ts=3.5, kind='proximity', agent='cup', extra={'snapshot': 'snap-1', 'follow_up': 2}),
    HabitEvent(ts=4.0, habit='hydration', action='sip', say='Nice sip!', extra={'ml': 50}),
    NudgeSent(ts=5.0, habit='breaks', rule='sit-50', agent='lamp', evidence='sitting 52 min', text='Stretch?'),
    CameraNotice(ts=6.0, key='dark', kind='problem', text='It is too dark to see the desk'),
    Compaction(ts=7.0, frames=900, intervals=12, from_ts=1.0, to_ts=6.0),
    ObjectStateChanged(ts=8.0, change='moved', label='bottle', prev_state='PRESENT', new_state='PRESENT',
                       device_id='pi-1', conf_avg=0.8, bbox=(10.0, 20.0, 50.0, 100.0), dwell_sec=12.5),
    ObjectStateChanged(ts=9.0, change='appeared', label='cup', prev_state='ABSENT', new_state='PRESENT',
                       device_id='pi-1'),
])
def test_every_event_round_trips_through_its_dict(event):
    assert event_from_dict(event.to_dict()) == event


def test_unknown_fields_are_kept_in_extra_and_written_back():
    d = {'id': 'x', 'ts': 1.0, 'type': 'habit', 'habit': 'light', 'action': 'sun', 'say': '', 'lux': 900}
    ev = event_from_dict(d)
    assert isinstance(ev, HabitEvent) and ev.extra == {'lux': 900}
    assert ev.to_dict() == {**d, 'quiet': False}


def test_compaction_uses_from_and_to_keys():
    d = Compaction(ts=7.0, frames=1, intervals=1, from_ts=1.0, to_ts=2.0).to_dict()
    assert (d['from'], d['to']) == (1.0, 2.0)


def test_unknown_event_type_is_an_error():
    with pytest.raises(ValueError, match='party'):
        event_from_dict({'id': 'x', 'ts': 1.0, 'type': 'party'})


def test_missing_required_field_names_it():
    with pytest.raises(ValueError, match='agent'):
        event_from_dict({'id': 'x', 'ts': 1.0, 'type': 'agent_joined', 'name': 'Cup', 'label': 'cup', 'conf': 0.8})


def test_events_get_unique_ids():
    a, b = AgentLeft(ts=1, agent='a', name='A', label='a', stayed_sec=1), AgentLeft(ts=1, agent='a', name='A',
                                                                                     label='a', stayed_sec=1)
    assert a.id != b.id


def test_frame_from_phone_has_no_detector_and_numbered_id():
    f = Frame(picture=np.zeros((4, 4, 3), np.uint8), received_at=10.0, source='phone', number=7)
    assert f.detector_state is None
    assert f.frame_id == 'phone-000007'
    assert f.pi_detections == ()


def test_frame_from_pi_uses_the_pi_id_and_detections():
    info = UploadInfo(frame_id='pi-000042', taken_at=9.5, detector=DetectorState.OK, detections=(CUP,))
    f = Frame(picture=np.zeros((4, 4, 3), np.uint8), received_at=10.0, source='pi', number=1, pi=info)
    assert f.detector_state is DetectorState.OK
    assert f.frame_id == 'pi-000042'
    assert f.pi_detections == (CUP,)


def test_object_state_event_says_object_in_its_dict():
    event = ObjectStateChanged(id='2-abc', ts=8.0, change='removed', label='bottle', prev_state='PRESENT',
                               new_state='ABSENT', device_id='pi-1', conf_avg=0.8, bbox=(10.0, 20.0, 50.0, 100.0),
                               dwell_sec=61.0)
    d = event.to_dict()
    assert d == {'id': '2-abc', 'ts': 8.0, 'type': 'object_state', 'change': 'removed', 'object': 'bottle',
                 'prev_state': 'PRESENT', 'new_state': 'ABSENT', 'device_id': 'pi-1', 'conf_avg': 0.8,
                 'bbox': [10.0, 20.0, 50.0, 100.0], 'dwell_sec': 61.0}
    assert event_from_dict(d).bbox == (10.0, 20.0, 50.0, 100.0)    # a tuple again, not a list
