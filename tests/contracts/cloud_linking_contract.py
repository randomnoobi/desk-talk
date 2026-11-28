"""What every CloudLinking must do (desktalk.ports.CloudLinking): say who the Pi is, and link it to the cloud server
the cloud website offers, but only to the computer asking, only once, and with a `kind` for every no that
POST /api/cloud/link knows. The real one (CloudLinker) runs these tests (test_pi_cloud_linking_contract.py)."""

from __future__ import annotations

import pytest

from desktalk.domain import CloudHello, CloudLinkRefused
from desktalk.ports import CloudLinking
from desktalk.web.routers.cloud import REFUSAL_STATUS

CALLER = '192.168.86.30'                     # the computer running the cloud server, on the home network
SERVER = f'http://{CALLER}:8080'
CODE = 'ABCD-EFGH'


def refusal(part: CloudLinking, server: str = SERVER, code: str = CODE, caller: str = CALLER) -> CloudLinkRefused:
    with pytest.raises(CloudLinkRefused) as caught:
        part.link(server, code, caller)
    assert caught.value.kind in REFUSAL_STATUS                   # the route has a status for every kind
    assert caught.value.text and CODE not in caught.value.text   # plain words, never the code
    return caught.value


class CloudLinkingContract:
    """Subclass this and give three fixtures, each a CloudLinking that isn't linked yet:

    - `linking`: the server it is offered takes the code;
    - `refusing_linking`: the server says no to the code;
    - `dead_linking`: the server doesn't answer.
    """

    def test_it_is_cloud_linking(self, linking):
        assert isinstance(linking, CloudLinking)

    def test_hello_says_a_name_and_not_linked(self, linking):
        hello = linking.hello()
        assert isinstance(hello, CloudHello) and isinstance(hello.name, str) and hello.name
        assert hello.linked is False

    def test_an_offer_from_the_computer_asking_links_it(self, linking):
        assert linking.link(SERVER, CODE, CALLER) is None
        assert linking.hello().linked is True

    def test_once_linked_it_says_no_to_another_offer(self, linking):
        linking.link(SERVER, CODE, CALLER)
        assert refusal(linking).kind == 'already_linked'
        assert linking.hello().linked is True

    @pytest.mark.parametrize('caller', ['192.168.86.99', '127.0.0.1', '8.8.8.8', ''])
    def test_it_links_only_to_the_computer_asking(self, linking, caller):
        assert refusal(linking, caller=caller).kind == 'not_caller'
        assert linking.hello().linked is False

    def test_a_server_on_the_internet_is_not_the_computer_asking(self, linking):
        assert refusal(linking, server='http://8.8.8.8:8080', caller='8.8.8.8').kind == 'not_caller'

    @pytest.mark.parametrize('server, code', [(f'ftp://{CALLER}', CODE), ('not an address', CODE), (SERVER, ''),
                                              (SERVER, '   '), (f'http://{CALLER}:99999', CODE)])
    def test_an_offer_that_is_not_a_web_address_and_a_code_is_a_bad_request(self, linking, server, code):
        assert refusal(linking, server=server, code=code).kind == 'bad_request'
        assert linking.hello().linked is False

    def test_a_code_the_server_says_no_to_is_refused_and_nothing_changes(self, refusing_linking):
        assert refusal(refusing_linking).kind == 'refused'
        assert refusing_linking.hello().linked is False

    def test_a_server_that_does_not_answer_is_unavailable_and_nothing_changes(self, dead_linking):
        assert refusal(dead_linking).kind == 'unavailable'
        assert dead_linking.hello().linked is False
