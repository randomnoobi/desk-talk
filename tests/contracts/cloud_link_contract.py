"""What every CloudLink must do (desktalk.ports.CloudLink): send events to the cloud server and say what it did.
The real one (HttpCloudLink, against a small server on this computer) and the recording stand-in in
tests/fakes/parts.py run these tests (test_adapters_cloud_link.py)."""

from __future__ import annotations

import pytest

from desktalk.domain import CloudNotLinked, CloudUnavailable
from desktalk.ports import CloudLink
from desktalk_shared.cloud_api import IngestReply

EVENTS = [{'id': 'a-1', 'ts': 1.0, 'type': 'habit', 'habit': 'hydration', 'action': 'sip', 'ml': 80},
          {'id': 'a-2', 'ts': 2.0, 'type': 'habit', 'habit': 'breaks', 'action': 'break', 'minutes': 5}]


class CloudLinkContract:
    """Subclass this and give three fixtures:

    - `link`: a CloudLink to a server that knows us and keeps what it gets (nothing sent yet);
    - `unknown_link`: one whose server says it doesn't know us (a wrong or removed token);
    - `dead_link`: one whose server doesn't answer.
    """

    def test_it_is_a_cloud_link(self, link):
        assert isinstance(link, CloudLink)

    def test_a_send_says_how_many_were_kept(self, link):
        reply = link.send(EVENTS, caught_up=False)
        assert isinstance(reply, IngestReply)
        assert (reply.accepted, reply.repeats, reply.refused) == (2, 0, ())

    def test_sending_the_same_events_again_counts_repeats(self, link):
        link.send(EVENTS, caught_up=False)
        reply = link.send(EVENTS, caught_up=True)
        assert (reply.accepted, reply.repeats) == (0, 2)

    def test_an_empty_send_is_fine(self, link):
        assert link.send([], caught_up=True).accepted == 0

    def test_a_server_that_does_not_know_us_raises_not_linked(self, unknown_link):
        with pytest.raises(CloudNotLinked):
            unknown_link.send(EVENTS, caught_up=False)

    def test_a_server_that_does_not_answer_raises_unavailable(self, dead_link):
        with pytest.raises(CloudUnavailable):
            dead_link.send(EVENTS, caught_up=False)
