"""
Where other devices find the app:

    links = Links(lan_ip(), web_port, public_address='')
    links.web_url         # http://192.168.1.20:5000  (http://desktalk.local:5000 when public_address says so)
"""

from __future__ import annotations

import socket
from dataclasses import dataclass

# lan_ip() "connects" a UDP socket to this address to learn which network the computer would use. Nothing
# is sent: a UDP connect only picks the way out.
ANY_OUTSIDE_ADDRESS = ('8.8.8.8', 80)
THIS_COMPUTER = '127.0.0.1'


def lan_ip() -> str:
    """This computer's address on the Wi-Fi (what other devices connect to); 127.0.0.1 when offline."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(ANY_OUTSIDE_ADDRESS)
        return s.getsockname()[0]
    except OSError:
        return THIS_COMPUTER
    finally:
        s.close()


@dataclass(frozen=True)
class Links:
    """address: the app's computer on the Wi-Fi. public_address: how other devices reach the app, like
    desktalk.local:5000 (web.public_address); '' when it isn't set. Inside Docker the address above is Docker's
    own, so this is what people are told to open."""

    address: str
    web_port: int
    public_address: str = ''

    @property
    def web_url(self) -> str:
        """Where other devices open the app."""
        return f'http://{self.public_address or f"{self.address}:{self.web_port}"}'
