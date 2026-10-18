"""config/agents.yaml → agent profiles."""

from desktalk.agents.profiles import ProfileBook


def book(settings, tmp_path):
    return ProfileBook(settings.agents_file, learned_path=tmp_path / 'learned.json')


def test_known_labels(settings, tmp_path):
    b = book(settings, tmp_path)
    assert b.for_label('laptop').name == 'Computer'
    assert b.for_label('keyboard').id == 'computer'
    assert b.for_label('cell phone').name == 'Phone'
    assert b.for_label('earphone').name == 'AirPods'
    assert b.for_label('cup').min_confidence == 0.55
    assert 'hydration' in b.for_label('mug').habits


def test_ignored_labels(settings, tmp_path):
    b = book(settings, tmp_path)
    assert b.for_label('person') is None
    assert b.for_label('chair') is None
    assert b.is_ignored('desk')


def test_unknown_label_gets_default_profile(settings, tmp_path):
    b = book(settings, tmp_path)
    p = b.for_label('stapler')
    assert p.auto and p.name == 'Stapler' and p.object == 'stapler'
    assert 'stapler' in p.personality
    assert b.for_label('stapler') is p                     # made once
    assert p.hello_text() == "Oh hi, I'm the stapler. Mind if I hang out here?"


def test_habit_lookups(settings, tmp_path):
    b = book(settings, tmp_path)
    assert {'cup', 'bottle', 'mug'} <= b.labels_for_habit('hydration')
    assert 'computer' in b.ids_for_habit('breaks')
    assert b.relationship_risk('computer', 'cup').startswith('Cup or bottle')


def test_mention_words(settings, tmp_path):
    w = book(settings, tmp_path).mention_words()
    assert w['monty'] == 'computer' and w['laptop'] == 'computer' and w['glug'] == 'cup'
    assert w['pen'] == 'pen'


def test_learned_personality_is_remembered(settings, tmp_path):
    b = book(settings, tmp_path)
    b.for_label('stapler')
    b.set_personality('stapler', 'A tiny bodybuilder who loves holding papers together.')
    again = book(settings, tmp_path)
    p = again.for_label('stapler')
    assert p.learned and p.personality.startswith('A tiny bodybuilder')


def test_too_close_text(settings, tmp_path):
    b = book(settings, tmp_path)
    assert b.get('computer').too_close_text('Cup') == 'Cup is getting really close to me. Could you move it back a little?'
