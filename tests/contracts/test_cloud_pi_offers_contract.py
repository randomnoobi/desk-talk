"""What every PiOffers must do (desktalk_cloud.ports.PiOffers). The fake in tests/fakes/cloud.py and the real
HttpPiOffers (against a pretend home network) run the same tests; the real one also gets the junk a Pi or a home
network can answer an offer with: a redirect, a huge reply, words that aren't clean, a program that never answers."""

from __future__ import annotations

import asyncio
import json
import logging

import httpx
import pytest

from desktalk_cloud.pi_offers import HttpPiOffers
from desktalk_cloud.ports import PiOfferFailed, PiOffers
from tests.fakes.cloud import FakePiOffers, cloud_config

SERVER = 'http://192.168.86.30:8080'
CODE = 'SECRET-CODE-7Q2X'
HOME_PI = '192.168.86.10'                # takes an offer
OTHER_PI = '192.168.86.40'               # takes an offer too
LINKED_PI = '192.168.86.9'               # is linked already
REFUSING_PI = '192.168.86.41'            # refuses any offer with REFUSAL
NOBODY = '192.168.86.99'
REFUSAL = 'That code is wrong'


class PiOffersContract:
    """Subclass this and give an `offers` fixture: a PiOffers on a home network where HOME_PI and OTHER_PI take an
    offer, LINKED_PI is linked already, REFUSING_PI refuses any offer with REFUSAL and NOBODY doesn't answer."""

    def test_it_is_a_pi_offers(self, offers):
        assert isinstance(offers, PiOffers)

    def test_an_offer_the_pi_takes(self, offers):
        asyncio.run(offers.offer(HOME_PI, SERVER, CODE))
        asyncio.run(offers.offer(OTHER_PI, SERVER, CODE))

    def test_a_linked_pi_says_already_linked(self, offers):
        with pytest.raises(PiOfferFailed) as failed:
            asyncio.run(offers.offer(LINKED_PI, SERVER, CODE))
        assert failed.value.kind == 'already_linked'

    def test_a_pi_that_refuses_gives_its_reason(self, offers):
        with pytest.raises(PiOfferFailed) as failed:
            asyncio.run(offers.offer(REFUSING_PI, SERVER, CODE))
        assert (failed.value.kind, failed.value.reason) == ('refused', REFUSAL)

    def test_no_answer(self, offers):
        with pytest.raises(PiOfferFailed) as failed:
            asyncio.run(offers.offer(NOBODY, SERVER, CODE))
        assert failed.value.kind == 'no_answer'

    def test_it_only_offers_and_never_searches(self, offers):
        # The browser does the looking: knocking on a whole home network from the server's container can stall
        # every other connection it makes (Colima's and Docker Desktop's relays keep each empty knock open).
        assert not hasattr(offers, 'find')

    def test_a_failure_never_holds_the_code(self, offers):
        for address in (LINKED_PI, REFUSING_PI, NOBODY):
            with pytest.raises(PiOfferFailed) as failed:
                asyncio.run(offers.offer(address, SERVER, CODE))
            assert CODE not in str(failed.value) and CODE not in repr(failed.value.args)


class TestFakePiOffers(PiOffersContract):
    @pytest.fixture
    def offers(self):
        fake = FakePiOffers(pis=[HOME_PI, OTHER_PI, REFUSING_PI], linked=[LINKED_PI])
        fake.refusals[REFUSING_PI] = PiOfferFailed('refused', REFUSAL)
        return fake

    def test_it_remembers_what_it_was_asked(self, offers):
        asyncio.run(offers.offer(HOME_PI, SERVER, CODE))
        assert offers.offers == [(HOME_PI, SERVER, CODE)]

    def test_offer_fails_fails_every_offer(self):
        fake = FakePiOffers(pis=[HOME_PI], offer_fails=PiOfferFailed('no_answer'))
        with pytest.raises(PiOfferFailed):
            asyncio.run(fake.offer(HOME_PI, SERVER, CODE))

    def test_on_offer_runs_with_the_offer(self):
        seen = []

        async def claim(address, server_url, code):
            seen.append((address, server_url, code))
        fake = FakePiOffers(pis=[HOME_PI], on_offer=claim)
        asyncio.run(fake.offer(HOME_PI, SERVER, CODE))
        assert seen == [(HOME_PI, SERVER, CODE)]


# ── the real one, on a pretend home network ─────────────────────────────
class PretendNetwork:
    """Answers like a home network. `links` maps an address to what it answers a link offer with; any other address
    can't be reached, and nothing answers anything but an offer. A value that is a function is called with the
    request."""

    def __init__(self, settings):
        self.port = settings.pi_port
        self.links: dict = {
            HOME_PI: httpx.Response(200, json={'linked': True}),
            LINKED_PI: httpx.Response(409, json={'error': 'This Pi is already linked.', 'kind': 'already_linked'}),
            OTHER_PI: httpx.Response(200, json={'linked': True}),
            REFUSING_PI: httpx.Response(400, json={'error': REFUSAL, 'kind': 'refused'}),
        }
        self.seen: list[httpx.Request] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        address = request.url.host
        if request.url.port != self.port:
            raise httpx.ConnectError('wrong port')
        answers = self.links if (request.method, request.url.path) == ('POST', '/api/cloud/link') else {}
        if address not in answers:
            raise httpx.ConnectError('nobody there')
        answer = answers[address]
        if callable(answer):
            answer = answer(request)
            if asyncio.iscoroutine(answer):
                answer = await answer
        assert isinstance(answer, httpx.Response)
        return answer


def real_offers(**changes):
    config = cloud_config(find_pis=changes)
    network = PretendNetwork(config.find_pis)
    client = httpx.AsyncClient(transport=httpx.MockTransport(network))
    return HttpPiOffers(config.find_pis, client=client), network


async def hang(_request):
    await asyncio.sleep(30)
    return httpx.Response(200, json={})


class TestHttpPiOffers(PiOffersContract):
    @pytest.fixture
    def pretend(self):
        return real_offers(link_timeout_sec=0.2)

    @pytest.fixture
    def offers(self, pretend):
        return pretend[0]

    @pytest.fixture
    def network(self, pretend):
        return pretend[1]

    def test_offer_posts_the_server_and_code_to_the_pi(self, offers, network):
        asyncio.run(offers.offer(HOME_PI, SERVER, CODE))
        (request,) = network.seen
        assert (request.method, str(request.url)) == ('POST',
                                                     f'http://{HOME_PI}:{network.port}/api/cloud/link')
        assert json.loads(request.content) == {'server': SERVER, 'code': CODE}
        assert request.headers['content-type'] == 'application/json'

    def test_offer_never_follows_a_redirect(self, offers, network):
        network.links[HOME_PI] = httpx.Response(307, headers={'location': 'http://192.168.86.77:5000/x'})
        with pytest.raises(PiOfferFailed) as failed:
            asyncio.run(offers.offer(HOME_PI, SERVER, CODE))
        assert failed.value.kind == 'no_answer'
        assert len(network.seen) == 1

    @pytest.mark.parametrize(('status', 'said', 'kind'), [
        (409, 'already_linked', 'already_linked'), (409, 'busy', 'busy'), (409, 'not_saved', 'not_saved'),
        (403, 'not_caller', 'not_caller'), (502, 'unavailable', 'unavailable'),
        (400, 'refused', 'refused'), (400, 'bad_request', 'refused'),
    ])
    def test_the_pis_own_kind_says_why(self, offers, network, status, said, kind):
        network.links[HOME_PI] = httpx.Response(status, json={'error': 'Some words.', 'kind': said})
        with pytest.raises(PiOfferFailed) as failed:
            asyncio.run(offers.offer(HOME_PI, SERVER, CODE))
        assert failed.value.kind == kind
        assert failed.value.reason == ('Some words.' if kind == 'refused' else None)   # the others have our words

    @pytest.mark.parametrize(('status', 'kind'), [(400, 'refused'), (403, 'not_caller'), (409, 'already_linked'),
                                                  (502, 'unavailable')])
    @pytest.mark.parametrize('said', [None, 'something_new', 5, ['busy']])
    def test_without_a_kind_it_knows_the_status_says_why(self, offers, network, status, kind, said):
        body = {'error': 'Some words.'} if said is None else {'error': 'Some words.', 'kind': said}
        network.links[HOME_PI] = httpx.Response(status, json=body)
        with pytest.raises(PiOfferFailed) as failed:
            asyncio.run(offers.offer(HOME_PI, SERVER, CODE))
        assert failed.value.kind == kind

    def test_a_kind_that_is_not_json_falls_back_to_the_status(self, offers, network):
        network.links[HOME_PI] = httpx.Response(409, html='<h1>busy</h1>')
        with pytest.raises(PiOfferFailed) as failed:
            asyncio.run(offers.offer(HOME_PI, SERVER, CODE))
        assert (failed.value.kind, failed.value.reason) == ('already_linked', None)

    def test_a_long_reason_is_cut_between_words(self, offers, network):
        most = cloud_config().find_pis.reason_max_chars
        words = ' '.join(['word'] * most)
        network.links[HOME_PI] = httpx.Response(400, json={'error': words, 'kind': 'refused'})
        with pytest.raises(PiOfferFailed) as failed:
            asyncio.run(offers.offer(HOME_PI, SERVER, CODE))
        reason = failed.value.reason
        assert reason is not None and len(reason) <= most
        assert reason.endswith('…') and set(reason[:-1].split()) == {'word'} and len(reason) > most - 6

    def test_a_long_reason_with_no_spaces_is_cut_too(self, offers, network):
        most = cloud_config().find_pis.reason_max_chars
        network.links[HOME_PI] = httpx.Response(400, json={'error': 'x' * (most + 1), 'kind': 'refused'})
        with pytest.raises(PiOfferFailed) as failed:
            asyncio.run(offers.offer(HOME_PI, SERVER, CODE))
        assert failed.value.reason == 'x' * (most - 1) + '…'

    def test_a_reason_that_just_fits_is_kept_whole(self, offers, network):
        most = cloud_config().find_pis.reason_max_chars
        network.links[HOME_PI] = httpx.Response(400, json={'error': f'  {"x" * most}  ', 'kind': 'refused'})
        with pytest.raises(PiOfferFailed) as failed:
            asyncio.run(offers.offer(HOME_PI, SERVER, CODE))
        assert failed.value.reason == 'x' * most

    @pytest.mark.parametrize('body', [{'error': 'bad\x00words'}, {'error': 'two\nlines'},
                                      {'error': 5}, {'error': '  '}, {'nothing': 'here'}, ['error']])
    def test_a_reason_that_is_not_clean_text_is_dropped(self, offers, network, body):
        network.links[HOME_PI] = httpx.Response(400, json=body)
        with pytest.raises(PiOfferFailed) as failed:
            asyncio.run(offers.offer(HOME_PI, SERVER, CODE))
        assert (failed.value.kind, failed.value.reason) == ('refused', None)

    def test_a_refusal_that_is_not_json_has_no_reason(self, offers, network):
        network.links[HOME_PI] = httpx.Response(400, html='<h1>no</h1>')
        with pytest.raises(PiOfferFailed) as failed:
            asyncio.run(offers.offer(HOME_PI, SERVER, CODE))
        assert (failed.value.kind, failed.value.reason) == ('refused', None)

    def test_a_huge_refusal_is_not_read_as_a_reason(self, offers, network):
        network.links[HOME_PI] = httpx.Response(400, content=b'{"error": "' + b'x' * 10_000 + b'"}')
        with pytest.raises(PiOfferFailed) as failed:
            asyncio.run(offers.offer(HOME_PI, SERVER, CODE))
        assert (failed.value.kind, failed.value.reason) == ('refused', None)

    @pytest.mark.parametrize('answer', [httpx.Response(500), httpx.Response(503, json={'error': 'cloud down'}),
                                        httpx.Response(404), httpx.Response(201, json={'linked': True})])
    def test_any_other_status_is_no_answer(self, offers, network, answer):
        network.links[HOME_PI] = answer
        with pytest.raises(PiOfferFailed) as failed:
            asyncio.run(offers.offer(HOME_PI, SERVER, CODE))
        assert failed.value.kind == 'no_answer'

    @pytest.mark.parametrize('error', [httpx.ConnectTimeout('slow'), httpx.ReadTimeout('slow'),
                                       httpx.ConnectError(f'refused while sending {CODE}')])
    def test_a_failed_send_is_no_answer(self, offers, network, error):
        def fails(_request):
            raise error
        network.links[HOME_PI] = fails
        with pytest.raises(PiOfferFailed) as failed:
            asyncio.run(offers.offer(HOME_PI, SERVER, CODE))
        assert failed.value.kind == 'no_answer'
        assert failed.value.__cause__ is None and failed.value.__suppress_context__

    def test_an_offer_that_never_ends_gives_up_at_the_link_timeout(self, offers, network):
        network.links[HOME_PI] = hang
        with pytest.raises(PiOfferFailed) as failed:
            asyncio.run(asyncio.wait_for(offers.offer(HOME_PI, SERVER, CODE), 5))
        assert failed.value.kind == 'no_answer'

    def test_logs_hold_no_code_and_no_reply(self, offers, network, caplog):
        caplog.set_level(logging.DEBUG)
        secret_words = 'private-words-from-the-pi'
        network.links[HOME_PI] = httpx.Response(400, json={'error': secret_words})

        def fails(_request):
            raise httpx.ConnectError(f'refused while sending {CODE} {secret_words}')
        network.links[OTHER_PI] = fails
        for address in (HOME_PI, OTHER_PI, LINKED_PI, NOBODY):
            with pytest.raises(PiOfferFailed):
                asyncio.run(offers.offer(address, SERVER, CODE))
        assert caplog.text
        assert CODE not in caplog.text and secret_words not in caplog.text

    def test_closing_closes_its_client(self, offers):
        asyncio.run(offers.aclose())
        assert offers._client.is_closed

    def test_its_own_client_ignores_proxy_settings_and_follows_no_redirects(self):
        offers = HttpPiOffers(cloud_config().find_pis)
        assert offers._client.trust_env is False
        assert offers._client.follow_redirects is False
        asyncio.run(offers.aclose())
