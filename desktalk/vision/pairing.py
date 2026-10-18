"""
Pairing the camera (Pi or phone) with this computer.

- A short random pairing key, so other devices on the Wi-Fi can't send pictures.
  It's saved in certs/pair-key.txt and reused after a restart.
- This computer's Wi-Fi address (what the Pi / phone connect to).
- For the phone only: a self-signed HTTPS certificate (phone browsers only allow
  the camera on https:// pages) and a QR code.
"""

from __future__ import annotations

import datetime
import ipaddress
import math
import os
import secrets
import socket


def make_pair_key(path: str | None = None) -> str:
    """Short random key included in the QR link / Pi command so only your camera can send frames.

    With `path`, the key is saved there and reused next time, so the phone's
    bookmarked page and the Pi's command keep working after you restart the server."""
    if path:
        try:
            with open(path) as f:
                key = f.read().strip()
            if 6 <= len(key) <= 64 and key.replace('-', '').replace('_', '').isalnum():
                return key
        except OSError:
            pass
    key = secrets.token_urlsafe(6)
    if path:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w') as f:
                f.write(key)
            os.chmod(path, 0o600)
        except OSError:
            pass
    return key


def parse_gravity(header: str | None):
    """'x,y,z' from the X-Desk-Gravity header -> (x, y, z) floats, or None."""
    if not header:
        return None
    try:
        parts = [float(v) for v in header.split(',')[:3]]
    except ValueError:
        return None
    if len(parts) != 3 or not all(abs(v) < 50 for v in parts):
        return None
    return tuple(parts)


def tilt_between(g1, g2) -> float:
    """Angle in degrees between two gravity readings (how far the phone has tipped)."""
    a = math.sqrt(sum(v * v for v in g1)); b = math.sqrt(sum(v * v for v in g2))
    if a < 1 or b < 1:
        return 0.0
    cos = sum(x * y for x, y in zip(g1, g2)) / (a * b)
    return math.degrees(math.acos(max(-1.0, min(1.0, cos))))


def lan_ip() -> str:
    """This computer's address on the Wi-Fi network (what the Pi / phone connect to)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # UDP "connect" sends nothing; it just picks the outgoing network interface.
        s.connect(('8.8.8.8', 80))
        return s.getsockname()[0]
    except OSError:
        return '127.0.0.1'
    finally:
        s.close()


def ensure_cert(cert_dir: str, ip: str):
    """
    Create (or reuse) a self-signed HTTPS certificate for this computer's IP.
    Returns (cert_path, key_path), or None if the `cryptography` package is missing.
    """
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
    except ImportError:
        return None

    os.makedirs(cert_dir, exist_ok=True)
    cert_path = os.path.join(cert_dir, 'desk-talk.crt')
    key_path = os.path.join(cert_dir, 'desk-talk.key')

    # Reuse the existing certificate if it already covers this IP and hasn't expired.
    if os.path.isfile(cert_path) and os.path.isfile(key_path):
        try:
            with open(cert_path, 'rb') as f:
                cert = x509.load_pem_x509_certificate(f.read())
            sans = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
            ips = [str(a) for a in sans.get_values_for_type(x509.IPAddress)]
            not_expired = cert.not_valid_after_utc > datetime.datetime.now(datetime.timezone.utc)
            if ip in ips and not_expired:
                return cert_path, key_path
        except Exception:
            pass  # unreadable or old → make a new one

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Desk Talk (local)')])
    now = datetime.datetime.now(datetime.timezone.utc)
    alt_names = [x509.DNSName('localhost'), x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]
    try:
        alt_names.append(x509.IPAddress(ipaddress.ip_address(ip)))
    except ValueError:
        pass
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=365))
        .add_extension(x509.SubjectAlternativeName(alt_names), critical=False)
        .sign(key, hashes.SHA256())
    )
    with open(key_path, 'wb') as f:
        f.write(key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ))
    os.chmod(key_path, 0o600)   # private key: readable only by you
    with open(cert_path, 'wb') as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    return cert_path, key_path


def qr_svg(text: str):
    """QR code as an SVG string (None if the `qrcode` package isn't installed)."""
    try:
        import qrcode
        import qrcode.image.svg
    except ImportError:
        return None
    img = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, border=2)
    svg = img.to_string(encoding='unicode')
    # Drop the XML declaration so it can be dropped straight into HTML.
    return svg[svg.find('<svg'):]


def print_qr(text: str):
    """Print a scannable QR code in the terminal (skipped if qrcode isn't installed)."""
    try:
        import qrcode
    except ImportError:
        return
    qr = qrcode.QRCode(border=1)
    qr.add_data(text)
    qr.make(fit=True)
    qr.print_ascii(invert=True)
