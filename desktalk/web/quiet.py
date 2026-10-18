"""Only print web requests that failed (the camera sends 3 pictures a second; the app polls too)."""

from werkzeug.serving import WSGIRequestHandler


class QuietHandler(WSGIRequestHandler):
    def log_request(self, code='-', size='-'):
        try:
            failed = int(code) >= 400
        except (TypeError, ValueError):
            failed = True
        if failed:
            super().log_request(code, size)
