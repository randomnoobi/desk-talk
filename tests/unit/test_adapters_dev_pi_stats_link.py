"""HttpPiStatsLink: the computer's line to the Pi's numbers. A fake opener stands in for the Wi-Fi: no network."""

from __future__ import annotations

import http.client
import io
import json
import socket
import urllib.error
import urllib.request

import pytest

from desktalk.adapters.dev.pi_stats_link import HttpPiStatsLink
from desktalk.domain import StatsUnavailable

GOOD = {'collecting': True, 'every_sec': 2, 'keep_min': 30, 'info': {'cores': 4}, 'samples': [{'at': 1.0}]}


class Opener:
    """Answers every request with `body`, or raises `error`, and keeps what it was asked."""

    def __init__(self, body: bytes = b'', error: BaseException | None = None):
        self.body = body
        self.error = error
        self.asked: list[tuple[str, float]] = []

    def __call__(self, url: str, timeout_sec: float) -> bytes:
        self.asked.append((url, timeout_sec))
        if self.error is not None:
            raise self.error
        return self.body


def link_with(opener: Opener, address: str = 'desktalk.local:5000', timeout_sec: float = 3) -> HttpPiStatsLink:
    return HttpPiStatsLink(address, timeout_sec, opener=opener)


def problem(opener: Opener) -> str:
    with pytest.raises(StatsUnavailable) as caught:
        link_with(opener).fetch(None)
    return str(caught.value)


def test_it_asks_the_pi_for_everything_when_since_is_not_given():
    opener = Opener(json.dumps(GOOD).encode())
    link_with(opener).fetch(None)
    assert opener.asked == [('http://desktalk.local:5000/api/system/stats', 3)]


def test_it_asks_only_for_newer_samples_when_since_is_given():
    opener = Opener(json.dumps(GOOD).encode())
    link_with(opener, address='192.168.1.20:5000', timeout_sec=1.5).fetch(1762900000.25)
    assert opener.asked == [('http://192.168.1.20:5000/api/system/stats?since=1762900000.25', 1.5)]


def test_a_since_of_zero_is_still_sent():
    opener = Opener(json.dumps(GOOD).encode())
    link_with(opener).fetch(0.0)
    assert opener.asked[0][0].endswith('?since=0.0')


def test_good_json_comes_back_as_it_is():
    assert link_with(Opener(json.dumps(GOOD).encode())).fetch(None) == GOOD


def test_no_answer_in_time_says_how_long_it_waited():
    assert problem(Opener(error=TimeoutError('timed out'))) == 'no answer in 3 s'


def test_a_connection_that_times_out_says_the_same():
    assert problem(Opener(error=urllib.error.URLError(TimeoutError('timed out')))) == 'no answer in 3 s'


def test_a_url_error_becomes_stats_unavailable():
    assert problem(Opener(error=urllib.error.URLError(ConnectionRefusedError(61, 'Connection refused'))))


def test_nothing_listening_says_so():
    words = problem(Opener(error=urllib.error.URLError(ConnectionRefusedError(61, 'Connection refused'))))
    assert 'refused' in words


def test_a_name_that_cannot_be_found_says_so():
    words = problem(Opener(error=urllib.error.URLError(socket.gaierror(8, 'nodename nor servname provided'))))
    assert words == "its name couldn't be found"


def test_a_pi_that_hangs_up_says_so():
    assert problem(Opener(error=http.client.RemoteDisconnected('closed'))) == 'it hung up without answering'
    assert problem(Opener(error=http.client.BadStatusLine('junk'))) == 'it hung up without answering'


def test_another_status_says_which():
    error = urllib.error.HTTPError('http://x/api/system/stats', 404, 'Not Found', http.client.HTTPMessage(), None)
    assert problem(Opener(error=error)) == 'it answered 404'


def test_some_other_os_error_gives_its_own_words():
    assert problem(Opener(error=OSError('Network is unreachable'))) == 'Network is unreachable'
    assert problem(Opener(error=OSError())) == 'OSError'


def test_an_address_that_cannot_be_used_says_so():
    assert problem(Opener(error=http.client.InvalidURL("nonnumeric port: 'x'"))) == "that address can't be used"


def test_an_address_urllib_cannot_make_sense_of_says_so():
    assert problem(Opener(error=ValueError('unknown url type'))) == "that address can't be used"


def test_an_address_written_with_http_in_front_still_works():
    opener = Opener(json.dumps(GOOD).encode())
    link_with(opener, address='http://desktalk.local:5000/').fetch(None)
    assert opener.asked[0][0] == 'http://desktalk.local:5000/api/system/stats'


def test_a_body_that_is_not_json_becomes_stats_unavailable():
    assert problem(Opener(b'not json')) == "it didn't send its numbers (the answer isn't JSON)"
    assert problem(Opener(b'\xff\xfe')) == "it didn't send its numbers (the answer isn't JSON)"


@pytest.mark.parametrize('body', [b'[]', b'{}', b'{"samples": {}}', b'"samples"', b'null'])
def test_json_without_a_samples_list_becomes_stats_unavailable(body):
    assert problem(Opener(body)) == "it didn't send its numbers"


def test_the_problem_never_repeats_the_address():
    """The route puts the address in its own sentence (prompts.yaml → dev.pi_stats_unreachable)."""
    for error in (TimeoutError(), urllib.error.URLError(ConnectionRefusedError(61, 'Connection refused'))):
        assert 'desktalk.local' not in problem(Opener(error=error))


class UrllibOpener:
    """Stands in for what urllib.request.build_opener makes: answers open(url, timeout) with `answer`, or raises it."""

    def __init__(self, answer: io.BytesIO | BaseException):
        self.answer = answer
        self.opened: list[tuple[str, float]] = []

    def open(self, url: str, timeout: float) -> io.BytesIO:
        self.opened.append((url, timeout))
        if isinstance(self.answer, BaseException):
            raise self.answer
        return self.answer


def use_urllib(monkeypatch, answer: io.BytesIO | BaseException) -> tuple[UrllibOpener, list[tuple]]:
    """Makes urllib.request.build_opener give a UrllibOpener (no network), and keeps the handlers it was given."""
    opener, built = UrllibOpener(answer), []

    def build_opener(*handlers):
        built.append(handlers)
        return opener

    monkeypatch.setattr(urllib.request, 'build_opener', build_opener)
    return opener, built


def test_the_usual_opener_asks_with_the_timeout(monkeypatch):
    opener, _ = use_urllib(monkeypatch, io.BytesIO(json.dumps(GOOD).encode()))
    assert HttpPiStatsLink('desktalk.local:5000', 2.5).fetch(None) == GOOD
    assert opener.opened == [('http://desktalk.local:5000/api/system/stats', 2.5)]


def test_the_usual_opener_never_goes_through_a_proxy(monkeypatch):
    """urllib would follow HTTP_PROXY from the environment: the Pi on the home Wi-Fi is asked straight."""
    monkeypatch.setenv('HTTP_PROXY', 'http://proxy.example:3128')
    monkeypatch.setenv('http_proxy', 'http://proxy.example:3128')
    _, built = use_urllib(monkeypatch, io.BytesIO(json.dumps(GOOD).encode()))
    HttpPiStatsLink('desktalk.local:5000', 2.5).fetch(None)
    proxies = [h for handlers in built for h in handlers if isinstance(h, urllib.request.ProxyHandler)]
    assert len(proxies) == 1
    assert vars(proxies[0])['proxies'] == {}           # an empty list of proxies: none, whatever the environment
    assert not hasattr(proxies[0], 'http_open')          # (each proxy it would use adds an <kind>_open)


def test_the_usual_opener_turns_a_refusal_into_stats_unavailable(monkeypatch):
    use_urllib(monkeypatch, urllib.error.URLError(ConnectionRefusedError(61, 'Connection refused')))
    with pytest.raises(StatsUnavailable):
        HttpPiStatsLink('desktalk.local:5000', 2.5).fetch(None)


def test_the_usual_opener_lets_go_of_an_error_reply(monkeypatch):
    body = io.BytesIO(b'Internal Server Error')
    error = urllib.error.HTTPError('http://x/api/system/stats', 500, 'Server Error', http.client.HTTPMessage(), body)
    use_urllib(monkeypatch, error)
    with pytest.raises(StatsUnavailable, match='^it answered 500$'):
        HttpPiStatsLink('desktalk.local:5000', 2.5).fetch(None)
    assert body.closed
