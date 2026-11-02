"""
How the Pi and the phone find the app:

    links = Links(lan_ip(), web_port, pair_key, phone_port=5443, public_address='')
    links.web_url         # http://192.168.1.20:5000  (http://desktalk.local:5000 when public_address says so)
    links.pi_command      # python3 -m raspberry_pi.camera --server http://192.168.1.20:5000 --key AbC123xY
                          # ('' when public_address is set: the Pi starts its own camera)
    links.phone_url       # https://192.168.1.20:5443/phone?key=AbC123xY  ('' when the phone isn't the camera)
    links.phone_qr_svg    # that link as a QR code, for the app to show

Phones only allow the camera on https:// pages, so the phone page's server has a certificate of its own,
made by ensure_cert() for this computer's Wi-Fi address and kept for the next start:

    cert = ensure_cert(folder, lan_ip(), config.camera, save=write_atomically)
"""

from __future__ import annotations

import datetime
import ipaddress
import logging
import socket
from collections.abc import Callable
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from desktalk.config import CameraSettings

log = logging.getLogger(__name__)

# lan_ip() "connects" a UDP socket to this address to learn which network the computer would use. Nothing
# is sent: a UDP connect only picks the way out.
ANY_OUTSIDE_ADDRESS = ('8.8.8.8', 80)
THIS_COMPUTER = '127.0.0.1'
RSA_KEY_BITS = 2048                 # the usual size for a certificate's key
RSA_PUBLIC_EXPONENT = 65537         # the value everyone uses
QR_BORDER_IN_APP = 2                # the empty border around the QR code, in squares


def lan_ip() -> str:
    """This computer's address on the Wi-Fi (what the Pi and the phone connect to); 127.0.0.1 when offline."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(ANY_OUTSIDE_ADDRESS)
        return s.getsockname()[0]
    except OSError:
        return THIS_COMPUTER
    finally:
        s.close()


@dataclass(frozen=True)
class Links:
    """address: the app's computer on the Wi-Fi. phone_port: the phone page's HTTPS port, when its server runs.
    public_address: how other devices reach the app, like desktalk.local:5000 (web.public_address); '' when it
    isn't set. Inside Docker the address above is Docker's own, so this is what people are told to open."""

    address: str
    web_port: int
    pair_key: str
    phone_port: int | None = None
    public_address: str = ''

    @property
    def web_url(self) -> str:
        """Where other devices open the app."""
        return f'http://{self.public_address or f"{self.address}:{self.web_port}"}'

    def pi_command_with_key_file(self, key_file: str) -> str:
        """What to run on the Pi, in the desk-talk folder, with the key read from a file there (so it is never
        written to a log). '' when public_address is set: the Pi starts its own camera."""
        if self.public_address:
            return ''
        return f'python3 -m raspberry_pi.camera --server {self.web_url} --key-file {key_file}'

    @property
    def pi_command(self) -> str:
        """What to run on the Pi, in the desk-talk folder, with the key in it. Only for the page, shown on this
        computer; never log it. '' when public_address is set."""
        if self.public_address:
            return ''
        return f'python3 -m raspberry_pi.camera --server {self.web_url} --key {self.pair_key}'

    @property
    def phone_url(self) -> str:
        if self.phone_port is None:
            return ''
        return f'https://{self.address}:{self.phone_port}/phone?key={self.pair_key}'

    @cached_property
    def phone_qr_svg(self) -> str | None:
        return qr_svg(self.phone_url) if self.phone_url else None


def qr_svg(text: str) -> str:
    """`text` as a QR code, an <svg> ready to put in a page."""
    import qrcode
    import qrcode.image.svg

    svg = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, border=QR_BORDER_IN_APP)
    out = svg.to_string(encoding='unicode')
    return out[out.find('<svg'):]          # without the <?xml ...?> line


# ── the phone page's HTTPS certificate ───────────────────────────────
# Writes a text file all-or-nothing, readable only by you (desktalk/adapters/storage/files.py: write_atomically).
SaveText = Callable[[Path, str], None]


@dataclass(frozen=True)
class Cert:
    cert_file: Path
    key_file: Path


def ensure_cert(folder: Path, address: str, camera: CameraSettings, *, save: SaveText) -> Cert:
    """The certificate in `folder` (camera.phone_cert_file, and its key in camera.phone_key_file) if it covers
    `address` and hasn't run out, else a new self-signed one, good for camera.phone_cert_days and written with
    `save`. The phone warns about it once ("not private"): it's this computer's own."""
    from cryptography import x509

    folder = Path(folder)
    cert = Cert(folder / camera.phone_cert_file, folder / camera.phone_key_file)
    try:
        saved = x509.load_pem_x509_certificate(cert.cert_file.read_bytes())
        names = saved.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        covers = address in [str(a) for a in names.get_values_for_type(x509.IPAddress)]
        if covers and cert.key_file.is_file() and saved.not_valid_after_utc > _now():
            return cert
    except FileNotFoundError:
        pass
    except (ValueError, x509.ExtensionNotFound) as exc:
        log.info('Making a new phone certificate: the old one could not be read (%s)', exc)
    _make_cert(cert, address, camera.phone_cert_days, save)
    return cert


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _make_cert(cert: Cert, address: str, valid_days: int, save: SaveText) -> None:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=RSA_PUBLIC_EXPONENT, key_size=RSA_KEY_BITS)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Desk Talk (this computer)')])
    names: list[x509.GeneralName] = [x509.DNSName('localhost'), x509.IPAddress(ipaddress.ip_address(THIS_COMPUTER))]
    try:
        if address != THIS_COMPUTER:
            names.append(x509.IPAddress(ipaddress.ip_address(address)))
    except ValueError:
        log.warning('%r is not an IP address, so the phone certificate only covers this computer', address)
    now = _now()
    built = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
             .serial_number(x509.random_serial_number())
             .not_valid_before(now - datetime.timedelta(days=1))      # in case the phone's clock is a little behind
             .not_valid_after(now + datetime.timedelta(days=valid_days))
             .add_extension(x509.SubjectAlternativeName(names), critical=False)
             .sign(key, hashes.SHA256()))
    # Each file all-or-nothing, and only you can read them (the private key above all).
    save(cert.key_file, key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                          serialization.NoEncryption()).decode('ascii'))
    save(cert.cert_file, built.public_bytes(serialization.Encoding.PEM).decode('ascii'))
