"""Lost items: "have you seen my keys?" Sightings from the detector and a vision scan, alerts, and answers."""

from __future__ import annotations

import time
from collections.abc import Sequence
from pathlib import Path

import numpy as np

from desktalk.config import AiReplies, AiReply, LostItem, LostItemsSettings, load_config
from desktalk.core.chat.workers.lost_item_words import TrackedItems
from desktalk.core.chat.workers.lost_items import LostItems
from desktalk.core.chat.workers.lost_items_answers import LostItemsDesk
from desktalk.domain import AgentProfile, Alert, BBox, Box, Detection, DetectionEvent, Light, SeenObject, Size
from desktalk.util import format_local_ts
from tests.fakes import (
    FakeChatModel,
    FakeClock,
    FakeDeskScene,
    FakeHabits,
    FakeLivePictures,
    FakePhotoAlbum,
    FakePictureTools,
    FakeProfiles,
    StaticRoster,
    SyncEventBus,
)

ITEMS = {
    'phone': LostItem(name='Phone', labels=('cell phone',), alert_after_min=10,
                      words=('phone', 'iphone', 'mobile', 'cell', 'cellphone', 'android')),
    'keys': LostItem(name='Keys', labels=('key', 'remote'), alert_after_min=15,
                     words=('key', 'keys', 'keychain', 'keyring', 'fob')),
    'wallet': LostItem(name='Wallet', labels=('wallet', 'handbag'), alert_after_min=15,
                       words=('wallet', 'purse', 'card', 'cards', 'bag', 'handbag')),
    'airpods': LostItem(name='AirPods', labels=('earphone', 'earbuds'), alert_after_min=15,
                        words=('airpod', 'airpods', 'earbud', 'earbuds', 'earphone', 'earphones')),
}
SETTINGS = LostItemsSettings(check_every_sec=60, scene_change=8.0, rescan_every_sec=300, snapshot_every_sec=120,
                             spotted_photo_cooldown_sec=75, start_delay_sec=5, compare_size=64, min_confidence=0.3,
                             just_seen_sec=90, detector_fresh_sec=5, nearby_kept=5, nearby_mentioned=3,
                             answer_min_words=3, question_max_chars=200, message_max_chars=500,
                             photo_note_max_chars=450, live_note_max_chars=220, items=ITEMS)
REPLIES = AiReplies.model_construct(lost_alert=AiReply(max_tokens=60, temperature=0.7),
                                    lost_answer=AiReply(max_tokens=200, temperature=0.35))
PROFILES = FakeProfiles([AgentProfile(id='computer', name='Computer', object='laptop', habits=['screens']),
                         AgentProfile(id='keys', name='Keys', object='keys'),
                         AgentProfile(id='paper', name='Paper', object='notepad', habits=['tasks'], temperature=0.5),
                         AgentProfile(id='lamp', name='Lamp'), AgentProfile(id='pen', name='Pen')])
WORDS = load_config(environ={}, dotenv=Path('/nonexistent')).prompts.lost_items
KEPT = 150
CTA = {'label': WORDS.captures_button, 'action': 'captures'}
NO_AI = FakeChatModel(ready=False)
TOOLS = FakePictureTools()


def picture(value: int) -> bytes:
    jpeg = TOOLS.encode_jpeg(np.full((48, 64, 3), value, np.uint8), quality=85)
    assert jpeg is not None
    return jpeg


DARK, BRIGHT = picture(20), picture(230)


class FakeLostItemsDesk(FakeLivePictures, FakeDeskScene, FakePhotoAlbum):
    def __init__(self):
        FakeLivePictures.__init__(self)
        FakeDeskScene.__init__(self)
        FakePhotoAlbum.__init__(self, KEPT)


class AlbumPhotos:
    """A PhotoTaker that keeps the live view picture in the desk's album, tagged like the real proof photos."""

    def __init__(self, desk: FakeLostItemsDesk, clock: FakeClock):
        self._desk, self._clock = desk, clock

    def save(self, label: str, crop_boxes: Sequence[Box] = ()) -> str | None:
        jpeg = self._desk.boxed()
        if not jpeg:
            return None
        return self._desk.add(jpeg, label, self._clock.now(), labels=list(self._desk.seen()),
                              vlm_labels=sorted(self._desk.scan_labels()))


class Rig:
    def __init__(self, chat=NO_AI, vision=None, members=('computer',), phone_is_camera=False, settings=SETTINGS,
                 words=WORDS, utc_offset_hours=0.0):
        self.clock = FakeClock(time.mktime((2026, 10, 21, 14, 0, 0, 0, 0, -1)), utc_offset_hours=utc_offset_hours)
        self.bus = SyncEventBus()
        self.desk = FakeLostItemsDesk()
        self.habits = FakeHabits()
        self.chat, self.vision = chat, vision
        self.proof_photos = AlbumPhotos(self.desk, self.clock)
        self.lost = LostItems(self.clock, self.bus, self.desk, StaticRoster(members), PROFILES, self.habits, chat,
                              vision, settings, REPLIES, photos=self.proof_photos, default_temperature=0.8,
                              pictures=TOOLS, prompts=words, photos_kept=KEPT, phone_is_camera=phone_is_camera)
        self.show(DARK)

    def show(self, jpeg):
        """The newest picture from the camera."""
        self.desk.put(0, jpeg, jpeg, Size(64, 48))

    def tag(self, *labels):
        """What the desk scene says is in the newest picture (what the photos are tagged with)."""
        boxes = {label: SeenObject(label=label, box=(50, 50, 80, 70), center=(65, 60), confidence=0.9)
                 for label in labels}
        self.desk.note_picture(boxes, self.clock.now(), person=False, moved=False, new_session=False)

    def see(self, *labels, conf=0.9, after=0.0):
        """One analysed picture with these things in it."""
        self.clock.advance(after)
        found = tuple(Detection(label, conf, BBox(50, 50, 80, 70)) for label in labels)
        self.tag(*labels)
        self.bus.publish(DetectionEvent(ts=self.clock.now(), frame_id='pi-000001', source='pi', size=(640, 480),
                                        detections=found, person=False, light=Light(brightness=120)))

    def tick(self, after=0.0):
        self.clock.advance(after)
        return self.lost.tick(self.clock.now())

    def alerts(self):
        return [(a.feature, a.text) for a in self.bus.of_type(Alert)]

    def photos(self):
        return [p.label for p in reversed(self.desk.newest(KEPT))]


def test_the_fake_desk_has_what_lost_items_need():
    assert isinstance(FakeLostItemsDesk(), LostItemsDesk)


# ── words ──
def test_the_items_and_their_words_come_from_the_settings():
    lost = Rig().lost
    assert lost.items['keys']['name'] == 'Keys' and lost.items['keys']['labels'] == {'key', 'remote'}
    items = TrackedItems(SETTINGS, WORDS)
    assert items.in_text('have you seen my keys?') == ['keys']
    assert items.in_text('Where is my iPhone and my wallet') == ['phone', 'wallet']


def test_a_lost_question_needs_a_question_and_a_thing():
    lost = Rig().lost
    assert lost.is_lost_query('where did i put my airpods')
    assert lost.is_lost_query('Have you seen my laptop?')
    assert not lost.is_lost_query('where is the sun')
    assert not lost.is_lost_query('my keys are great')


def test_the_question_words_come_from_the_prompt_settings():
    lost = Rig(words=WORDS.model_copy(update={'triggers': ('any sign of my',)})).lost
    assert lost.is_lost_query('any sign of my keys?')
    assert not lost.is_lost_query('have you seen my keys?')


def test_detector_words_and_vision_words_find_the_item():
    items = TrackedItems(SETTINGS, WORDS)
    assert items.in_labels({'earphone'}) == {'airpods'}
    assert items.in_labels({'car keys'}) == {'keys'}
    assert items.in_labels({'keyboard'}) == set()
    assert items.in_labels({'keyboard keys'}) == set()
    assert items.in_labels({'Cell Phone', 'cup'}) == {'phone'}


# ── sightings from the detector ──
def test_a_detection_is_a_sighting_with_the_agents_nearby():
    rig = Rig(members=('computer', 'keys'))
    rig.see('key', 'cup')
    seen = rig.lost.sightings()
    assert seen == {'keys': {'last_seen': rig.clock.now(), 'alerted': False, 'source': 'detector',
                             'last_near': ['Computer']}}


def test_a_weak_box_is_not_a_sighting():
    rig = Rig()
    rig.see('key', conf=0.2)
    assert rig.lost.sightings() == {}


def test_an_item_that_turns_up_gets_a_spotted_photo_but_not_too_often():
    rig = Rig()
    rig.see('key')                     # the first picture is the starting point: no photo
    assert rig.photos() == []
    rig.see(after=1)
    rig.see('key', after=1)
    assert rig.photos() == ['Spotted: Keys']
    rig.see(after=1)
    rig.see('key', after=60)
    assert rig.photos() == ['Spotted: Keys']
    rig.see(after=1)
    rig.see('key', after=15)
    assert rig.photos() == ['Spotted: Keys', 'Spotted: Keys']
    assert rig.desk.newest(1)[0].ts == rig.clock.now()


# ── alerts ──
def test_an_item_gone_for_its_alert_time_is_mentioned_once():
    rig = Rig()
    rig.see('key')
    rig.see()
    assert rig.tick(after=15 * 60 - 1) == 60
    assert rig.alerts() == []
    rig.tick(after=1)
    assert rig.alerts() == [('keys', "Keys: hasn't been seen for 15 min. Last seen near: Computer.")]
    [alert] = rig.bus.of_type(Alert)
    assert (alert.kind, alert.scenario, alert.agent) == ('scenario', 'lost_object', 'lens')
    assert rig.habits.bumps == [('lost_alerts', 1)]
    rig.tick(after=60)
    assert len(rig.alerts()) == 1


def test_the_alert_names_only_a_few_agents_nearby():
    rig = Rig(members=('computer', 'paper', 'lamp', 'pen'))
    rig.see('key')
    rig.see()
    rig.tick(after=15 * 60)
    assert rig.alerts()[0][1].endswith('Last seen near: Computer, Paper, Lamp.')


def test_the_ai_words_the_alert():
    ai = FakeChatModel("Hey, it's Keys! I was last near Computer. Come find me?")
    rig = Rig(chat=ai)
    rig.see('key')
    rig.see()
    rig.tick(after=15 * 60)
    assert rig.alerts() == [('keys', "Hey, it's Keys! I was last near Computer. Come find me?")]
    call = ai.calls[0]
    assert (call['max_tokens'], call['temperature'], call['background']) == (60, 0.7, True)
    assert 'Keys has been missing for 15 minute(s). Last seen near: Computer.' in call['messages'][1]['content']


def test_the_built_in_alert_when_the_ai_cannot_answer():
    rig = Rig(chat=FakeChatModel(fail='Gemini is out of quota for today'))
    rig.see('key')
    rig.see()
    rig.tick(after=15 * 60)
    assert rig.alerts() == [('keys', "Keys: hasn't been seen for 15 min. Last seen near: Computer.")]


def test_no_alert_for_an_item_still_in_the_picture():
    rig = Rig()
    rig.see('key')
    rig.tick(after=20 * 60)
    assert rig.alerts() == []


def test_no_phone_alert_when_the_phone_is_the_camera():
    rig = Rig(phone_is_camera=True)
    rig.see('cell phone')
    rig.see()
    rig.tick(after=30 * 60)
    assert rig.alerts() == []


def test_an_item_found_again_can_be_mentioned_again_later():
    rig = Rig()
    rig.see('key')
    rig.see()
    rig.tick(after=15 * 60)
    rig.see('key', after=1)
    rig.see(after=1)
    rig.tick(after=15 * 60)
    assert [feature for feature, _ in rig.alerts()] == ['keys', 'keys']


def test_no_alerts_without_a_picture():
    rig = Rig()
    rig.see('key')
    rig.see()
    rig.desk.plain_pictures.clear()
    rig.tick(after=20 * 60)
    assert rig.alerts() == []


# ── the vision scan ──
def test_the_vision_scan_finds_small_things_the_detector_missed():
    vision = FakeChatModel('Sure: {"visible": ["keys", "mug"]}')
    rig = Rig(vision=vision)
    rig.tick()
    assert vision.calls[0]['jpeg'] == DARK and vision.calls[0]['background']
    assert 'Phone, Keys, Wallet, AirPods' in vision.calls[0]['prompt']
    assert rig.lost.sightings()['keys'] == {'last_seen': rig.clock.now(), 'alerted': False, 'source': 'vision',
                                            'last_near': ['Computer', 'keys', 'mug']}
    assert rig.desk.scan_labels() == {'keys', 'mug'}


def test_the_scan_runs_when_the_picture_changes_or_after_a_while():
    vision = FakeChatModel('{"visible": []}')
    rig = Rig(vision=vision)
    rig.tick()
    rig.tick(after=60)
    assert len(vision.calls) == 1
    rig.show(BRIGHT)
    rig.tick(after=60)
    assert len(vision.calls) == 2
    rig.tick(after=60)
    rig.tick(after=60)
    rig.tick(after=60)
    rig.tick(after=60)
    assert len(vision.calls) == 2
    rig.tick(after=60)
    assert len(vision.calls) == 3


def test_a_fresh_detector_sighting_wins_over_the_scan():
    rig = Rig(vision=FakeChatModel('{"visible": ["keys"]}'))
    rig.see('key')
    rig.tick(after=2)
    assert rig.lost.sightings()['keys']['source'] == 'detector'


def test_an_item_the_scan_still_sees_is_not_alerted():
    rig = Rig(vision=FakeChatModel('{"visible": ["keys"]}'))
    rig.see('key')
    rig.see()
    rig.show(BRIGHT)
    rig.tick(after=15 * 60)
    assert rig.alerts() == []


def test_a_scan_that_fails_changes_nothing():
    rig = Rig(vision=FakeChatModel(fail='Ollama is not running'))
    rig.desk.set_scan_labels({'mug'})
    assert rig.tick() == 60
    assert rig.lost.sightings() == {} and rig.desk.scan_labels() == {'mug'}


def test_scan_photos_spotted_items_and_the_desk_now_and_then():
    answers = ['{"visible": []}', '{"visible": ["wallet"]}', '{"visible": ["wallet"]}']
    rig = Rig(vision=FakeChatModel(answers))
    rig.desk.boxed_pictures.clear()
    rig.tick()
    assert rig.photos() == []                        # no live view picture to take a photo of yet
    rig.show(BRIGHT)
    rig.tick(after=60)
    assert rig.photos() == ['Spotted: Wallet (vision)', 'Desk scan']
    rig.show(DARK)
    rig.tick(after=60)
    assert rig.photos() == ['Spotted: Wallet (vision)', 'Desk scan']
    rig.show(BRIGHT)
    rig.tick(after=60)
    assert rig.photos() == ['Spotted: Wallet (vision)', 'Desk scan', 'Desk scan']


def test_the_first_scan_is_only_the_starting_point():
    rig = Rig(vision=FakeChatModel('{"visible": ["wallet"]}'))
    rig.tick()
    assert rig.photos() == ['Desk scan']


# ── answering "have you seen my keys?" ──
def test_the_answer_says_when_and_where_the_item_was_seen():
    ai = FakeChatModel('Your keys were right next to Computer half a minute ago!')
    rig = Rig(chat=ai)
    rig.see('key')
    rig.clock.advance(30)
    reply = rig.lost.answer('have you seen my keys?')
    assert reply == {'speaker': 'lens', 'cta': CTA, 'text': 'Your keys were right next to Computer half a minute ago!'}
    call = ai.calls[0]
    assert (call['max_tokens'], call['temperature'], call['background']) == (200, 0.35, False)
    assert call['messages'][0]['content'].startswith('You are Lens, the camera in a desk-objects group chat.')
    assert '- your Keys: seen on the desk within ~30s (' in call['messages'][1]['content']
    assert ') near Computer.' in call['messages'][1]['content']


def test_the_tasks_agent_answers_when_it_is_in_the_chat():
    ai = FakeChatModel('Found them!')
    rig = Rig(chat=ai, members=('computer', 'paper'))
    rig.see('key')
    reply = rig.lost.answer('where are my keys')
    assert reply['speaker'] == 'paper'
    assert ai.calls[0]['messages'][0]['content'].startswith('You are Paper, the notepad')


def test_the_built_in_answer_for_a_recent_sighting():
    rig = Rig()
    rig.see('key')
    rig.clock.advance(30)
    text = rig.lost.answer('have you seen my keys?')['text']
    assert text.startswith("I've got your Keys on the desk within the last minute or so (")
    assert text.endswith(') near Computer.')


def test_the_built_in_answer_for_an_older_sighting():
    rig = Rig()
    rig.see('key')
    rig.clock.advance(10 * 60 + 5)
    assert 'about 10 minute(s) ago near Computer.' in rig.lost.answer('have you seen my keys?')['text']


def test_the_answer_says_the_time_on_the_clocks_time_zone():
    rig = Rig(utc_offset_hours=5.5)
    rig.see('key')
    seen_at = rig.clock.now()
    rig.clock.advance(10 * 60 + 5)
    text = rig.lost.answer('have you seen my keys?')['text']
    assert f'around {format_local_ts(seen_at, utc_offset_sec=5.5 * 3600)},' in text


def test_the_built_in_answer_when_the_item_was_never_seen():
    text = Rig().lost.answer('have you seen my keys?')['text']
    assert text == ("I'm not seeing a saved sighting of your Keys on the desk since we started. Captures (in the "
                    "sidebar, or the button below) has every desk photo with its time.")


def test_a_never_seen_item_gets_a_look_at_a_saved_photo_tagged_with_it():
    rig = Rig(vision=FakeChatModel('Your keys are under the notebook on the left.'))
    rig.desk.boxed_pictures[0] = BRIGHT
    rig.tag('remote')
    photo = rig.proof_photos.save('Spotted: Keys')
    rig.tag()
    text = rig.lost.answer('have you seen my keys?')['text']
    assert text.endswith('A quick look at the live picture: Your keys are under the notebook on the left.')
    [call] = rig.vision.calls                      # the photo was enough: no look at the live picture
    saved = rig.desk.get(photo) if photo else None
    assert saved is not None and call['jpeg'] == saved.jpeg == BRIGHT and not call['background']
    assert 'Spotted: Keys' in call['prompt']


def test_a_saved_photo_answer_that_says_too_little_falls_back_to_the_live_picture():
    rig = Rig(vision=FakeChatModel(['Not sure.', 'Visible: left of the laptop.']))
    rig.tag('key')
    rig.proof_photos.save('Spotted: Keys')
    rig.tag()
    text = rig.lost.answer('have you seen my keys?')['text']
    assert text.endswith('A quick look at the live picture: Visible: left of the laptop.')
    assert rig.vision.calls[1]['jpeg'] == DARK


def test_a_never_seen_item_gets_a_look_at_the_live_picture():
    rig = Rig(vision=FakeChatModel('Not visible: no keys on the desk.'))
    text = rig.lost.answer('have you seen my keys?')['text']
    assert text.endswith('A quick look at the live picture: Not visible: no keys on the desk.')
    assert rig.vision.calls[0]['jpeg'] == DARK


def test_one_answer_makes_no_more_ai_calls_than_the_page_waits_for():
    """Every item named and never spotted, each in a saved photo the vision AI says too little about: two looks
    per item, then the chat AI's answer (the page's wait is sized with lost_items.most_ai_calls)."""
    vision, chat = FakeChatModel('Not sure.'), FakeChatModel('Not on the desk, sorry.')
    rig = Rig(chat=chat, vision=vision)
    rig.tag('cell phone', 'key', 'wallet', 'earphone')
    rig.proof_photos.save('Desk scan')
    rig.tag()
    rig.lost.answer('have you seen my phone, keys, wallet or airpods?')
    assert len(vision.calls) + len(chat.calls) == SETTINGS.most_ai_calls() == 9


def test_how_much_of_the_vision_ais_answers_is_kept_comes_from_the_settings():
    short = SETTINGS.model_copy(update={'photo_note_max_chars': 12, 'live_note_max_chars': 9})
    rig = Rig(vision=FakeChatModel('Not visible: no keys on the desk.'), settings=short)
    assert rig.lost.answer('have you seen my keys?')['text'].endswith('A quick look at the live picture: Not visib')
    rig = Rig(vision=FakeChatModel('Your keys are under the notebook on the left.'), settings=short)
    rig.tag('key')
    rig.proof_photos.save('Spotted: Keys')
    rig.tag()
    assert rig.lost.answer('have you seen my keys?')['text'].endswith('live picture: Your keys ar')


def test_how_much_of_your_question_the_ais_are_shown_comes_from_the_settings():
    short = SETTINGS.model_copy(update={'question_max_chars': 12, 'message_max_chars': 16})
    rig = Rig(vision=FakeChatModel('Yes, it is there.'), settings=short)
    rig.lost.answer('have you seen my laptop anywhere?')
    assert '"have you see"' in rig.vision.calls[0]['prompt']
    chat = FakeChatModel('Your keys were by the laptop a minute ago.')
    rig = Rig(chat=chat, settings=short)
    rig.lost.answer('have you seen my keys anywhere?')
    assert 'User wrote:\nhave you seen my\n' in chat.calls[0]['messages'][1]['content']


def test_a_question_about_something_else_gets_a_look_at_the_live_picture():
    rig = Rig(vision=FakeChatModel('Yes, your laptop is in the middle of the desk.'))
    reply = rig.lost.answer('have you seen my laptop?')
    assert reply == {'speaker': 'lens', 'cta': CTA, 'text': 'Yes, your laptop is in the middle of the desk.'}


def test_a_question_about_something_else_without_a_vision_ai():
    text = Rig().lost.answer('have you seen my laptop?')['text']
    assert text == WORDS.not_spotted and text.startswith("I checked the desk camera but couldn't confidently spot")


def test_the_answer_words_come_from_the_prompt_settings():
    words = WORDS.model_copy(update={'not_spotted': 'No luck.', 'captures_button': 'Photos',
                                     'answer_system': '{name} the {object} looks for things.'})
    assert Rig(words=words).lost.answer('have you seen my laptop?') == {
        'speaker': 'lens', 'cta': {'label': 'Photos', 'action': 'captures'}, 'text': 'No luck.'}
    ai = FakeChatModel('Found them!')
    Rig(chat=ai, words=words).lost.answer('have you seen my keys?')
    assert ai.calls[0]['messages'][0]['content'] == 'Lens the camera looks for things.'


def test_the_facts_plain_answers_photo_labels_and_alert_come_from_the_prompt_settings():
    words = WORDS.model_copy(update={
        'answer_as': WORDS.answer_as.model_copy(update={'name': 'Eye', 'object': 'webcam'}),
        'answer_user': '{message} | {facts}', 'just_seen_fact': '{name} {seconds}s{near}',
        'near': ' by {names}', 'seen': '{name} at {minutes} min{near}', 'never_seen': 'No {name}.{more}',
        'see_captures': ' See photos.', 'spotted_photo': 'Found {name}', 'alert_plain': '{name} gone{hint}',
        'last_seen_near': ' (near {names})', 'other_things': ('stapler',)})
    ai = FakeChatModel('Found them!')
    rig = Rig(chat=ai, words=words)
    rig.see('key')
    rig.see('key', after=1)
    rig.see(after=1)
    rig.see('key', after=1)
    rig.clock.advance(30)
    rig.lost.answer('have you seen my keys?')
    assert ai.calls[0]['messages'][0]['content'].startswith('You are Eye, the webcam')
    assert ai.calls[0]['messages'][1]['content'] == 'have you seen my keys? | Keys 30s by Computer'
    assert rig.photos() == ['Found Keys']
    rig = Rig(words=words)
    assert rig.lost.answer('have you seen my keys?')['text'] == 'No Keys. See photos.'
    rig.see('key')
    rig.clock.advance(10 * 60 + 5)
    assert rig.lost.answer('have you seen my keys?')['text'] == 'Keys at 10 min by Computer'
    rig.see(after=5 * 60)
    rig.tick()
    assert rig.alerts() == [('keys', 'Keys gone (near Computer)')]
    assert rig.lost.is_lost_query('have you seen my stapler?') and not rig.lost.is_lost_query('seen my laptop?')
