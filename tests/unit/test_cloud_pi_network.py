"""Which home network addresses the cloud server knocks on when it looks for Pis."""

from __future__ import annotations

import pytest

from desktalk_cloud.pi_network import addresses_to_search, in_search_network

HOME = 'http://192.168.86.30:8080'


def test_a_home_address_gives_every_other_address_of_its_network():
    found = addresses_to_search(HOME, 24)
    assert found is not None
    assert len(found) == 253
    assert found[0] == '192.168.86.1' and found[-1] == '192.168.86.254'
    assert '192.168.86.30' not in found                  # the server itself
    assert '192.168.86.0' not in found and '192.168.86.255' not in found


def test_a_ten_address_gives_its_own_network():
    found = addresses_to_search('http://10.0.0.5', 24)
    assert found is not None
    assert all(address.startswith('10.0.0.') for address in found)
    assert '10.0.0.5' not in found and len(found) == 253


def test_a_172_address_inside_the_private_range_counts():
    assert addresses_to_search('http://172.20.1.9:8080/', 24) is not None


@pytest.mark.parametrize('public_url', ['http://localhost:8080', 'http://desk.example', 'http://8.8.8.8',
                                        'http://127.0.0.1', '', 'http://172.32.0.1', 'http://[fd00::1]:8080',
                                        'http://169.254.1.2', 'not an address', 'http://192.168.86.30:port'])
def test_no_private_network_gives_none(public_url):
    assert addresses_to_search(public_url, 24) is None


def test_in_search_network():
    assert in_search_network('192.168.86.225', HOME, 24)
    for address in ('192.168.87.5', '8.8.8.8', 'desktalk.local', '192.168.86.225:80', '', '192.168.86.30',
                    '192.168.86.0', '192.168.86.255', ' 192.168.86.225'):
        assert not in_search_network(address, HOME, 24), address


def test_nothing_is_in_the_network_when_there_is_none():
    assert not in_search_network('127.0.0.5', 'http://localhost:8080', 24)
