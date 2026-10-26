"""The desk monitor: safety alerts from each analysed picture (danger zones, desk edges, pets, clutter, plants)."""

import pytest

from desktalk.core.vision.analyzer import DeskAnalyzer
from desktalk.core.vision.desk_monitor import DeskMonitor
from desktalk.core.vision.rules import SafetyRules
from desktalk.domain import LENS, AgentProfile, Alert, BBox, Detection, DetectionEvent, Light
from tests.fakes import FakeDeskStatus, FakeHabits, FakePhotoTaker, FakeProfiles, InMemoryKeyValueStore, SyncEventBus
from tests.unit.test_core_vision_kit import FakeDesk, app_config, seen_object

T = 1_760_000_000          # a real time: the monitor counts 0 as "never alerted"
LAPTOP = (200, 200, 400, 350)
CUP_NEAR = (410, 250, 450, 320)          # inside the laptop's spill zone
CUP_FAR = (560, 380, 600, 450)

AGENTS = [
    AgentProfile('computer', 'Computer', labels=['laptop', 'keyboard', 'mouse'], habits=['breaks'], fragile=True),
    AgentProfile('cup', 'Cup', labels=['cup', 'bottle'], habits=['hydration']),
    AgentProfile('snack', 'Snack', labels=['banana', 'apple'], habits=['food']),
    AgentProfile('paper', 'Paper', labels=['book'], habits=['tasks'], fragile=True),
    AgentProfile('plant', 'Plant', labels=['potted plant', 'flower', 'houseplant']),
    AgentProfile('phone', 'Phone', labels=['cell phone'], habits=['phone_free'], fragile=True),
    AgentProfile('scissors', 'Scissors', labels=['scissors', 'knife']),
]


class Rig:
    """A desk monitor wired to fakes. look() is one analysed picture: the agents' objects on the desk
    (as the roster leaves them in the desk scene) and every detector word in the picture. show_pet() shows
    pets for just enough pictures for them to count."""

    def __init__(self, agents=AGENTS, warm=True, saved=None, words=None):
        self.bus = SyncEventBus()
        self.desk = FakeDesk()
        self.photos = FakePhotoTaker()
        self.habits = FakeHabits()
        self.status = FakeDeskStatus(warm=warm)
        self.profiles = FakeProfiles(agents, ignored=['person', 'chair'])
        self.settings = app_config().alerts
        prompts = app_config().prompts
        rules = SafetyRules(InMemoryKeyValueStore({'safety_rules': saved} if saved else {}), app_config().safety,
                            words=prompts.desk_rules)
        self.monitor = DeskMonitor(self.bus, self.desk, DeskAnalyzer(rules, words=prompts.desk_checks), self.profiles,
                                   self.habits, self.status, self.photos, self.settings,
                                   words=words or prompts.desk_alerts)

    def look(self, ts, agents=None, labels=(), size=(640, 480), confidence=0.9, box=(0, 0, 10, 10), chat=None):
        """Returns the alerts this picture caused. `agents` are on the desk; the ones in `chat` (all of them when
        it's None) are in the chat too. The words in `labels` are seen at `confidence`, in `box`."""
        agents = agents or {}
        seen = {a: seen_object(label, box) for a, (label, box) in agents.items()}
        self.desk.set_on_desk(seen, ts)
        self.desk.set_members({a: o for a, o in seen.items() if chat is None or a in chat}, ts)
        found = [Detection(label, 0.9, BBox(*box)) for label, box in agents.values()]
        found += [Detection(label, confidence, BBox(*box)) for label in labels]
        self.bus.publish(DetectionEvent(ts=ts, frame_id=f'pi-{int(ts)}', source='pi', size=size,
                                        detections=tuple(found), person=False, light=Light(brightness=120)))
        return [e for e in self.bus.published if isinstance(e, Alert) and e.ts == ts]

    def show_pet(self, ts, agents=None, labels=('cat',), chat=None):
        """The pets in `labels` are seen clearly in alerts.pet_min_pictures pictures in a row, a second
        apart, the last one at `ts`. Returns the alerts the last picture caused."""
        count = self.settings.pet_min_pictures
        for k in range(count - 1, 0, -1):
            self.look(ts - k, agents, labels, chat=chat)
        return self.look(ts, agents, labels, chat=chat)

    def alerts(self):
        return self.bus.of_type(Alert)

    def dangers(self):
        return [d.to_dict() for d in self.desk.now().dangers]

    def result(self):
        result = self.desk.now().result
        assert result is not None
        return result


def cup_near_laptop():
    return {'computer': ('laptop', LAPTOP), 'cup': ('cup', CUP_NEAR)}


# ── danger zones ──
def test_cup_in_the_laptop_zone_makes_the_fragile_laptop_speak_up():
    rig = Rig()
    [alert] = rig.look(T + 1000, cup_near_laptop())
    assert (alert.kind, alert.agent, alert.text) == ('proximity', 'computer', '')
    snap = alert.extra['snapshot']
    assert {k: v for k, v in alert.extra.items() if k != 'snapshot'} == {
        'follow_up': 0, 'near': 'cup', 'severity': 'high', 'rule_label': 'Spill / pressure zone', 'depth_px': 70}
    assert snap == 'photo-1' and rig.photos.saved == [('Proximity alert', (CUP_NEAR, LAPTOP))]
    assert rig.habits.bumps == [('danger_count', 1)]


PHONE = (200, 200, 300, 260)
KNIFE_NEAR = (310, 220, 330, 250)          # inside the phone's scratch zone


def knife_near_phone():
    return {'phone': ('cell phone', PHONE), 'scissors': ('knife', KNIFE_NEAR)}


def test_scissors_near_phone_still_warns():
    """Neither one is in the chat (scissors never join): the warning still goes out, from Lens."""
    [alert] = Rig().look(T + 1000, knife_near_phone(), chat=())
    assert (alert.kind, alert.agent, alert.extra['near']) == ('proximity', LENS, 'phone')
    assert alert.extra['rule_label'] == 'Screen scratch zone'


def test_fragile_member_speaks():
    [alert] = Rig().look(T + 1000, knife_near_phone(), chat={'phone'})
    assert (alert.agent, alert.extra['near']) == ('phone', 'scissors')


def test_the_risky_thing_speaks_when_the_fragile_one_is_not_in_the_chat():
    [alert] = Rig().look(T + 1000, cup_near_laptop(), chat={'cup'})
    assert (alert.agent, alert.extra['near']) == ('cup', 'computer')


def test_a_zone_owner_that_is_not_fragile_speaks_when_the_risky_thing_is_not_in_the_chat():
    agents = [AgentProfile('computer', 'Computer', labels=['laptop']), *AGENTS[1:]]
    [alert] = Rig(agents).look(T + 1000, cup_near_laptop(), chat={'computer'})
    assert (alert.agent, alert.extra['near']) == ('computer', 'cup')


def test_the_warnings_look_at_everything_on_the_desk_not_only_the_chat():
    rig = Rig()
    members = {'computer': seen_object('laptop', LAPTOP), 'cup': seen_object('cup', CUP_NEAR)}
    rig.desk.set_members(members, T)               # in the chat, but not on the desk list: no warning
    rig.desk.set_on_desk({}, T)
    rig.bus.publish(DetectionEvent(ts=T + 1000, frame_id='pi-1', source='pi', size=(640, 480), detections=(),
                                   person=False, light=Light(brightness=120)))
    assert rig.alerts() == [] and rig.result()['risk_level'] == 0


def test_when_the_zone_owner_is_not_fragile_the_risky_thing_speaks():
    agents = [AgentProfile('computer', 'Computer', labels=['laptop']), *AGENTS[1:]]
    [alert] = Rig(agents).look(T + 1000, cup_near_laptop())
    assert (alert.agent, alert.extra['near']) == ('cup', 'computer')


def test_alerts_about_the_same_pair_wait_longer_each_time():
    rig = Rig()
    for t in range(1000, 1601):
        rig.look(T + t, cup_near_laptop())
    assert [(a.ts - T, a.extra['follow_up']) for a in rig.alerts()] == [
        (1000, 0), (1050, 1), (1150, 2), (1350, 3), (1550, 4)]     # 50 s, 100 s, then at most 8 x 25 s


def test_a_pair_quiet_for_a_while_starts_over():
    rig = Rig()
    for t in (1000, 1050, 1150):
        rig.look(T + t, cup_near_laptop())
    [alert] = rig.look(T + 1150 + 301, cup_near_laptop())
    assert alert.extra['follow_up'] == 0
    [again] = rig.look(T + 1150 + 301 + 50, cup_near_laptop())     # the waits start over: 50 s, 100 s ...
    assert again.extra['follow_up'] == 1


def test_the_ai_is_told_about_recent_alerts():
    rig = Rig()
    rig.look(T + 1000, cup_near_laptop())
    assert rig.desk.recent_alerts(100) == [{'time': T + 1000, 'type': 'proximity', 'character': 'computer',
                                            'near': 'cup', 'severity': 'high', 'rule_label': 'Spill / pressure zone'}]


def test_only_the_latest_alerts_are_remembered_for_the_ai():
    rig = Rig()
    for k in range(40):
        rig.look(T + 1000 + 1000 * k, cup_near_laptop())
    assert len(rig.desk.recent_alerts(100)) == app_config().alerts.recent_kept == 30


# ── desk edges ──
def test_something_at_the_desk_edge():
    rig = Rig(saved={'desk_outline': [[0, 0], [1, 0], [1, 0.8], [0, 0.8]]})
    [alert] = rig.look(T + 1000, {'cup': ('cup', (400, 480, 470, 570))}, size=(960, 720))
    assert alert.agent == 'cup'
    assert {k: v for k, v in alert.extra.items() if k != 'snapshot'} == {
        'follow_up': 0, 'severity': 'medium', 'rule_label': 'Near front edge (6px)'}
    assert rig.dangers() == [{'pair': ['cup', 'desk_edge'], 'label': 'cup near front edge',
                                        'type': 'edge_danger'}]


def test_something_not_in_the_chat_at_the_desk_edge_is_said_by_lens():
    rig = Rig(saved={'desk_outline': [[0, 0], [1, 0], [1, 0.8], [0, 0.8]]})
    [alert] = rig.look(T + 1000, {'cup': ('cup', (400, 480, 470, 570))}, size=(960, 720), chat=())
    assert alert.agent == LENS and alert.extra['rule_label'] == 'Near front edge (6px)'


def test_desk_edges_use_the_size_of_the_picture():
    rig = Rig(saved={'desk_outline': [[0, 0], [1, 0], [1, 0.8], [0, 0.8]]})
    # in a picture twice as tall, the front edge is far below the cup
    assert rig.look(T + 1000, {'cup': ('cup', (400, 480, 470, 570))}, size=(960, 1440)) == []


# ── the desk's safety state ──
def test_safety_state_for_the_app_and_the_ai():
    rig = Rig()
    rig.look(T + 1000, cup_near_laptop())
    assert rig.desk.now().state == 'SAFE'            # one zone intrusion is risk level 1
    assert rig.dangers() == [{'pair': ['cup', 'laptop'], 'label': 'Spill / pressure zone',
                                        'depth_px': 70, 'type': 'zone_intrusion'}]
    assert rig.result()['risk_level'] == 1
    assert rig.desk.danger_since(frozenset({'cup', 'laptop'})) == T + 1000


def test_dangerous_from_the_risk_level_in_the_settings():
    rig = Rig()
    mouse = ('mouse', (405, 270, 415, 280))           # right between the cup and the laptop
    rig.look(T + 1000, {**cup_near_laptop(), 'mouse': mouse})
    assert rig.result()['risk_level'] >= app_config().alerts.dangerous_from_risk
    assert rig.desk.now().state == 'DANGEROUS'


def test_a_danger_keeps_its_start_time_until_it_is_gone():
    rig = Rig()
    rig.look(T + 1000, cup_near_laptop())
    rig.look(T + 1010, cup_near_laptop())
    assert rig.desk.danger_since(frozenset({'cup', 'laptop'})) == T + 1000
    rig.look(T + 1020, {'computer': ('laptop', LAPTOP), 'cup': ('cup', CUP_FAR)})
    assert rig.desk.danger_since(frozenset({'cup', 'laptop'})) is None and rig.dangers() == []


# ── pets ──
UNSURE = 0.5          # how sure the detector was about a blurry cat


def test_a_pet_near_the_desk():
    rig = Rig()
    [alert] = rig.show_pet(T + 1000, {'computer': ('laptop', LAPTOP), 'cup': ('cup', CUP_FAR)})
    assert (alert.kind, alert.scenario, alert.feature, alert.agent) == ('scenario', 'risk_detection', 'pet_risk',
                                                                       'computer')
    assert alert.text == 'Cat detected near the desk! Cup could get knocked over! Careful!'


def test_a_pet_with_nothing_to_knock_over_and_nobody_fragile():
    [alert] = Rig().show_pet(T + 1000, {'cup2': ('remote', (10, 10, 50, 50))}, labels=['dog'])
    assert (alert.agent, alert.text) == ('lens', 'Dog detected near the desk! Careful!')


def test_a_pet_is_said_by_lens_when_the_fragile_one_is_not_in_the_chat():
    [alert] = Rig().show_pet(T + 1000, {'computer': ('laptop', LAPTOP), 'cup': ('cup', CUP_FAR)}, chat={'cup'})
    assert alert.agent == LENS
    assert alert.text == 'Cat detected near the desk! Cup could get knocked over! Careful!'


def test_an_arm_close_to_the_camera_that_looks_a_bit_like_a_cat_is_not_a_pet():
    rig = Rig()
    for k in range(10):
        rig.look(T + 1000 + k, labels=['cat'], confidence=(0.55, 0.7)[k % 2], box=(0, 0, 640, 240))
    assert rig.alerts() == []


def test_a_real_cat_counts_once_it_is_seen_in_enough_pictures():
    rig = Rig()
    count = rig.settings.pet_min_pictures
    for k in range(count - 1):
        assert rig.look(T + 1000 + k, labels=['cat']) == []
    [alert] = rig.look(T + 1000 + count - 1, labels=['cat'])
    assert alert.text.startswith('Cat detected near the desk!')


def look_at_cat(rig, pictures):
    """One picture per entry, a second apart: 'good' = a clear cat, 'blurry' = no cat found,
    'unsure' = a cat the detector isn't sure about. Returns the alerts each picture caused."""
    seen = []
    for k, kind in enumerate(pictures):
        labels = [] if kind == 'blurry' else ['cat']
        confidence = UNSURE if kind == 'unsure' else 0.9
        seen.append(rig.look(T + 1000 + k, labels=labels, confidence=confidence))
    return seen


def test_a_cat_seen_clearly_in_enough_of_the_last_pictures_counts_with_misses_in_between():
    rig = Rig()
    s = rig.settings
    misses = ['blurry', 'unsure'] * s.pet_window_pictures
    pictures = ['good', *misses[:s.pet_window_pictures - s.pet_min_pictures], *['good'] * (s.pet_min_pictures - 1)]
    *before, last = look_at_cat(rig, pictures)
    assert before == [[]] * (len(pictures) - 1)
    assert [a.feature for a in last] == ['pet_risk']


def test_a_cat_seen_clearly_in_too_few_of_the_last_pictures_does_not_count():
    rig = Rig()
    s = rig.settings
    misses = ['blurry', 'unsure'] * s.pet_window_pictures
    # one more miss than allowed, so the first clear pictures drop out of the memory
    pictures = [*['good'] * (s.pet_min_pictures - 1), *misses[:s.pet_window_pictures - s.pet_min_pictures + 1], 'good']
    look_at_cat(rig, pictures)
    assert rig.alerts() == []


def test_a_cat_that_left_before_a_long_gap_is_not_counted_again():
    """The cat is seen, an alert goes out and the cat leaves. Then no pictures are analysed for a few
    minutes (the Pi was busy, say). The first picture after the gap has no cat, so no alert, even though
    most of the last few pictures had one and the wait between alerts is over."""
    rig = Rig()
    s = rig.settings
    look_at_cat(rig, ['good'] * s.pet_window_pictures)
    assert len(rig.alerts()) == 1
    after_gap = T + 1000 + s.pet_window_pictures + s.pet_cooldown_sec + s.pet_forget_after_sec
    assert rig.look(after_gap) == []
    assert len(rig.alerts()) == 1


def test_pictures_with_a_short_gap_between_still_count_towards_a_pet():
    rig = Rig()
    s = rig.settings
    gap = s.pet_forget_after_sec            # at the limit, not over it
    for k in range(s.pet_min_pictures - 1):
        assert rig.look(T + 1000 + k * gap, labels=['cat']) == []
    [alert] = rig.look(T + 1000 + (s.pet_min_pictures - 1) * gap, labels=['cat'])
    assert alert.feature == 'pet_risk'


def test_pet_alerts_wait_between():
    rig = Rig()
    assert rig.show_pet(T + 1000)
    assert rig.show_pet(T + 1100) == []
    assert rig.show_pet(T + 1121)


def test_two_pets_in_the_order_of_the_settings():
    alerts = Rig().show_pet(T + 1000, labels=['dog', 'cat'])
    assert [a.text.split()[0] for a in alerts] == ['Cat', 'Dog']


# ── clutter ──
CLUTTER = ['cup', 'book', 'pen', 'phone', 'keys', 'remote', 'person', 'chair']


def test_lots_of_things_on_the_desk():
    rig = Rig()
    [alert] = rig.look(T + 1000, {'paper': ('book', (10, 10, 50, 50))}, labels=CLUTTER)
    assert (alert.scenario, alert.feature, alert.agent) == ('reminders', 'organization', 'paper')
    assert alert.text == 'I count 6 things on the desk right now. Maybe tidy up a bit?'


def test_clutter_is_said_by_lens_when_the_tasks_agent_is_not_there():
    [alert] = Rig().look(T + 1000, labels=CLUTTER)
    assert alert.agent == 'lens'


def test_clutter_is_said_by_lens_when_the_tasks_agent_is_on_the_desk_but_not_in_the_chat():
    [alert] = Rig().look(T + 1000, {'paper': ('book', (10, 10, 50, 50))}, labels=CLUTTER, chat=())
    assert alert.agent == LENS


def test_clutter_waits_until_the_camera_has_watched_a_while():
    assert Rig(warm=False).look(T + 1000, labels=CLUTTER) == []


def test_clutter_is_mentioned_at_most_every_half_hour():
    rig = Rig()
    assert rig.look(T + 1000, labels=CLUTTER)
    assert rig.look(T + 1000 + 1800, labels=CLUTTER) == []
    assert rig.look(T + 1000 + 1801, labels=CLUTTER)


def test_ignored_things_are_not_clutter():
    assert Rig().look(T + 1000, labels=CLUTTER[:5] + ['person', 'chair']) == []


# ── plants ──
def test_a_plant_on_the_desk_asks_for_water_once_a_day():
    rig = Rig()
    [alert] = rig.look(T + 1000, {'plant': ('potted plant', (10, 10, 50, 50))}, labels=[])
    assert (alert.scenario, alert.feature, alert.agent) == ('reminders', 'water_plants', 'plant')
    assert alert.text == 'I can see a plant on the desk. Has it had any water today?'
    assert rig.look(T + 1000 + 86400, {'plant': ('potted plant', (10, 10, 50, 50))}) == []
    assert rig.look(T + 1000 + 86401, {'plant': ('potted plant', (10, 10, 50, 50))})


def test_a_plant_on_the_desk_but_not_in_the_chat_is_mentioned_by_lens():
    [alert] = Rig().look(T + 1000, {'plant': ('potted plant', (10, 10, 50, 50))}, chat=())
    assert (alert.feature, alert.agent) == ('water_plants', LENS)


@pytest.mark.parametrize('warm, said', [(True, ['lens']), (False, [])])
def test_a_plant_that_is_not_an_agent_is_mentioned_by_lens_when_warm(warm, said):
    assert [a.agent for a in Rig(warm=warm).look(T + 1000, labels=['flower'])] == said


def test_it_listens_only_to_detection_events():
    rig = Rig()
    assert [s['name'] for s in rig.bus.stats()] == ['desk-monitor']
    rig.bus.publish(Alert(ts=1, kind='scenario', text='hi'))
    assert rig.bus.stats()[0]['handled'] == 0


def test_no_photo_means_the_alert_still_goes_out_without_one():
    rig = Rig()
    rig.photos.has_picture = False
    [alert] = rig.look(T + 1000, cup_near_laptop())
    assert alert.extra['snapshot'] is None


def test_the_alert_words_come_from_prompts_yaml():
    words = app_config().prompts.desk_alerts.model_copy(update={
        'photo_label': 'Too close', 'pet': '{pet}!{knock}', 'pet_knock': ' Mind {names}.', 'clutter': '{count} things.',
        'plant': 'Water me.', 'edge_reason': '{edge} side, {distance}px', 'edge_danger': '{object} at {edge}'})
    rig = Rig(words=words)
    rig.look(T + 1000, cup_near_laptop())
    assert rig.photos.saved[0][0] == 'Too close'
    [alert] = rig.show_pet(T + 3000, {'computer': ('laptop', LAPTOP), 'cup': ('cup', CUP_FAR)})
    assert alert.text == 'Cat! Mind Cup.'
    [alert] = rig.look(T + 5000, {'paper': ('book', (10, 10, 50, 50))}, labels=CLUTTER)
    assert alert.text == '6 things.'
    [alert] = rig.look(T + 7000, {'plant': ('potted plant', (10, 10, 50, 50))})
    assert alert.text == 'Water me.'
    rig = Rig(saved={'desk_outline': [[0, 0], [1, 0], [1, 0.8], [0, 0.8]]}, words=words)
    [alert] = rig.look(T + 1000, {'cup': ('cup', (400, 480, 470, 570))}, size=(960, 720))
    assert alert.extra['rule_label'] == 'front side, 6px' and rig.dangers()[0]['label'] == 'cup at front'
