"""
The Pi as the cloud website finds it: it says who it is, and links itself to a cloud server with a one-time code
the website offers, so linking needs no command on the Pi.

    linker = CloudLinker(store, claim, config.cloud, config.prompts.cloud, desktop, clock,
                         public_address=config.web.public_address, saved=True, host_name=socket.gethostname)
    linker.hello()                                       # CloudHello(name='desktalk', linked=False)
    linker.link('http://192.168.86.30:8080', code, caller='192.168.86.30')     # raises CloudLinkRefused

Anyone on the home network can ask, so it is careful:
  - it links only to the computer asking: the server's address must be a home network number (10.x, 172.16-31.x,
    192.168.x) and the same one the offer came from, so an offer can't point the Pi at an internet server or at a
    third machine;
  - it says no while it is linked already, so nobody can move a linked Pi to another server (the install command
    still can); a link the server stopped knowing doesn't count;
  - one offer at a time, so two clicks can't make two devices on the cloud;
  - with saving off the link wouldn't last a restart, so it says no;
  - it says out loud that it linked (and logs it), so a link you didn't make gets noticed.
The code is never logged or shown; nothing is saved unless the claim worked. The link is saved just as
scripts/link_cloud.py saves it (desktalk/domain/cloud_link.py), and the sender reads it on its next round.
"""

from __future__ import annotations

import ipaddress
import logging
import threading
from collections.abc import Callable
from urllib.parse import urlsplit

from desktalk.config import CloudSettings
from desktalk.config.prompts import CloudWords
from desktalk.domain import CloudHello, CloudLinkRefused, CloudNotLinked, CloudUnavailable
from desktalk.domain.cloud_link import is_active_link, link_record
from desktalk.ports import Clock, DesktopNotifications, KeyValueStore
from desktalk_shared.cloud_api import ClaimReply

log = logging.getLogger(__name__)

# claim(server, code, name, *, timeout_sec, error_text_chars) → ClaimReply (desktalk/adapters/cloud/http_link.py):
# raises CloudNotLinked when the server says no to the code, CloudUnavailable when it can't be reached.
ClaimCode = Callable[..., ClaimReply]

# The home network numbers (the private IPv4 ranges every home router hands out).
HOME_NETWORKS = tuple(ipaddress.IPv4Network(n) for n in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))
LOCAL_SUFFIX = '.local'


def _home_address(host: str) -> ipaddress.IPv4Address | None:
    """The IPv4 address `host` is written as, if it is a home network number (an IPv6 address carrying one, as a
    server listening on both kinds sees an IPv4 caller, counts as that number). None for anything else."""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address):
        address = address.ipv4_mapped
    if address is None or not any(address in network for network in HOME_NETWORKS):
        return None
    return address


class CloudLinker:
    """The CloudLinking part (desktalk/ports.py).

    store: where the link is saved (settings → cloud.link_key). claim: trades a code for a link (ClaimCode).
    settings: cloud's time limit, reason length and name length. words: prompts.yaml → cloud.
    desktop: the Pi's speaker (or the Mac's pop-ups), for the line it says once linked. clock: the time the link
    starts from. public_address: settings → web.public_address, whose name (without .local) is the Pi's name; ''
    uses host_name() instead. saved: False when saving is off (the data folder is in memory)."""

    def __init__(self, store: KeyValueStore, claim: ClaimCode, settings: CloudSettings, words: CloudWords,
                 desktop: DesktopNotifications, clock: Clock, *, public_address: str, saved: bool,
                 host_name: Callable[[], str]):
        self._store = store
        self._claim = claim
        self._s = settings
        self._words = words
        self._desktop = desktop
        self._clock = clock
        self._public_address = public_address
        self._saved = saved
        self._host_name = host_name
        self._one_at_a_time = threading.Lock()

    def _name(self) -> str:
        name = self._public_address.partition(':')[0].strip() or self._host_name().strip()
        if name.lower().endswith(LOCAL_SUFFIX):
            name = name[:-len(LOCAL_SUFFIX)]
        return name[:self._s.name_max_chars]

    def hello(self) -> CloudHello:
        return CloudHello(name=self._name(), linked=is_active_link(self._store.load(self._s.link_key)))

    def _server(self, server: str, code: str, caller: str) -> str:
        """The server's address without a trailing slash, once the offer passes every check before the lock."""
        words = self._words
        server = server.strip().rstrip('/')
        try:
            parts = urlsplit(server)
            parts.port                                  # noqa: B018  (a bad port raises ValueError here)
        except ValueError:
            raise CloudLinkRefused('bad_request', words.offer_bad) from None
        if parts.scheme not in ('http', 'https') or not parts.hostname or not code.strip():
            raise CloudLinkRefused('bad_request', words.offer_bad)
        wanted = _home_address(parts.hostname)
        if wanted is None or wanted != _home_address(caller):
            raise CloudLinkRefused('not_caller', words.offer_not_caller)
        if not self._saved:
            raise CloudLinkRefused('not_saved', words.offer_not_saved)
        return server

    def link(self, server: str, code: str, caller: str) -> None:
        server = self._server(server, code, caller)
        if not self._one_at_a_time.acquire(blocking=False):
            raise CloudLinkRefused('busy', self._words.offer_busy)
        try:
            self._link(server, code.strip())
        finally:
            self._one_at_a_time.release()

    def _link(self, server: str, code: str) -> None:
        words, key = self._words, self._s.link_key
        old = self._store.load(key)      # read first: the code works once, so nothing may fail after it is used
        if is_active_link(old):
            raise CloudLinkRefused('already_linked', words.offer_already_linked)
        try:
            reply = self._claim(server, code, self._name(), timeout_sec=self._s.timeout_sec,
                                error_text_chars=self._s.error_text_chars)
        except CloudNotLinked as exc:
            reason = ' '.join(str(exc).split())[:self._s.error_text_chars]
            raise CloudLinkRefused('refused', words.link_refused_because.format(reason=reason) if reason
                                   else words.link_code_refused) from None
        except CloudUnavailable as exc:
            raise CloudLinkRefused('unavailable', words.link_no_answer.format(server=server, error=str(exc))) from None
        self._store.save(key, link_record(server, reply.device_id, reply.token, old, self._clock.now()))
        log.info(words.offer_linked_log.format(server=server))
        self._desktop.show(words.offer_linked_title, words.offer_linked_said)
