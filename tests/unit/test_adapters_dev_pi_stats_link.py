"""HttpPiStatsLink: the computer's line to the Pi's numbers, health and objects. A fake opener stands in for the
Wi-Fi: no network."""

from __future__ import annotations

import http.client
import io
import json
import socket
import urllib.error
import urllib.request

import pytest

from desktalk.adapters.dev.pi_stats_link import NOT_ITS_OBJECTS, HttpPiStatsLink
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


# ── the Pi's health (the developer page's System and Storage tabs) ──
HEALTH = {'uptime_sec': 60, 'camera': {'source': 'pi'}, 'checks': {'camera': {'level': 'ok', 'value': 2.0,
                                                                              'unit': 'pictures/s'}}}


def health_problem(opener: Opener) -> str:
    with pytest.raises(StatsUnavailable) as caught:
        link_with(opener).health()
    return str(caught.value)


def test_health_asks_the_pi_for_its_health():
    opener = Opener(json.dumps(HEALTH).encode())
    assert link_with(opener, address='http://desktalk.local:5000/', timeout_sec=1.5).health() == HEALTH
    assert opener.asked == [('http://desktalk.local:5000/api/health', 1.5)]


def test_health_says_what_went_wrong_in_the_same_words_as_the_numbers():
    assert health_problem(Opener(error=urllib.error.URLError(TimeoutError('timed out')))) == 'no answer in 3 s'
    assert health_problem(Opener(error=ValueError('unknown url type'))) == "that address can't be used"


def test_health_that_is_not_json_says_it_is_not_the_health_checks():
    """Not "its numbers": the System tab asked for the Pi's health checks."""
    assert health_problem(Opener(b'not json')) == "it didn't send its health checks (the answer isn't JSON)"


@pytest.mark.parametrize('body', [b'[]', b'{}', b'{"checks": []}', b'{"samples": []}', b'null'])
def test_health_without_a_checks_dict_says_it_is_not_the_health_checks(body):
    assert health_problem(Opener(body)) == "it didn't send its health checks"


# ── what the Pi's camera sees (the developer page's Live tab) ──
STATES = {'objects': {'cup': {'state': 'PRESENT', 'since': 5.0}}, 'watched_since': 1.0, 'changes': []}
STORY = {'label': 'pen/pencil', 'timeline': [{'ts': 5.0, 'kind': 'appeared'}], 'photos': []}


def not_found(url: str = 'http://x') -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, 404, 'Not Found', http.client.HTTPMessage(), None)


def test_states_asks_for_the_changes_since_the_time_given():
    opener = Opener(json.dumps(STATES).encode())
    assert link_with(opener, timeout_sec=1.5).states(0.0) == STATES
    assert opener.asked == [('http://desktalk.local:5000/api/objects/states?since=0.0', 1.5)]


def test_traced_labels_asks_for_the_smoothing_list():
    opener = Opener(b'{"labels": ["cup"]}')
    assert link_with(opener).traced_labels() == {'labels': ['cup']}
    assert opener.asked[0][0] == 'http://desktalk.local:5000/api/objects/smoothing'


def test_a_trace_sends_a_label_with_a_slash_in_the_query():
    opener = Opener(b'{"label": "pen/pencil", "steps": []}')
    assert link_with(opener).trace('pen/pencil') == {'label': 'pen/pencil', 'steps': []}
    assert opener.asked[0][0] == 'http://desktalk.local:5000/api/objects/smoothing/trace?label=pen%2Fpencil'


def test_a_story_sends_a_label_with_a_slash_quoted_in_the_path():
    opener = Opener(json.dumps(STORY).encode())
    assert link_with(opener).story('pen/pencil') == STORY
    assert opener.asked[0][0] == 'http://desktalk.local:5000/api/objects/pen%2Fpencil/story'


def test_a_story_the_pi_has_nothing_of_is_empty():
    """The Pi's story route answers 404 when there's nothing to tell: the Live tab shows an empty story."""
    assert link_with(Opener(error=not_found())).story('mug') == {'label': 'mug', 'timeline': [], 'photos': []}


def test_a_picture_comes_back_as_it_is():
    opener = Opener(b'\xff\xd8 not json at all')
    assert link_with(opener).photo('cup 1/2') == b'\xff\xd8 not json at all'
    assert opener.asked[0][0] == 'http://desktalk.local:5000/api/objects/photo/cup%201%2F2'


def test_a_picture_that_is_gone_is_none():
    assert link_with(Opener(error=not_found())).photo('old') is None


def test_a_picture_the_pi_fails_to_send_says_so():
    error = urllib.error.HTTPError('http://x', 500, 'Server Error', http.client.HTTPMessage(), None)
    with pytest.raises(StatsUnavailable, match='^it answered 500$'):
        link_with(Opener(error=error)).photo('cup-1')


def test_a_404_for_the_states_is_still_a_problem():
    """Only a story or a picture may be missing: a Pi without the states route is too old, or not a Pi."""
    with pytest.raises(StatsUnavailable, match='^it answered 404$'):
        link_with(Opener(error=not_found())).states(0.0)


@pytest.mark.parametrize('ask, body', [
    (lambda link: link.states(0.0), b'{"objects": []}'),
    (lambda link: link.states(0.0), b'{}'),
    (lambda link: link.traced_labels(), b'{"labels": {}}'),
    (lambda link: link.trace('cup'), b'{"label": "cup"}'),
    (lambda link: link.story('cup'), b'{"label": "cup", "timeline": {}}'),
    (lambda link: link.story('cup'), b'[]'),
])
def test_a_reply_of_the_wrong_shape_says_it_is_not_what_the_camera_sees(ask, body):
    with pytest.raises(StatsUnavailable) as caught:
        ask(link_with(Opener(body)))
    assert str(caught.value) == NOT_ITS_OBJECTS


def test_objects_that_are_not_json_say_so():
    with pytest.raises(StatsUnavailable) as caught:
        link_with(Opener(b'<html>')).states(0.0)
    assert str(caught.value) == f"{NOT_ITS_OBJECTS} (the answer isn't JSON)"


def test_the_objects_say_what_went_wrong_in_the_same_words_as_the_numbers():
    with pytest.raises(StatsUnavailable, match='^no answer in 3 s$'):
        link_with(Opener(error=urllib.error.URLError(TimeoutError('timed out')))).story('cup')
    with pytest.raises(StatsUnavailable, match="^that address can't be used$"):
        link_with(Opener(error=ValueError('unknown url type'))).photo('cup-1')
