"""
The desk fixes on the whole app built by bootstrap (with fakes): only the objects that matter join the chat, a
sip is a drink lifted and put back, warnings and reminders also pop up on the Mac, and the to-do list the server
keeps reminds about due dates and logs a ticked-off to-do.
"""

from __future__ import annotations

from desktalk.domain import BBox, Detection
from tests.integration.app_kit import CUP_FAR, LAPTOP, DeskRig, changed, desk_config
from tests.integration.web_kit import web_client

GLASSES = Detection('glasses', 0.9, BBox(100, 380, 160, 420))
CUP_UP = Detection('cup', 0.9, BBox(560, 300, 600, 370))           # CUP_FAR lifted towards your mouth
PHONE = Detection('cell phone', 0.9, BBox(300, 380, 360, 440))
SCISSORS = Detection('scissors', 0.9, BBox(330, 400, 380, 450))    # inside the phone's screen scratch zone
BOOK = Detection('book', 0.9, BBox(60, 300, 180, 380))             # Book, who keeps the to-do list
TOMORROW = '2026-10-22'                                             # the rig's day is 2026-10-21


def only_tasks(config):
    """Only the to-do reminders are on, with a one-minute warm-up."""
    for rule in ('hydration', 'breaks', 'light', 'phone_free', 'food'):
        config = changed(config, f'protocols.{rule}', enabled=False)
    return changed(config, 'reminders', warmup_min=1)


def test_glasses_never_join_but_the_cup_does(tmp_path):
    rig = DeskRig(tmp_path)
    rig.watch(rig.config.agents.leave_after_sec / 2, CUP_FAR, GLASSES)
    assert rig.app.roster.member_ids() == ['cup']
    assert 'glasses' in rig.app.scene.on_desk()                      # the warnings still see them
    assert rig.app.chat_joins is not None
    [refused] = rig.app.chat_joins.joins('glasses')
    assert refused.kind == 'refused'
    assert [j.kind for j in rig.app.chat_joins.joins('cup')] == ['joined']


def test_a_mug_also_seen_as_a_slightly_surer_vase_still_joins_as_the_cup(tmp_path):
    rig = DeskRig(tmp_path)
    cup = Detection('cup', 0.50, CUP_FAR.bbox)
    vase = Detection('vase', 0.52, CUP_FAR.bbox)                     # no block of its own in agents.yaml
    rig.watch(rig.config.agents.leave_after_sec / 2, vase, cup)
    assert rig.app.roster.member_ids() == ['cup']


def test_a_sip_the_camera_sees_is_logged_once_and_pops_up(tmp_path):
    rig = DeskRig(tmp_path)
    rig.watch(4, LAPTOP, CUP_FAR)
    rig.messages()
    rig.watch(1, LAPTOP, CUP_UP)                                     # lifted...
    rig.watch(4, LAPTOP)                                             # ...drunk from...
    rig.watch(3, LAPTOP, CUP_FAR)                                    # ...and put back
    sips = [m for m in rig.messages() if m.get('habit') == 'hydration' and m['type'] == 'group_message']
    assert len(sips) == 1 and 'notify' not in sips[0]                # the page never sees the pop-up mark
    assert rig.app.habits.today(rig.clock.now())['hydration']['sips'] == 1
    assert [title for title, _ in rig.desktop.shown] == [rig.config.prompts.notifications.sip_title]


def test_scissors_near_a_phone_not_in_the_chat_get_a_warning_from_lens(tmp_path):
    rig = DeskRig(tmp_path)
    rig.app.preferences.update({'sensitivity': 0})                  # Picky: the phone takes 10 s to join
    rig.watch(rig.config.agents.on_desk_after_sec + 1, LAPTOP, PHONE, SCISSORS)
    assert 'phone' not in rig.app.roster.member_ids()
    warnings = [m for m in rig.messages() if m.get('alert') == 'proximity' and m['character'] == 'lens']
    assert warnings and 'scissors' in warnings[0]['text'] and 'cell phone' in warnings[0]['text']
    lens_name = rig.config.prompts.lens.card.name
    assert (lens_name, warnings[0]['text']) in rig.desktop.shown


def test_a_to_do_due_tomorrow_reminds_and_ticking_it_off_is_logged(tmp_path):
    rig = DeskRig(tmp_path, config=only_tasks(desk_config(tmp_path)))
    client = web_client(rig.app)
    essay = {'text': 'Essay', 'done': False, 'due': TOMORROW}
    sent = client.post('/api/chat_todos', json={'chat': 'paper', 'widget': {'type': 'todo', 'items': [essay]}})
    assert sent.json() == {'ok': True}
    assert client.get('/api/todos').json() == {'has_list': True, 'items': [essay]}

    rig.watch(2 * 60, LAPTOP, BOOK, step=1.0)
    rig.messages()
    rig.tick(rig.app.reminders)
    [nudge] = [m for m in rig.messages() if m['type'] == 'group_message']
    assert (nudge['habit'], nudge['character']) == ('tasks', 'paper')
    assert 'Essay' in nudge['text'] and 'tomorrow' in nudge['text']
    assert ('Book', nudge['text']) in rig.desktop.shown

    done = {**essay, 'done': True}
    client.post('/api/chat_todos', json={'chat': 'paper', 'widget': {'type': 'todo', 'items': [done]}})
    events = client.get('/api/log/events', params={'types': 'habit'}).json()['events']
    assert [(e['habit'], e['action']) for e in events] == [('tasks', 'done')]
