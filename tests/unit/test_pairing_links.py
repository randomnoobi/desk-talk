"""The links the app shows: with web.public_address set they use it, and show no Pi command."""

from __future__ import annotations

from desktalk.web.pairing import Links

KEY = 'AbC123xY'


def test_links_use_the_address_and_port_on_a_computer():
    links = Links('192.168.1.20', 5000, KEY)
    assert links.web_url == 'http://192.168.1.20:5000'
    assert links.pi_command == f'python3 -m raspberry_pi.camera --server http://192.168.1.20:5000 --key {KEY}'
    assert links.pi_command_with_key_file('pair-key.txt') == (
        'python3 -m raspberry_pi.camera --server http://192.168.1.20:5000 --key-file pair-key.txt')


def test_a_public_address_replaces_the_address_and_there_is_no_pi_command():
    links = Links('172.18.0.3', 5000, KEY, public_address='desktalk.local:5000')
    assert links.web_url == 'http://desktalk.local:5000'
    assert links.pi_command == '' and links.pi_command_with_key_file('pair-key.txt') == ''


def test_the_pairing_key_is_not_in_how_links_print():
    """A Links that lands in a log line, a traceback or a test failure must not show the key."""
    links = Links('192.168.1.20', 5000, KEY, phone_port=5443, public_address='desktalk.local:5000')
    assert KEY not in repr(links) and KEY not in str(links)
