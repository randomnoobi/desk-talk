"""The pairing key: a short random key so other devices on the Wi-Fi can't send pictures."""

from __future__ import annotations

import stat

from desktalk.adapters.camera import make_pair_key

URL_SAFE = set('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_')


def test_a_new_key_is_saved_and_used_again_after_a_restart(tmp_path):
    path = tmp_path / 'certs' / 'pair-key.txt'
    key = make_pair_key(path, key_bytes=6)
    assert path.read_text() == key
    assert make_pair_key(path, key_bytes=6) == key


def test_the_key_is_url_safe_and_as_strong_as_asked(tmp_path):
    short = make_pair_key(None, key_bytes=6)
    long = make_pair_key(None, key_bytes=12)
    assert len(short) == 8 and len(long) == 16          # 4 characters for every 3 random bytes
    assert set(short + long) <= URL_SAFE


def test_only_you_can_read_the_saved_key(tmp_path):
    path = tmp_path / 'pair-key.txt'
    make_pair_key(path, key_bytes=6)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_a_key_you_wrote_yourself_is_kept(tmp_path):
    path = tmp_path / 'pair-key.txt'
    path.write_text('my-desk_42\n')
    assert make_pair_key(path, key_bytes=6) == 'my-desk_42'


def test_a_broken_or_too_short_saved_key_is_replaced(tmp_path):
    path = tmp_path / 'pair-key.txt'
    for bad in ('two words', 'abc', '', 'key/../x'):
        path.write_text(bad)
        key = make_pair_key(path, key_bytes=6)
        assert key != bad.strip() and len(key) == 8 and path.read_text() == key


def test_without_a_file_each_key_is_new():
    assert make_pair_key(None, key_bytes=6) != make_pair_key(None, key_bytes=6)


def test_a_folder_that_cant_be_written_still_gives_a_key(tmp_path, caplog):
    blocker = tmp_path / 'certs'
    blocker.write_text('a file where the folder should be')
    key = make_pair_key(blocker / 'pair-key.txt', key_bytes=6)
    assert len(key) == 8
    assert 'pairing key' in caplog.text
