"""What every PiFinder must do (desktalk_cloud.ports.PiFinder). The fake in tests/fakes/cloud.py and the real
HttpPiFinder (against a pretend home network) run the same tests; the real one also gets the junk a home network can
answer with: a router's page, a redirect, a huge reply, a program that never answers."""

from __future__ import annotations

import asyncio
import json
import logging

import httpx
import pytest

from desktalk_cloud.domain import FoundPi
from desktalk_cloud.pi_finder import HttpPiFinder
from desktalk_cloud.ports import PiFinder, PiOfferFailed
from tests.fakes.cloud import FakePiFinder, cloud_config

SERVER = 'http://192.168.86.30:8080'
CODE = 'SECRET-CODE-7Q2X'
HOME_PI = FoundPi('192.168.86.10', 'desktalk', False)
LINKED_PI = FoundPi('192.168.86.9', 'kitchen', True)
LONG_PI = FoundPi('192.168.86.40', 'x' * 500, False)
REFUSING_PI = FoundPi('192.168.86.41', 'fussy', False)
GOOD_PIS = (HOME_PI, LINKED_PI, LONG_PI, REFUSING_PI)
NOBODY = '192.168.86.99'
REFUSAL = 'That code is wrong'


def name_max_chars() -> int:
    return cloud_config().pairing.name_max_chars


class PiFinderContract:
    """Subclass this and give a `finder` fixture: a PiFinder on a home network where the Pis in GOOD_PIS answer
    (LINKED_PI already linked, REFUSING_PI refusing any offer with REFUSAL) and NOBODY doesn't answer."""

    def test_it_is_a_pi_finder(self, finder):
        assert isinstance(finder, PiFinder)

    def test_find_gives_the_pis_that_answer_sorted_by_address(self, finder):
        found = asyncio.run(finder.find([HOME_PI.address, NOBODY, LINKED_PI.address]))
        assert found == [LINKED_PI, HOME_PI]                    # .9 before .10: by number, not by text

    def test_find_asks_only_the_addresses_it_is_given(self, finder):
        assert asyncio.run(finder.find([HOME_PI.address])) == [HOME_PI]
        assert asyncio.run(finder.find([])) == []

    def test_a_long_name_is_cut(self, finder):
        (found,) = asyncio.run(finder.find([LONG_PI.address]))
        assert found.name == 'x' * name_max_chars()

    def test_an_offer_the_pi_takes(self, finder):
        asyncio.run(finder.offer(HOME_PI.address, SERVER, CODE))

    def test_a_linked_pi_says_already_linked(self, finder):
        with pytest.raises(PiOfferFailed) as failed:
            asyncio.run(finder.offer(LINKED_PI.address, SERVER, CODE))
        assert failed.value.kind == 'already_linked'

    def test_a_pi_that_refuses_gives_its_reason(self, finder):
        with pytest.raises(PiOfferFailed) as failed:
            asyncio.run(finder.offer(REFUSING_PI.address, SERVER, CODE))
        assert (failed.value.kind, failed.value.reason) == ('refused', REFUSAL)

    def test_no_answer(self, finder):
        with pytest.raises(PiOfferFailed) as failed:
            asyncio.run(finder.offer(NOBODY, SERVER, CODE))
        assert failed.value.kind == 'no_answer'

    def test_a_failure_never_holds_the_code(self, finder):
        for address in (LINKED_PI.address, REFUSING_PI.address, NOBODY):
            with pytest.raises(PiOfferFailed) as failed:
                asyncio.run(finder.offer(address, SERVER, CODE))
            assert CODE not in str(failed.value) and CODE not in repr(failed.value.args)


class TestFakePiFinder(PiFinderContract):
    @pytest.fixture
    def finder(self):
        fake = FakePiFinder(pis=GOOD_PIS, name_max_chars=name_max_chars())
        fake.refusals[REFUSING_PI.address] = PiOfferFailed('refused', REFUSAL)
        return fake

    def test_it_remembers_what_it_was_asked(self, finder):
        asyncio.run(finder.find([HOME_PI.address]))
        asyncio.run(finder.offer(HOME_PI.address, SERVER, CODE))
        assert finder.searched == [[HOME_PI.address]]
        assert finder.offers == [(HOME_PI.address, SERVER, CODE)]

    def test_offer_fails_fails_every_offer(self):
        fake = FakePiFinder(pis=GOOD_PIS, offer_fails=PiOfferFailed('no_answer'))
        with pytest.raises(PiOfferFailed):
            asyncio.run(fake.offer(HOME_PI.address, SERVER, CODE))

    def test_on_offer_runs_with_the_offer(self):
        seen = []

        async def claim(address, server_url, code):
            seen.append((address, server_url, code))
        fake = FakePiFinder(pis=GOOD_PIS, on_offer=claim)
        asyncio.run(fake.offer(HOME_PI.address, SERVER, CODE))
        assert seen == [(HOME_PI.address, SERVER, CODE)]


# ── the real one, on a pretend home network ─────────────────────────────
def hello(pi: FoundPi) -> dict:
    return {'desk_talk': 'pi', 'name': pi.name, 'linked': pi.linked}


class PretendNetwork:
    """Answers like a home network. `hellos` and `links` map an address to what it answers; any other address can't
    be reached. A value that is a function is called with the request."""

    def __init__(self, settings):
        self.port = settings.pi_port
        self.hellos: dict = {pi.address: httpx.Response(200, json=hello(pi)) for pi in GOOD_PIS}
        self.links: dict = {
            HOME_PI.address: httpx.Response(200, json={'linked': True}),
            LINKED_PI.address: httpx.Response(409, json={'error': 'This Pi is already linked.',
                                                          'kind': 'already_linked'}),
            LONG_PI.address: httpx.Response(200, json={'linked': True}),
            REFUSING_PI.address: httpx.Response(400, json={'error': REFUSAL, 'kind': 'refused'}),
        }
        self.seen: list[httpx.Request] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        address = request.url.host
        if request.url.port != self.port:
            raise httpx.ConnectError('wrong port')
        answers = self.hellos if (request.method, request.url.path) == ('GET', '/api/cloud/hello') else \
            self.links if (request.method, request.url.path) == ('POST', '/api/cloud/link') else {}
        if address not in answers:
            raise httpx.ConnectError('nobody there')
        answer = answers[address]
        if callable(answer):
            answer = answer(request)
            if asyncio.iscoroutine(answer):
                answer = await answer
        assert isinstance(answer, httpx.Response)
        return answer


def real_finder(**changes):
    config = cloud_config(find_pis=changes)
    network = PretendNetwork(config.find_pis)
    client = httpx.AsyncClient(transport=httpx.MockTransport(network))
    return HttpPiFinder(config.find_pis, config.pairing.name_max_chars, client=client), network


async def hang(_request):
    await asyncio.sleep(30)
    return httpx.Response(200, json={})


class TestHttpPiFinder(PiFinderContract):
    @pytest.fixture
    def pretend(self):
        return real_finder(knock_timeout_sec=0.2, link_timeout_sec=0.2)

    @pytest.fixture
    def finder(self, pretend):
        return pretend[0]

    @pytest.fixture
    def network(self, pretend):
        return pretend[1]

    def test_it_knocks_on_the_pis_port_and_hello_route(self, finder, network):
        asyncio.run(finder.find([HOME_PI.address]))
        (request,) = network.seen
        assert str(request.url) == f'http://{HOME_PI.address}:{network.port}/api/cloud/hello'

    @pytest.mark.parametrize('answer', [
        httpx.Response(200, html='<html><body>Router admin</body></html>'),
        httpx.Response(302, headers={'location': 'http://192.168.86.10:5000/api/cloud/hello'}),
        httpx.Response(200, json={'desk_talk': 'router', 'name': 'r', 'linked': False}),
        httpx.Response(200, json={'name': 'no-kind', 'linked': False}),
        httpx.Response(200, json={'desk_talk': 'pi', 'name': 'r', 'linked': 'yes'}),
        httpx.Response(200, json={'desk_talk': 'pi', 'name': 'r', 'linked': 1}),
        httpx.Response(200, json={'desk_talk': 'pi', 'name': 'bad\x07name', 'linked': False}),
        httpx.Response(200, json={'desk_talk': 'pi', 'name': 'bad\nname', 'linked': False}),
        httpx.Response(200, content=b'{"desk_talk": "pi", "name": "\\ud800", "linked": false}'),
        httpx.Response(200, json={'desk_talk': 'pi', 'name': '   ', 'linked': False}),
        httpx.Response(200, json={'desk_talk': 'pi', 'name': 5, 'linked': False}),
        httpx.Response(200, json=['desk_talk', 'pi']),
        httpx.Response(500, json={'desk_talk': 'pi', 'name': 'r', 'linked': False}),
        httpx.Response(200, content=b'{"desk_talk": "pi", "name": "' + b'x' * 10_000 + b'", "linked": false}'),
        httpx.Response(200, content=b'\xff\xfe not json'),
    ])
    def test_find_skips_what_is_not_a_pi(self, finder, network, answer):
        network.hellos['192.168.86.50'] = answer
        assert asyncio.run(finder.find(['192.168.86.50', HOME_PI.address])) == [HOME_PI]

    def test_find_never_follows_a_redirect(self, finder, network):
        network.hellos['192.168.86.50'] = httpx.Response(302, headers={'location': f'http://{HOME_PI.address}:'
                                                                                   f'{network.port}/api/cloud/hello'})
        asyncio.run(finder.find(['192.168.86.50']))
        assert [request.url.host for request in network.seen] == ['192.168.86.50']

    @pytest.mark.parametrize('error', [httpx.ConnectTimeout('slow'), httpx.ReadTimeout('slow'),
                                       httpx.ConnectError('refused'), httpx.RemoteProtocolError('junk'),
                                       httpx.DecodingError('bad gzip'), ValueError('something odd')])
    def test_find_skips_an_address_that_fails(self, finder, network, error):
        def fails(_request):
            raise error
        network.hellos['192.168.86.50'] = fails
        assert asyncio.run(finder.find(['192.168.86.50', HOME_PI.address])) == [HOME_PI]

    def test_find_gives_up_on_an_address_that_never_answers(self, finder, network):
        network.hellos['192.168.86.50'] = hang
        assert asyncio.run(asyncio.wait_for(finder.find(['192.168.86.50', HOME_PI.address]), 5)) == [HOME_PI]

    def test_find_knocks_on_at_most_at_once_addresses_at_the_same_time(self):
        finder, network = real_finder(at_once=3)
        busy = {'now': 0, 'most': 0}

        async def counted(_request):
            busy['now'] += 1
            busy['most'] = max(busy['most'], busy['now'])
            await asyncio.sleep(0.01)
            busy['now'] -= 1
            return httpx.Response(404)
        addresses = [f'192.168.86.{n}' for n in range(100, 120)]
        for address in addresses:
            network.hellos[address] = counted
        asyncio.run(finder.find(addresses))
        assert busy['most'] == 3

    def test_offer_posts_the_server_and_code_to_the_pi(self, finder, network):
        asyncio.run(finder.offer(HOME_PI.address, SERVER, CODE))
        (request,) = network.seen
        assert (request.method, str(request.url)) == ('POST',
                                                     f'http://{HOME_PI.address}:{network.port}/api/cloud/link')
        assert json.loads(request.content) == {'server': SERVER, 'code': CODE}
        assert request.headers['content-type'] == 'application/json'

    def test_offer_never_follows_a_redirect(self, finder, network):
        network.links[HOME_PI.address] = httpx.Response(307, headers={'location': 'http://192.168.86.77:5000/x'})
        with pytest.raises(PiOfferFailed) as failed:
            asyncio.run(finder.offer(HOME_PI.address, SERVER, CODE))
        assert failed.value.kind == 'no_answer'
        assert len(network.seen) == 1

    @pytest.mark.parametrize(('status', 'said', 'kind'), [
        (409, 'already_linked', 'already_linked'), (409, 'busy', 'busy'), (409, 'not_saved', 'not_saved'),
        (403, 'not_caller', 'not_caller'), (502, 'unavailable', 'unavailable'),
        (400, 'refused', 'refused'), (400, 'bad_request', 'refused'),
    ])
    def test_the_pis_own_kind_says_why(self, finder, network, status, said, kind):
        network.links[HOME_PI.address] = httpx.Response(status, json={'error': 'Some words.', 'kind': said})
        with pytest.raises(PiOfferFailed) as failed:
            asyncio.run(finder.offer(HOME_PI.address, SERVER, CODE))
        assert failed.value.kind == kind
        assert failed.value.reason == ('Some words.' if kind == 'refused' else None)   # the others have our words

    @pytest.mark.parametrize(('status', 'kind'), [(400, 'refused'), (403, 'not_caller'), (409, 'already_linked'),
                                                  (502, 'unavailable')])
    @pytest.mark.parametrize('said', [None, 'something_new', 5, ['busy']])
    def test_without_a_kind_it_knows_the_status_says_why(self, finder, network, status, kind, said):
        body = {'error': 'Some words.'} if said is None else {'error': 'Some words.', 'kind': said}
        network.links[HOME_PI.address] = httpx.Response(status, json=body)
        with pytest.raises(PiOfferFailed) as failed:
            asyncio.run(finder.offer(HOME_PI.address, SERVER, CODE))
        assert failed.value.kind == kind

    def test_a_kind_that_is_not_json_falls_back_to_the_status(self, finder, network):
        network.links[HOME_PI.address] = httpx.Response(409, html='<h1>busy</h1>')
        with pytest.raises(PiOfferFailed) as failed:
            asyncio.run(finder.offer(HOME_PI.address, SERVER, CODE))
        assert (failed.value.kind, failed.value.reason) == ('already_linked', None)

    def test_a_long_reason_is_cut_between_words(self, finder, network):
        most = cloud_config().find_pis.reason_max_chars
        words = ' '.join(['word'] * most)
        network.links[HOME_PI.address] = httpx.Response(400, json={'error': words, 'kind': 'refused'})
        with pytest.raises(PiOfferFailed) as failed:
            asyncio.run(finder.offer(HOME_PI.address, SERVER, CODE))
        reason = failed.value.reason
        assert reason is not None and len(reason) <= most
        assert reason.endswith('…') and set(reason[:-1].split()) == {'word'} and len(reason) > most - 6

    def test_a_long_reason_with_no_spaces_is_cut_too(self, finder, network):
        most = cloud_config().find_pis.reason_max_chars
        network.links[HOME_PI.address] = httpx.Response(400, json={'error': 'x' * (most + 1), 'kind': 'refused'})
        with pytest.raises(PiOfferFailed) as failed:
            asyncio.run(finder.offer(HOME_PI.address, SERVER, CODE))
        assert failed.value.reason == 'x' * (most - 1) + '…'

    def test_a_reason_that_just_fits_is_kept_whole(self, finder, network):
        most = cloud_config().find_pis.reason_max_chars
        network.links[HOME_PI.address] = httpx.Response(400, json={'error': f'  {"x" * most}  ', 'kind': 'refused'})
        with pytest.raises(PiOfferFailed) as failed:
            asyncio.run(finder.offer(HOME_PI.address, SERVER, CODE))
        assert failed.value.reason == 'x' * most

    @pytest.mark.parametrize('body', [{'error': 'bad\x00words'}, {'error': 'two\nlines'},
                                      {'error': 5}, {'error': '  '}, {'nothing': 'here'}, ['error']])
    def test_a_reason_that_is_not_clean_text_is_dropped(self, finder, network, body):
        network.links[HOME_PI.address] = httpx.Response(400, json=body)
        with pytest.raises(PiOfferFailed) as failed:
            asyncio.run(finder.offer(HOME_PI.address, SERVER, CODE))
        assert (failed.value.kind, failed.value.reason) == ('refused', None)

    def test_a_refusal_that_is_not_json_has_no_reason(self, finder, network):
        network.links[HOME_PI.address] = httpx.Response(400, html='<h1>no</h1>')
        with pytest.raises(PiOfferFailed) as failed:
            asyncio.run(finder.offer(HOME_PI.address, SERVER, CODE))
        assert (failed.value.kind, failed.value.reason) == ('refused', None)

    def test_a_huge_refusal_is_not_read_as_a_reason(self, finder, network):
        network.links[HOME_PI.address] = httpx.Response(400, content=b'{"error": "' + b'x' * 10_000 + b'"}')
        with pytest.raises(PiOfferFailed) as failed:
            asyncio.run(finder.offer(HOME_PI.address, SERVER, CODE))
        assert (failed.value.kind, failed.value.reason) == ('refused', None)

    @pytest.mark.parametrize('answer', [httpx.Response(500), httpx.Response(503, json={'error': 'cloud down'}),
                                        httpx.Response(404), httpx.Response(201, json={'linked': True})])
    def test_any_other_status_is_no_answer(self, finder, network, answer):
        network.links[HOME_PI.address] = answer
        with pytest.raises(PiOfferFailed) as failed:
            asyncio.run(finder.offer(HOME_PI.address, SERVER, CODE))
        assert failed.value.kind == 'no_answer'

    @pytest.mark.parametrize('error', [httpx.ConnectTimeout('slow'), httpx.ReadTimeout('slow'),
                                       httpx.ConnectError(f'refused while sending {CODE}')])
    def test_a_failed_send_is_no_answer(self, finder, network, error):
        def fails(_request):
            raise error
        network.links[HOME_PI.address] = fails
        with pytest.raises(PiOfferFailed) as failed:
            asyncio.run(finder.offer(HOME_PI.address, SERVER, CODE))
        assert failed.value.kind == 'no_answer'
        assert failed.value.__cause__ is None and failed.value.__suppress_context__

    def test_an_offer_that_never_ends_gives_up_at_the_link_timeout(self, finder, network):
        network.links[HOME_PI.address] = hang
        with pytest.raises(PiOfferFailed) as failed:
            asyncio.run(asyncio.wait_for(finder.offer(HOME_PI.address, SERVER, CODE), 5))
        assert failed.value.kind == 'no_answer'

    def test_logs_hold_no_code_and_no_reply(self, finder, network, caplog):
        caplog.set_level(logging.DEBUG)
        secret_words = 'private-words-from-the-pi'
        network.hellos['192.168.86.50'] = httpx.Response(200, html=f'<p>{secret_words}</p>')
        network.links[HOME_PI.address] = httpx.Response(400, json={'error': secret_words})

        def fails(_request):
            raise httpx.ConnectError(f'refused while sending {CODE} {secret_words}')
        network.links[LONG_PI.address] = fails
        asyncio.run(finder.find(['192.168.86.50', HOME_PI.address]))
        for address in (HOME_PI.address, LONG_PI.address, LINKED_PI.address, NOBODY):
            with pytest.raises(PiOfferFailed):
                asyncio.run(finder.offer(address, SERVER, CODE))
        assert caplog.text
        assert CODE not in caplog.text and secret_words not in caplog.text

    def test_closing_closes_its_client(self, finder):
        asyncio.run(finder.aclose())
        assert finder._client.is_closed

    def test_its_own_client_ignores_proxy_settings_and_follows_no_redirects(self):
        config = cloud_config()
        finder = HttpPiFinder(config.find_pis, config.pairing.name_max_chars)
        assert finder._client.trust_env is False
        assert finder._client.follow_redirects is False
        asyncio.run(finder.aclose())
