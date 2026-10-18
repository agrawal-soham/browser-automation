"""Makes the VLM worker's TLS certificate and the shared token, on Laptop B itself (Windows or Linux), so the private
key never leaves the machine that uses it. Only cert.pem (public) and the token are copied to Laptop A.

  python tools/make_worker_cert.py                    # detects this laptop's LAN address
  python tools/make_worker_cert.py --ip 192.168.1.20  # or names it
  python tools/make_worker_cert.py --force            # new certificate (the address changed, or it expired)

Files (in --out, default C:\\privacy-agent\\tls on Windows, ~/privacy-agent/tls elsewhere):
  key.pem   private key: stays here
  cert.pem  certificate naming this laptop's IP: copy to Laptop A
  token     shared secret: copy to Laptop A over a private channel (kept if it already exists)
"""

from __future__ import annotations

import argparse
import datetime as dt
import ipaddress
import os
import secrets
import socket
import sys
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID


def default_out() -> Path:
    return Path(r"C:\privacy-agent\tls") if os.name == "nt" else Path.home() / "privacy-agent" / "tls"


def lan_ip() -> str:
    """The address this laptop uses to reach the network. A UDP connect sends no packet; it only picks the route."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))  # TEST-NET-1: never routed anywhere real
        return s.getsockname()[0]
    finally:
        s.close()


def make_cert(ip: str, days: int) -> tuple[bytes, bytes]:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "privacy-agent vlm worker")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=days))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(ip)), x509.DNSName("laptop-b")]),
            critical=False,
        )
        # Self-signed and trusted directly by Laptop A (VLM_CA_FILE), so it is its own CA.
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(key, hashes.SHA256())
    )
    key_pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    return cert.public_bytes(serialization.Encoding.PEM), key_pem


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ip", help="this laptop's LAN address (detected when left out)")
    p.add_argument("--out", type=Path, default=default_out())
    p.add_argument("--days", type=int, default=90)
    p.add_argument("--force", action="store_true", help="replace an existing certificate")
    a = p.parse_args()

    ip = a.ip or lan_ip()
    ipaddress.ip_address(ip)  # fails loudly on a typo
    a.out.mkdir(parents=True, exist_ok=True)
    cert_path, key_path, token_path = a.out / "cert.pem", a.out / "key.pem", a.out / "token"

    if cert_path.exists() and not a.force:
        current = x509.load_pem_x509_certificate(cert_path.read_bytes())
        names = current.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        ips = [str(x) for x in names.get_values_for_type(x509.IPAddress)]
        print(f"Certificate already exists for {', '.join(ips)}, valid until {current.not_valid_after_utc:%Y-%m-%d}.")
        if ip not in ips:
            print(f"WARNING: this laptop is now {ip}. Run again with --force, then copy the new cert.pem to Laptop A.")
            return 2
    else:
        cert_pem, key_pem = make_cert(ip, a.days)
        key_path.write_bytes(key_pem)
        cert_path.write_bytes(cert_pem)
        print(f"Made a certificate for {ip}, valid {a.days} days.")
    if not token_path.exists():
        token_path.write_text(secrets.token_urlsafe(32))
        print("Made a new token.")
    if os.name != "nt":
        for f in (key_path, token_path):
            f.chmod(0o600)

    print(f"\nFiles in {a.out}:")
    print("  key.pem   stays on this laptop")
    print("  cert.pem  copy to Laptop A:  ~/privacy-agent/tls/laptop-a/laptop-b.pem")
    print("  token     copy to Laptop A:  ~/privacy-agent/tls/laptop-a/token   (private channel only)")
    print(f"\nLaptop A connects to:  wss://{ip}:8765")
    return 0


if __name__ == "__main__":
    sys.exit(main())
