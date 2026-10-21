"""The habit tracker: sips, breaks, phone-free time, food and light from detection events."""

from tests.conftest import HabitRig as Rig


def test_sip_when_the_cup_leaves_and_comes_back(settings, tmp_path):
    r = Rig(settings, tmp_path)
    r.run(10, 'cup')
    r.run(8)                       # cup picked up for 8 s
    r.run(5, 'cup')
    sips = r.said('sip')
    assert len(sips) == 1 and 'Sip detected' in sips[0]['say']
    assert r.tracker.d['sips'] == 1 and r.tracker.d['water_ml'] == 80
    # gone 3 minutes (refill in the kitchen?): not a sip
    r.run(180)
    r.run(5, 'cup')
    assert r.tracker.d['sips'] == 1


def test_a_short_flicker_is_not_a_sip(settings, tmp_path):
    r = Rig(settings, tmp_path)
    r.run(10, 'cup')
    r.run(1)                        # one second of missed detections
    r.run(5, 'cup')
    assert r.tracker.d['sips'] == 0


def test_break_when_away_five_minutes(settings, tmp_path):
    r = Rig(settings, tmp_path)
    r.run(40, 'laptop')
    sit_start = r.tracker.sit_since
    assert sit_start is not None and r.tracker.at_desk(r.t)
    r.run(2 * 60, 'laptop', person=False)                  # 2 min away: not a break yet
    r.run(5, 'laptop')
    assert r.tracker.d['breaks'] == 0 and r.tracker.sit_since == sit_start
    r.run(6 * 60, 'laptop', person=False)                  # 6 min away
    r.run(5, 'laptop')
    assert r.tracker.d['breaks'] == 1
    assert r.said('break')[0]['minutes'] >= 5
    assert r.tracker.sit_since > sit_start                 # the sitting clock restarted


def test_camera_off_for_a_while_counts_as_a_break(settings, tmp_path):
    r = Rig(settings, tmp_path)
    r.run(10, 'laptop')
    r.t += 7 * 60                   # no pictures for 7 minutes
    r.run(2, 'laptop')
    assert r.tracker.d['breaks'] == 1 and r.said('break')[0]['how'] == 'camera off'


def test_phone_free_time_and_streak(settings, tmp_path):
    settings.protocols.phone_free['celebrate_every_min'] = 1
    r = Rig(settings, tmp_path)
    r.run(70, 'laptop')                                   # 70 s without a phone
    assert r.tracker.d['phone_free_sec'] > 60
    assert len(r.said('streak')) == 1
    r.run(30, 'laptop', 'cell phone')
    assert r.tracker.phone_on_desk_since is not None and r.tracker.phone_free_streak == 0
    assert r.tracker.d['phone_on_desk_sec'] > 20
    state = r.tracker.rule_state(r.t)
    assert state['phone_on_desk_since'] is not None


def test_phone_not_counted_when_it_is_the_camera(settings, tmp_path):
    r = Rig(settings, tmp_path, phone_counts=False)
    r.run(20, 'laptop', 'cell phone')
    assert r.tracker.d['phone_free_sec'] == 0 and r.tracker.rule_state(r.t)['phone_on_desk_since'] is None


def test_food_logged_once_and_meal_finished(settings, tmp_path):
    r = Rig(settings, tmp_path)
    r.run(4 * 60, 'apple')
    r.run(5)
    food = r.said('logged')
    assert len(food) == 1 and food[0]['label'] == 'apple' and food[0]['healthy']
    assert r.tracker.meal_done_ts is not None and r.tracker.meal_name == 'apple'


def test_light_readings(settings, tmp_path):
    r = Rig(settings, tmp_path)
    r.run(3, 'laptop', light={'lux': 500, 'brightness': 180})
    assert r.tracker.bright_since is not None and r.tracker.dim_since is None
    r.run(3, 'laptop', light={'lux': 20, 'brightness': 30})
    assert r.tracker.bright_since is None and r.tracker.dim_since is not None


def test_manual_logs_and_saving(settings, tmp_path):
    r = Rig(settings, tmp_path)
    r.tracker.log('hydration', 'glass')
    r.tracker.log('hydration', 'sip')
    r.tracker.log('breaks', 'break')
    r.tracker.log('light', 'sunlight', minutes=15)
    r.tracker.log('food', 'food', label='almonds', healthy=True)
    today = r.tracker.today(r.t)
    assert today['hydration']['water_ml'] == 320 and today['breaks']['count'] == 1
    assert today['light']['sunlight_minutes'] == 15 and today['food']['entries'][0]['label'] == 'almonds'
    # a restart keeps today's numbers
    r2 = Rig(settings, tmp_path)
    r2.t = r.t
    assert r2.tracker.d['water_ml'] == 320 and r2.tracker.d['breaks'] == 1


def test_new_day_starts_fresh(settings, tmp_path):
    r = Rig(settings, tmp_path)
    r.tracker.log('hydration', 'glass')
    r.t += 86400
    r.run(1, 'laptop')
    assert r.tracker.d['water_ml'] == 0


def test_rule_state_feeds_the_protocols(settings, tmp_path):
    r = Rig(settings, tmp_path)
    r.run(5, 'cup', 'laptop')
    s = r.tracker.rule_state(r.t, open_todos=['Essay'])
    assert s['at_desk'] and s['drink_on_desk'] and s['screen_on_desk'] and s['open_todos'] == ['Essay']
    assert s['wake_ts'] < s['bed_ts']


def test_sip_counts_even_with_a_second_drink_on_the_desk(settings, tmp_path):
    r = Rig(settings, tmp_path)
    r.run(10, 'cup', 'bottle')
    r.run(8, 'bottle')             # the mug is picked up; the water bottle stays
    r.run(5, 'cup', 'bottle')
    assert r.tracker.d['sips'] == 1
