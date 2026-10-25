"""What every KeyValueStore must do (desktalk.ports.KeyValueStore)."""

from __future__ import annotations


class KeyValueContract:
    """Subclass and give a `kv` fixture (empty)."""

    def test_missing_key_gives_the_default(self, kv):
        assert kv.load('nothing') is None
        assert kv.load('nothing', {'a': 1}) == {'a': 1}

    def test_saved_value_comes_back(self, kv):
        kv.save('preferences', {'wake': '07:00', 'quiet': [22, 7]})
        assert kv.load('preferences') == {'wake': '07:00', 'quiet': [22, 7]}

    def test_loaded_value_is_a_copy(self, kv):
        kv.save('prefs', {'a': [1]})
        kv.load('prefs')['a'].append(2)
        assert kv.load('prefs') == {'a': [1]}

    def test_keys_by_prefix(self, kv):
        kv.save('habits/2026-10-20', {})
        kv.save('habits/2026-10-21', {})
        kv.save('preferences', {})
        assert kv.keys('habits/') == ['habits/2026-10-20', 'habits/2026-10-21']
