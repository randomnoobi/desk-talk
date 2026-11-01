"""A stand-in AI company for tests/docker/test_cloud_stack.py. It runs as one more container on the stack's own
network, with no published port, and speaks the OpenAI chat completions form. It uses only the standard library and
contacts nothing. The test copies this text into the container, so it must stay free of dollar signs (compose would
read them)."""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer

REPLY = '# A fake week\n\nWritten by the fake AI.'


class FakeAI(BaseHTTPRequestHandler):
    def _answer(self, status: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:           # the health check
        self._answer(200, {'ok': True})

    def do_POST(self) -> None:
        asked = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        if self.headers.get('Authorization') != 'Bearer ' + os.environ['FAKE_AI_KEY']:
            self._answer(401, {'error': 'wrong key'})
            return
        has_data = '<data>' in asked['messages'][0]['content']
        text = REPLY if has_data else 'The request held no data.'
        self._answer(200, {'choices': [{'index': 0, 'finish_reason': 'stop',
                                        'message': {'role': 'assistant', 'content': text}}]})


if __name__ == '__main__':
    HTTPServer(('0.0.0.0', int(os.environ['FAKE_AI_PORT'])), FakeAI).serve_forever()
