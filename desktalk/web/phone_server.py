"""
The phone camera's own small HTTPS server (only when camera.source is phone).

It can only do two things: show the camera page and accept frames. Phones
only allow the camera on https:// pages, so it uses a self-signed certificate.
"""

from __future__ import annotations

import threading

from flask import Flask, jsonify, request, send_from_directory

from .. import paths
from ..vision.pairing import ensure_cert, parse_gravity, qr_svg


def create_phone_app(app) -> Flask:
    phone = Flask('desk_talk_phone')
    phone.config['MAX_CONTENT_LENGTH'] = 8 * 1024 * 1024   # one JPEG frame is ~100 KB
    src = app.source

    @phone.route('/')
    @phone.route('/phone')
    @phone.route('/phone/')
    def phone_page():
        return send_from_directory(paths.PHONE_CAMERA_DIR, 'index.html')

    @phone.route('/phone/<path:filename>')
    def phone_static(filename):
        return send_from_directory(paths.PHONE_CAMERA_DIR, filename)

    @phone.route('/art/<path:filename>')
    @phone.route('/fonts/<path:filename>')
    def shared_assets(filename):
        folder = 'art' if request.path.startswith('/art/') else 'fonts'
        return send_from_directory(paths.WEB_APP_DIR / folder, filename)

    @phone.route('/api/phone/config')
    def phone_config():
        return jsonify({'fps': float(app.settings.camera.get('fps', 3))})

    @phone.route('/api/phone/frame', methods=['POST'])
    def phone_frame():
        if request.headers.get('X-Desk-Key') != app.pair_key:
            return jsonify({'error': 'Pairing key does not match. Scan the QR code on your computer again.'}), 403
        if not src.push_jpeg(request.get_data()):
            return jsonify({'error': 'not a JPEG image'}), 400
        rot = request.headers.get('X-Desk-Rotation', '')
        src.set_motion(parse_gravity(request.headers.get('X-Desk-Gravity')),
                       int(rot) % 360 if rot.lstrip('-').isdigit() else None)
        out = {'ok': True, 'fps': src.fps}
        tip = app.camera_health.phone_tip()
        if tip:
            out['tip'] = tip     # shown on the phone too, e.g. "Too dark"
        return jsonify(out)

    @phone.route('/api/phone/stop', methods=['POST'])
    def phone_stop():
        if request.headers.get('X-Desk-Key') != app.pair_key:
            return jsonify({'error': 'Pairing key does not match.'}), 403
        src.mark_stopped()
        return jsonify({'ok': True})

    return phone


def start_phone_server(app, ip: str) -> str | None:
    """Make an HTTPS certificate, then serve the phone page in a background thread. Returns the link."""
    cert = ensure_cert(str(paths.CERTS_DIR), ip)
    if not cert:
        print('  Phone camera : needs the `cryptography` package → pip install -r requirements.txt')
        return None
    port = int(app.settings.camera.get('phone_port', 5443))
    url = f'https://{ip}:{port}/phone?key={app.pair_key}'
    app.phone_url = url
    app.phone_qr_svg = qr_svg(url)
    from werkzeug.serving import make_server

    from .request_logging import QuietHandler
    srv = make_server('0.0.0.0', port, create_phone_app(app), threaded=True, ssl_context=cert,
                      request_handler=QuietHandler)
    threading.Thread(target=srv.serve_forever, daemon=True, name='PhoneHTTPS').start()
    return url

