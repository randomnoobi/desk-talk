"""raspberry_pi/camera/net.py: one HTTP request with the standard library; no answer is always a NoAnswer."""

import socket
import threading

import pytest

from raspberry_pi.camera import net


class OneReply:
    """A server on 127.0.0.1 that reads one request and writes `reply` (raw bytes), then hangs up."""

    def __init__(self, reply: bytes):
        self.reply = reply
        self.sock = socket.socket()
        self.sock.bind(('127.0.0.1', 0))
        self.sock.listen(1)
        self.url = f'http://127.0.0.1:{self.sock.getsockname()[1]}/detect'
        self.thread = threading.Thread(target=self._serve, daemon=True)

    def _serve(self):
        conn, _ = self.sock.accept()
        with conn:
            conn.recv(65536)
            conn.sendall(self.reply)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.sock.close()
        self.thread.join(2)


def test_an_error_reply_comes_back_with_its_status_and_body():
    body = b'{"detail": "the model crashed"}'
    reply = b'HTTP/1.1 500 Oops\r\nContent-Length: %d\r\n\r\n%s' % (len(body), body)
    with OneReply(reply) as server:
        assert net.call('POST', server.url, body=b'x', timeout=2) == (500, body)


def test_an_error_reply_cut_off_half_way_is_no_answer():
    """The server says the error reply is 100 bytes long, then hangs up after 5."""
    cut_off = b'HTTP/1.1 500 Oops\r\nContent-Length: 100\r\n\r\nshort'
    with OneReply(cut_off) as server, pytest.raises(net.NoAnswer, match='half-way'):
        net.call('POST', server.url, body=b'x', timeout=2)
