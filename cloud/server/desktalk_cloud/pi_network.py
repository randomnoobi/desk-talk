"""Which addresses to knock on when looking for Pis: the home network around this server's own address (PUBLIC_URL).

Only a private IPv4 address (10.x, 172.16-31.x, 192.168.x) gives a network to search. A name like `localhost`, a
public address, or anything else gives none, so the server never knocks on the internet or on itself."""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

__all__ = ['addresses_to_search', 'in_search_network']

PRIVATE_NETWORKS = tuple(ipaddress.IPv4Network(network) for network in ('10.0.0.0/8', '172.16.0.0/12',
                                                                          '192.168.0.0/16'))


def _own_address(public_url: str) -> ipaddress.IPv4Address | None:
    """PUBLIC_URL's address, when it is a private IPv4 address written as a number."""
    try:
        parts = urlsplit(public_url)
        host, _port = parts.hostname, parts.port            # reading the port checks it is a number
        address = ipaddress.IPv4Address(host or '')
    except ValueError:
        return None
    return address if any(address in network for network in PRIVATE_NETWORKS) else None


def _network(public_url: str, prefix: int) -> tuple[ipaddress.IPv4Network, ipaddress.IPv4Address] | None:
    own = _own_address(public_url)
    if own is None:
        return None
    network = ipaddress.IPv4Network(f'{own}/{prefix}', strict=False)
    if not any(network.subnet_of(private) for private in PRIVATE_NETWORKS):
        return None
    return network, own


def addresses_to_search(public_url: str, prefix: int) -> list[str] | None:
    """Every usable address of the network around PUBLIC_URL's address, except that address itself, lowest first.
    None when PUBLIC_URL gives no private network to search."""
    found = _network(public_url, prefix)
    if found is None:
        return None
    network, own = found
    return [str(address) for address in network.hosts() if address != own]


def in_search_network(address: str, public_url: str, prefix: int) -> bool:
    """Whether `address` is exactly one of the addresses addresses_to_search gives (written the same way)."""
    found = _network(public_url, prefix)
    if found is None:
        return False
    network, own = found
    try:
        parsed = ipaddress.IPv4Address(address)
    except ValueError:
        return False
    usable = parsed in network and parsed not in (network.network_address, network.broadcast_address, own)
    return usable and str(parsed) == address
