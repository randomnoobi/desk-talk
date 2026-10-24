"""Agents joining and leaving the chat (desktalk/core/agents/roster.py)."""

import shutil
from pathlib import Path

import pytest

from desktalk.config import load_config
from desktalk.core.agents.profiles import ProfileBook
from desktalk.core.agents.roster import Roster
from desktalk.core.desk import Scene
from desktalk.domain import AgentJoined, AgentLeft, Alert, BBox, Detection, DetectionEvent, Light, SeenObject
from desktalk.ports import RosterView
from tests.fakes import InMemoryKeyValueStore, SyncEventBus

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope='module')
def config(tmp_path_factory):
    root = tmp_path_factory.mktemp('project')
    (root / 'config').mkdir()
    for name in ('settings.yaml', 'agents.yaml', 'protocols.yaml', 'prompts.yaml'):
        shutil.copy(ROOT / 'config' / name, root / 'config' / name)
    return load_config(root, environ={})


class Rig:
    """A roster on a synchronous bus, fed made-up pictures."""

    def __init__(self, config, on_desk=None, **agent_settings):
        self.bus = SyncEventBus()
        self.desk = Scene()
        self.profiles = ProfileBook(config.agents_file, InMemoryKeyValueStore(), personality_max_chars=400)
        agents = config.agents.model_copy(update=agent_settings)
        self.roster = Roster(self.bus, self.profiles, self.desk, agents, config.presence, on_desk=on_desk)

    def see(self, ts, *dets, size=(640, 480)):
        self.bus.publish(DetectionEvent(ts=ts, frame_id=f'pi-{ts}', source='pi', size=size, detections=tuple(dets),
                                        person=False, light=Light(brightness=120)))

    def run(self, start, seconds, *dets, size=(640, 480)):
        """Pictures 3 a second for `seconds`. Returns the time after the last one."""
        n = int(seconds * 3)
        for i in range(n):
            self.see(start + i / 3, *dets, size=size)
        return start + n / 3

    def joined(self):
        return [e for e in self.bus.of_type(AgentJoined)]

    def left(self):
        return [e for e in self.bus.of_type(AgentLeft)]


def d(label, conf=0.9, box=(100, 100, 200, 200)):
    return Detection(label, conf, BBox(*box))


def test_it_is_a_roster_view(config):
    assert isinstance(Rig(config).roster, RosterView)


def test_object_joins_and_leaves(config):
    r = Rig(config)
    t = r.run(1000.0, 4, d('laptop'), d('person'))           # 4 seconds at 3 pictures a second
    assert [(e.agent, e.name, e.label, e.conf) for e in r.joined()] == [('computer', 'Computer', 'laptop', 0.9)]
    assert r.joined()[0].ts == pytest.approx(1003.0)          # seen for 3 s (agents.join_after_sec)
    assert r.roster.member_ids() == ['computer']
    assert set(r.desk.members()) == {'computer'}                # person is ignored
    r.run(t, 50, d('person'))                                 # 50 s with no laptop
    assert [(e.agent, e.name, e.label) for e in r.left()] == [('computer', 'Computer', 'laptop')]
    left = r.left()[0]
    assert left.stayed_sec == pytest.approx(round(left.ts - 1003.0, 1))
    assert left.ts - t == pytest.approx(45.0, abs=0.5)        # gone 45 s (agents.leave_after_sec)
    assert r.roster.member_ids() == [] and r.desk.members() == {}
    snap = r.roster.snapshot()
    assert snap[0]['id'] == 'computer' and snap[0]['on_desk'] is False and snap[0]['left_at'] == left.ts


def test_join_and_leave_times_come_from_the_settings(config):
    r = Rig(config, join_after_sec=1, leave_after_sec=5)
    t = r.run(0.0, 1.5, d('laptop'))
    assert r.joined()[0].ts == pytest.approx(1.0)
    r.run(t, 6)
    assert r.left()[0].ts - t == pytest.approx(5.0, abs=0.5)


def test_several_labels_make_one_agent_seen_as_its_best_word(config):
    r = Rig(config)
    r.run(0.0, 4, d('cup', 0.7), d('bottle', 0.8, (300, 100, 350, 200)))
    assert [(e.agent, e.label, e.conf) for e in r.joined()] == [('cup', 'bottle', 0.8)]
    snap = r.roster.snapshot()[0]
    assert snap['label_seen'] == 'bottle' and snap['confidence'] == 0.8


def test_unknown_objects_still_join(config):
    r = Rig(config)
    r.run(0.0, 4, d('stapler'))
    assert [(e.agent, e.name) for e in r.joined()] == [('stapler', 'Stapler')]


def test_weak_detections_dont_count(config):
    r = Rig(config)
    for i in range(15):
        r.see(i / 3, d('cup', 0.5))           # cup needs 0.55 (agents.yaml)
        r.see(i / 3, d('laptop', 0.3))        # below the general 0.45 (agents.min_confidence)
    assert r.joined() == []


def test_general_confidence_comes_from_the_settings(config):
    r = Rig(config, min_confidence=0.2)
    r.run(0.0, 4, d('laptop', 0.3))
    assert [e.agent for e in r.joined()] == ['computer']


def test_only_objects_on_the_desk_join(config):
    seen_points = []

    def bottom_half(x, y, width, height):     # the desk is the bottom half of the picture
        seen_points.append((x, y, width, height))
        return y >= height / 2

    r = Rig(config, on_desk=bottom_half)
    r.run(0.0, 4, d('cup', box=(100, 20, 150, 100)),         # above the desk
          d('laptop', box=(200, 300, 400, 450)))             # on the desk
    assert [e.agent for e in r.joined()] == ['computer']
    assert (300, 450, 640, 480) in seen_points                # where the laptop touches the desk


def test_objects_off_the_desk_join_when_only_on_desk_is_off(config):
    r = Rig(config, on_desk=lambda x, y, w, h: False, only_on_desk=False)
    r.run(0.0, 4, d('laptop'))
    assert [e.agent for e in r.joined()] == ['computer']


def test_desk_check_is_skipped_when_the_picture_size_is_unknown(config):
    r = Rig(config, on_desk=lambda x, y, w, h: False)
    r.run(0.0, 4, d('laptop'), size=(0, 0))
    assert [e.agent for e in r.joined()] == ['computer']


def test_max_agents(config):
    r = Rig(config, max_agents=2)
    t = r.run(0.0, 4, d('laptop'), d('book', box=(300, 300, 400, 400)), d('scissors', box=(10, 10, 50, 50)))
    assert len(r.joined()) == 2
    first_two = {e.agent for e in r.joined()}
    gone = next(iter(first_two))
    keep = (first_two - {gone}).pop()
    boxes = {'computer': d('laptop'), 'paper': d('book', box=(300, 300, 400, 400)),
             'scissors': d('scissors', box=(10, 10, 50, 50))}
    r.run(t, 50, *[boxes[a] for a in boxes if a != gone])     # one leaves: the waiting one gets its seat
    assert [e.agent for e in r.left()] == [gone]
    assert set(r.roster.member_ids()) == {keep} | (set(boxes) - first_two)


def test_desk_objects_are_the_members_seen_just_now(config):
    r = Rig(config)
    t = r.run(100.0, 4, d('laptop', box=(10, 20, 30, 40)))
    assert r.desk.members() == {'computer': SeenObject(label='laptop', box=(10.0, 20.0, 30.0, 40.0),
                                                       center=(20.0, 30.0), confidence=0.9, cam=0)}
    assert r.desk.first_seen('computer') == pytest.approx(103.0)
    assert r.desk.last_seen('computer') == pytest.approx(t - 1 / 3)
    t = r.run(t, 3)                                            # out of sight 3 s: still a member...
    assert r.roster.member_ids() == ['computer']
    assert r.desk.members() == {}                                # ...but not on the desk right now (2.5 s)


def test_camera_off_freezes_the_roster(config):
    """No pictures = no events: agents don't all 'leave' just because the Pi went quiet."""
    r = Rig(config)
    r.run(0.0, 4, d('laptop'))
    assert r.roster.member_ids() == ['computer'] and r.left() == []


def test_snapshot_lists_members_and_earlier_agents_newest_first(config):
    r = Rig(config)
    t = r.run(0.0, 4, d('laptop'))
    t = r.run(t, 4, d('laptop'), d('cup', box=(300, 300, 350, 350)))
    snap = r.roster.snapshot()
    assert [a['id'] for a in snap] == ['cup', 'computer']
    cup = snap[0]
    assert cup['on_desk'] is True and cup['left_at'] is None and cup['label_seen'] == 'cup'
    assert cup['name'] == 'Cup' and cup['tracker'] == 'hydration'
    assert 'personality' not in cup and 'hello' not in cup              # private parts stay private
    assert set(cup) == {'id', 'name', 'labels', 'object', 'nickname', 'icon', 'art', 'color', 'about', 'habits',
                        'tracker', 'fragile', 'auto', 'learned', 'on_desk', 'joined_at', 'left_at', 'label_seen',
                        'confidence'}


def test_an_agent_that_left_has_no_seen_label_in_the_snapshot(config):
    r = Rig(config)
    t = r.run(0.0, 4, d('laptop'))
    r.run(t, 50)
    snap = r.roster.snapshot()[0]
    assert 'label_seen' not in snap and 'confidence' not in snap and snap['joined_at'] == pytest.approx(3.0)


def test_other_events_are_not_handled(config):
    r = Rig(config)
    r.bus.publish(Alert(ts=1.0, kind='proximity', agent='cup', text='hi'))
    assert r.bus.stats() == [{'name': 'roster', 'handled': 0, 'waiting': 0, 'dropped': 0, 'errors': 0}]
