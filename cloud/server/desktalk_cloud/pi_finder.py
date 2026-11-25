"""Finds Desk Talk Pis on the home network by knocking on each address's "who are you" route, and offers one a link.

The home network can answer with anything: a router's admin page, a printer, a redirect, a huge page, or nothing at
all. Only a short JSON reply that says it is a Desk Talk Pi counts; everything else is left out, and nothing is ever
followed to another address. Each address gets its own time limit (`find_pis.knock_timeout_sec`) and an offer gets
`find_pis.link_timeout_sec`, each one `asyncio.timeout` around the whole call (httpx's own timeout is for one read).

The offer carries a one-time code, and a Pi's reply is not ours to keep: neither goes in a log line or an error.
Only the address, the status and the kind of error are logged."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
from collections.abc import Sequence
from typing import Any, TypeGuard

import httpx

from desktalk_cloud.config import FindPisSettings
from desktalk_cloud.domain import FoundPi, is_clean_text
from desktalk_cloud.ports import PiOfferFailed

log = logging.getLogger(__name__)

HELLO_PATH = '/api/cloud/hello'
LINK_PATH = '/api/cloud/link'
OK = 200
PI_KIND = 'pi'
# The Pi's `kind` when it says no to a link offer (desktalk/web/routers/cloud.py), as a PiOfferFailed kind: the Pi's
# bad_request and refused both carry its own words.
KIND_THE_PI_SAID = {'bad_request': 'refused', 'refused': 'refused', 'not_caller': 'not_caller',
                    'already_linked': 'already_linked', 'busy': 'busy', 'not_saved': 'not_saved',
                    'unavailable': 'unavailable'}
# With no kind we know, the status says it: the one kind the Pi gives that status (409 is all three of
# already_linked, busy and not_saved, so it reads as the first). Any other status is no answer.
KIND_OF_STATUS = {400: 'refused', 403: 'not_caller', 409: 'already_linked', 502: 'unavailable'}
CUT_MARK = '…'


class _TooBig(Exception):
    pass


def _is_plain_line(text: Any) -> TypeGuard[str]:
    """Text a person can read on one line: no control characters, and something a store can keep."""
    return isinstance(text, str) and bool(text.strip()) and is_clean_text(text) and text.isprintable()


def _by_number(pi: FoundPi) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    return ipaddress.ip_address(pi.address)


class HttpPiFinder:
    """The PiFinder (ports.py) over the home network. `client` is for tests (an httpx client with a fake transport)."""

    def __init__(self, settings: FindPisSettings, name_max_chars: int, client: httpx.AsyncClient | None = None):
        self._settings = settings
        self._name_max_chars = name_max_chars
        # trust_env=False: a proxy set for the internet must not carry knocks on the home network.
        self._client = client or httpx.AsyncClient(trust_env=False, follow_redirects=False)

    async def aclose(self) -> None:
        await self._client.aclose()

    def _address_of(self, address: str, path: str) -> str:
        return f'http://{address}:{self._settings.pi_port}{path}'

    async def _read(self, reply: httpx.Response) -> bytes:
        """The whole reply, a piece at a time; _TooBig as soon as it passes `reply_max_kb`."""
        limit = self._settings.reply_max_kb * 1024
        pieces: list[bytes] = []
        size = 0
        async for piece in reply.aiter_bytes():
            size += len(piece)
            if size > limit:
                raise _TooBig
            pieces.append(piece)
        return b''.join(pieces)

    async def _call(self, method: str, address: str, path: str, deadline: float,
                    body: dict[str, str] | None = None) -> tuple[int, bytes | None]:
        """(status, body). The body is None when it was too big. Raises httpx's errors and TimeoutError."""
        async with asyncio.timeout(deadline):
            async with self._client.stream(method, self._address_of(address, path), json=body, timeout=None,
                                           follow_redirects=False) as reply:
                try:
                    return reply.status_code, await self._read(reply)
                except _TooBig:
                    return reply.status_code, None

    # ── who is there ───────────────────────────────────────────────────
    async def find(self, addresses: Sequence[str]) -> list[FoundPi]:
        slots = asyncio.Semaphore(self._settings.at_once)

        async def knock(address: str) -> FoundPi | None:
            async with slots:
                return await self._hello(address)
        answers = await asyncio.gather(*(knock(address) for address in addresses))
        found = sorted((pi for pi in answers if pi is not None), key=_by_number)
        log.info('Looked for Pis at %d addresses and found %d', len(addresses), len(found))
        return found

    async def _hello(self, address: str) -> FoundPi | None:
        try:
            status, content = await self._call('GET', address, HELLO_PATH, self._settings.knock_timeout_sec)
        except Exception:                       # whatever one address does, the search goes on
            return None                         # most addresses have nobody there: that is not worth a log line
        if status != OK or content is None:
            return None
        try:
            reply = json.loads(content)
        except (ValueError, RecursionError):
            return None
        if not isinstance(reply, dict) or reply.get('desk_talk') != PI_KIND:
            return None
        name, linked = reply.get('name'), reply.get('linked')
        if not _is_plain_line(name) or not isinstance(linked, bool):
            return None
        return FoundPi(address, name.strip()[:self._name_max_chars].strip(), linked)

    # ── linking ────────────────────────────────────────────────────────
    async def offer(self, address: str, server_url: str, code: str) -> None:
        try:
            status, content = await self._call('POST', address, LINK_PATH, self._settings.link_timeout_sec,
                                               {'server': server_url, 'code': code})
        except TimeoutError:
            log.warning('The Pi at %s took too long to link', address)
            raise PiOfferFailed('no_answer') from None
        except httpx.HTTPError as error:        # its text can hold the address with the code: only the kind
            log.warning('The Pi at %s did not answer the link offer (%s)', address, type(error).__name__)
            raise PiOfferFailed('no_answer') from None
        if status == OK:
            log.info('The Pi at %s took the link offer', address)
            return
        reply = _json_object(content)
        said = reply.get('kind')
        kind = KIND_THE_PI_SAID[said] if isinstance(said, str) and said in KIND_THE_PI_SAID else \
            KIND_OF_STATUS.get(status, 'no_answer')
        log.info('The Pi at %s answered the link offer with status %d (%s)', address, status, kind)
        raise PiOfferFailed(kind, self._reason(reply) if kind == 'refused' else None)

    def _reason(self, reply: dict[str, Any]) -> str | None:
        """The Pi's `{"error": "..."}` words, when they are one line of clean text, cut between words to
        `reason_max_chars` (with a … to show it)."""
        reason = reply.get('error')
        if not _is_plain_line(reason):
            return None
        reason, most = reason.strip(), self._settings.reason_max_chars
        if len(reason) <= most:
            return reason
        cut = reason[:most - len(CUT_MARK) + 1]         # one letter more, to see whether the cut falls on a space
        cut = cut.rsplit(' ', 1)[0] if ' ' in cut.strip() else cut[:-1]
        return cut.rstrip() + CUT_MARK


def _json_object(content: bytes | None) -> dict[str, Any]:
    """The reply as a JSON object; an empty one when it is too big, not JSON or not an object."""
    try:
        reply = json.loads(content) if content is not None else None
    except (ValueError, RecursionError):
        return {}
    return reply if isinstance(reply, dict) else {}
