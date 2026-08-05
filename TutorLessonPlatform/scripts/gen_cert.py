#!/usr/bin/env python3
"""Generate a self-signed TLS certificate without third-party packages."""

from __future__ import annotations

import datetime
import ipaddress
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CERT_DIR = ROOT / "server" / "certs"
CERT_FILE = CERT_DIR / "cert.pem"
KEY_FILE = CERT_DIR / "key.pem"


def via_openssl() -> bool:
    openssl = shutil.which("openssl")
    if not openssl:
        return False
    CERT_DIR.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            openssl,
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-keyout",
            str(KEY_FILE),
            "-out",
            str(CERT_FILE),
            "-days",
            "825",
            "-nodes",
            "-subj",
            "/CN=localhost",
            "-addext",
            "subjectAltName=DNS:localhost,IP:127.0.0.1",
        ],
        check=True,
    )
    return True


def via_stdlib() -> None:
    """Create cert using available Python modules; prefers cryptography if installed, else openssl required."""
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
    except ImportError as exc:
        raise SystemExit(
            "Нужен OpenSSL в PATH или пакет cryptography для генерации сертификата.\n"
            "Установите OpenSSL либо выполните: py -3 -m pip install cryptography"
        ) from exc

    CERT_DIR.mkdir(parents=True, exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=1))
        .not_valid_after(datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=825))
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
                ]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    KEY_FILE.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    CERT_FILE.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


def main() -> None:
    if CERT_FILE.exists() and KEY_FILE.exists():
        print(f"Уже есть: {CERT_FILE}")
        return
    if via_openssl():
        print(f"Сертификат создан через OpenSSL: {CERT_FILE}")
        return
    via_stdlib()
    print(f"Сертификат создан: {CERT_FILE}")


if __name__ == "__main__":
    main()
