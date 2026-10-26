"""Agents joining and leaving the chat (desktalk/core/agents/roster.py)."""

import shutil
from pathlib import Path

import pytest

from desktalk.config import load_config
from desktalk.core.agents.profiles import ProfileBook
from desktalk.core.agents.roster import Roster
from desktalk.core.desk import Scene
from desktalk.domain import AgentJoined, AgentLeft, Alert, BBox, Detection, DetectionEvent, Light, SeenObject
from desktalk.ports import ChatJoins, RosterList, RosterView
from tests.fakes import FakePreferences, InMemoryKeyValueStore, SyncEventBus

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope='module')
def config(tmp_path_factory):
    root = tmp_path_factory.mktemp('project')
    (root / 'config').mkdir()
    for name in ('settings.yaml', 'agents.yaml', 'protocols.yaml', 'prompts.yaml'):
        shutil.copy(ROOT / 'config' / name, root / 'config' / name)
    return load_config(root, environ={})


@pytest.fixture(scope='module')
def strict_cup_config(tmp_path_factory):
    """The real settings, with agents.yaml giving the cup a stricter min_confidence of its own (0.55)."""
    root = tmp_path_factory.mktemp('strict')
    (root / 'config').mkdir()
    for name in ('settings.yaml', 'protocols.yaml', 'prompts.yaml'):
        shutil.copy(ROOT / 'config' / name, root / 'config' / name)
    agents = (ROOT / 'config' / 'agents.yaml').read_text()
    agents = agents.replace('    tracker: hydration\n', '    tracker: hydration\n    min_confidence: 0.55\n', 1)
    (root / 'config' / 'agents.yaml').write_text(agents)
    return load_config(root, environ={})


class Rig:
    """A roster on a synchronous bus, fed made-up pictures."""

    def __init__(self, config, on_desk=None, sensitivity=None, joins_kept=None, **agent_settings):
        self.bus = SyncEventBus()
        self.desk = Scene()
        self.prefs = FakePreferences({} if sensitivity is None else {'sensitivity': sensitivity})
        self.profiles = ProfileBook(config.agents_file, InMemoryKeyValueStore(), personality_max_chars=400)
        # join_after_sec and min_confidence change the sensitivity step in use (settings → agents.sensitivity_steps)
        step = {k: agent_settings.pop(k) for k in ('join_after_sec', 'min_confidence') if k in agent_settings}
        agents = config.agents.model_copy(update=agent_settings)
        if step:
            steps = list(agents.sensitivity_steps)
            steps[agents.sensitivity_default] = agents.default_step().model_copy(update=step)
            agents = agents.model_copy(update={'sensitivity_steps': tuple(steps)})
        self.roster = Roster(self.bus, self.profiles, self.desk, agents, config.presence, self.prefs,
                             on_desk=on_desk, joins_kept=joins_kept or config.dev.trace_events_kept)

    def see(self, ts, *dets, size=(640, 480)):
        self.bus.publish(DetectionEvent(ts=ts, frame_id=f'pi-{ts}', source='pi', size=size, detections=tuple(dets),
                                        person=False, light=Light(brightness=120)))

    def run(self, start, seconds, *dets, size=(640, 480)):
        """Pictures 3 a second for `seconds`. Returns the time after the last one."""
        n = int(seconds * 3)
        for i in range(n):
            self.see(start + i / 3, *dets, size=size)
        return start + n / 3

    def seen_until(self, start, end, *dets, every=0.1):
        """Pictures every `every` seconds from `start` to `end`, both included."""
        for i in range(round((end - start) / every) + 1):
            self.see(round(start + i * every, 3), *dets)

    def joined(self):
        return [e for e in self.bus.of_type(AgentJoined)]

    def left(self):
        return [e for e in self.bus.of_type(AgentLeft)]


def d(label, conf=0.9, box=(100, 100, 200, 200)):
    return Detection(label, conf, BBox(*box))


def test_it_is_a_roster_view(config):
    roster = Rig(config).roster
    assert isinstance(roster, RosterView) and isinstance(roster, RosterList) and isinstance(roster, ChatJoins)


def test_object_joins_and_leaves(config):
    r = Rig(config)
    t = r.run(1000.0, 4, d('laptop'), d('person'))           # 4 seconds at 3 pictures a second
    assert [(e.agent, e.name, e.label, e.conf) for e in r.joined()] == [('computer', 'Computer', 'laptop', 0.9)]
    assert r.joined()[0].ts == pytest.approx(1003.0)          # seen for 3 s (the Normal sensitivity step)
    assert r.roster.member_ids() == ['computer']
    assert set(r.desk.members()) == {'computer'}                # person is ignored
    r.run(t, config.agents.leave_after_sec + 5, d('person'))  # 5 s longer than the wait, with no laptop
    assert [(e.agent, e.name, e.label) for e in r.left()] == [('computer', 'Computer', 'laptop')]
    left = r.left()[0]
    assert left.stayed_sec == pytest.approx(round(left.ts - 1003.0, 1))
    assert left.ts - t == pytest.approx(config.agents.leave_after_sec, abs=0.5)   # gone this long
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


def test_unknown_object_never_joins(config):
    r = Rig(config)
    r.run(0.0, 30, d('remote'), d('stapler', box=(300, 300, 350, 350)))
    assert r.joined() == [] and r.roster.member_ids() == []
    assert [j.kind for j in r.roster.joins('remote')] == ['refused']


def test_glasses_never_join(config):
    r = Rig(config)
    r.run(0.0, 30, d('glasses', 0.9))
    assert r.joined() == []
    refused = r.roster.joins('glasses')
    assert len(refused) == 1                      # one for the whole stretch of sightings, not one per picture
    assert (refused[-1].kind, refused[-1].agent, refused[-1].label) == ('refused', 'glasses', 'glasses')
    assert refused[-1].ts == pytest.approx(3.0) and refused[-1].seen_for_sec == pytest.approx(3.0)


def test_a_refused_object_is_refused_again_after_it_was_gone(config):
    r = Rig(config)
    t = r.run(0.0, 4, d('glasses'))
    t = r.run(t, config.agents.leave_after_sec + 5)
    r.run(t, 4, d('glasses'))
    assert [j.kind for j in r.roster.joins('glasses')] == ['refused', 'refused']


def first_join_ts(r):
    return r.joined()[0].ts if r.joined() else None


def test_cup_joins_after_step_wait(config):
    normal = Rig(config, sensitivity=2)
    normal.seen_until(0.0, 2.9, d('cup'))
    assert normal.joined() == []
    normal.see(3.0, d('cup'))
    assert first_join_ts(normal) == pytest.approx(3.0)
    picky = Rig(config, sensitivity=0)
    picky.seen_until(0.0, 9.9, d('cup'))
    assert picky.joined() == []
    picky.see(10.0, d('cup'))
    assert first_join_ts(picky) == pytest.approx(10.0)


def test_slider_change_works_at_once(config):
    r = Rig(config, sensitivity=2)
    t = r.run(0.0, 4, d('laptop'))
    assert [e.agent for e in r.joined()] == ['computer']
    r.prefs.update({'sensitivity': 0})                        # moved to Picky
    t = r.run(t, 9.5, d('laptop'), d('cup', box=(300, 300, 350, 350)))
    assert [e.agent for e in r.joined()] == ['computer']      # 9.5 s is not enough any more
    r.run(t, 1, d('laptop'), d('cup', box=(300, 300, 350, 350)))
    assert [e.agent for e in r.joined()] == ['computer', 'cup']
    assert r.joined()[1].ts == pytest.approx(4.0 + 10.0)


def test_weak_box_counts_at_eager_not_picky(config):
    eager = Rig(config, sensitivity=4)
    eager.run(0.0, 4, d('laptop', 0.4))
    assert [e.agent for e in eager.joined()] == ['computer']
    picky = Rig(config, sensitivity=0)
    picky.run(0.0, 30, d('laptop', 0.4))
    assert picky.joined() == []


def test_profile_own_min_confidence_wins_when_stricter(strict_cup_config):
    config = strict_cup_config
    eager = Rig(config, sensitivity=4)                        # Eager wants 0.35, the cup itself 0.55
    eager.run(0.0, 10, d('cup', 0.5))
    assert eager.joined() == []
    picky = Rig(config, sensitivity=0)                        # Picky wants 0.65: more than the cup's own
    picky.run(0.0, 30, d('cup', 0.6))
    assert picky.joined() == []
    picky.run(30.0, 11, d('cup', 0.7))
    assert [e.agent for e in picky.joined()] == ['cup']


@pytest.mark.parametrize('bad', ['x', None, 99, -1, 2.5, True, [1]])
def test_bad_sensitivity_uses_default(config, bad):
    r = Rig(config)
    r.prefs.values['sensitivity'] = bad
    r.seen_until(0.0, 2.9, d('laptop', 0.46))
    assert r.joined() == []
    r.see(3.0, d('laptop', 0.46))                             # Normal: 0.45 and 3 s
    assert first_join_ts(r) == pytest.approx(3.0)
    assert r.roster.joins('laptop')[-1].step == 'Normal'
    weak = Rig(config)
    weak.prefs.values['sensitivity'] = bad
    weak.run(0.0, 10, d('laptop', 0.44))
    assert weak.joined() == []


def test_on_desk_list_has_non_chat_objects(config):
    r = Rig(config)
    r.seen_until(0.0, 2.9, d('scissors'))
    assert r.desk.on_desk() == {}                             # not seen long enough yet
    r.see(3.0, d('scissors', box=(10, 20, 30, 40)))
    assert r.desk.on_desk() == {'scissors': SeenObject(label='scissors', box=(10.0, 20.0, 30.0, 40.0),
                                                       center=(20.0, 30.0), confidence=0.9, cam=0)}
    assert r.desk.members() == {}
    r.run(3.1, 3)                                             # gone longer than presence.disappear_after_sec
    assert r.desk.on_desk() == {}


def test_on_desk_list_has_chat_members_and_unknown_things_but_not_ignored_words(config):
    r = Rig(config)
    r.run(0.0, 4, d('laptop'), d('remote', box=(300, 300, 350, 350)), d('person', box=(0, 0, 50, 50)))
    assert set(r.desk.on_desk()) == {'computer', 'remote'} and set(r.desk.members()) == {'computer'}


def test_on_desk_list_has_its_own_numbers_not_the_slider(config):
    r = Rig(config, sensitivity=0)                            # Picky: 0.65 and 10 s for the chat
    r.run(0.0, 4, d('scissors', 0.5))
    assert set(r.desk.on_desk()) == {'scissors'}             # on_desk_min_confidence 0.45, on_desk_after_sec 3
    low = Rig(config, sensitivity=4)                          # Eager: 0.35 for the chat
    low.run(0.0, 4, d('scissors', 0.4))
    assert low.desk.on_desk() == {}


def test_on_desk_list_only_has_things_on_the_marked_desk(config):
    r = Rig(config, on_desk=lambda x, y, w, h: y >= h / 2)
    r.run(0.0, 4, d('scissors', box=(100, 20, 150, 100)), d('remote', box=(200, 300, 400, 450)))
    assert set(r.desk.on_desk()) == {'remote'}


def test_join_records_reason(config):
    r = Rig(config)
    r.run(0.0, 4, d('cup', 0.8))
    join = r.roster.joins('cup')[-1]
    assert (join.kind, join.agent, join.label, join.confidence, join.step) == ('joined', 'cup', 'cup', 0.8, 'Normal')
    assert join.seen_for_sec >= 3 and join.ts == pytest.approx(3.0)
    assert join.on_desk is None                               # no desk outline


def test_leave_records_reason(config):
    r = Rig(config, on_desk=lambda x, y, w, h: True)
    t = r.run(0.0, 4, d('cup', 0.8))
    r.run(t, config.agents.leave_after_sec + 5)
    joined, left = r.roster.joins('cup')
    assert (joined.kind, joined.on_desk) == ('joined', True)
    assert (left.kind, left.agent, left.confidence, left.step, left.on_desk) == ('left', 'cup', 0.8, 'Normal', True)
    assert left.ts == r.left()[0].ts and left.seen_for_sec == pytest.approx(t - 1 / 3, abs=0.01)


def test_only_the_newest_joins_are_kept(config):
    r = Rig(config, joins_kept=2)
    t = 0.0
    for _ in range(3):
        t = r.run(t, 4, d('glasses'))
        t = r.run(t, config.agents.leave_after_sec + 5)
    assert [j.kind for j in r.roster.joins('glasses')] == ['refused', 'refused']
    assert r.roster.joins('glasses')[0].ts > 100


def test_weak_detections_dont_count(config):
    r = Rig(config)
    for i in range(15):
        r.see(i / 3, d('cup', 0.44), d('laptop', 0.3, box=(300, 300, 400, 400)))   # below the Normal step's 0.45
    assert r.joined() == []


def test_a_cup_follows_the_slider_like_everything_else(config):
    # the camera often sees a water bottle as a cup at about 0.46: enough for the Normal step
    r = Rig(config)
    r.run(0.0, 4, d('cup', 0.46))
    assert [e.agent for e in r.joined()] == ['cup']


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
    t = r.run(0.0, 4, d('laptop'), d('book', box=(300, 300, 400, 400)), d('cell phone', box=(10, 10, 50, 50)))
    assert len(r.joined()) == 2
    first_two = {e.agent for e in r.joined()}
    gone = next(iter(first_two))
    keep = (first_two - {gone}).pop()
    boxes = {'computer': d('laptop'), 'paper': d('book', box=(300, 300, 400, 400)),
             'phone': d('cell phone', box=(10, 10, 50, 50))}
    # one leaves: the waiting one gets its seat
    r.run(t, config.agents.leave_after_sec + 5, *[boxes[a] for a in boxes if a != gone])
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
    r.run(t, config.agents.leave_after_sec + 5)
    snap = r.roster.snapshot()[0]
    assert 'label_seen' not in snap and 'confidence' not in snap and snap['joined_at'] == pytest.approx(3.0)


def test_other_events_are_not_handled(config):
    r = Rig(config)
    r.bus.publish(Alert(ts=1.0, kind='proximity', agent='cup', text='hi'))
    assert r.bus.stats() == [{'name': 'roster', 'handled': 0, 'waiting': 0, 'dropped': 0, 'errors': 0}]
