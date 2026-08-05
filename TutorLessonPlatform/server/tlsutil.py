"""TLS helpers for HTTPS/WSS."""

from __future__ import annotations

import ssl
import subprocess
import sys
from pathlib import Path

CERT_DIR = Path(__file__).resolve().parent / "certs"
CERT_FILE = CERT_DIR / "cert.pem"
KEY_FILE = CERT_DIR / "key.pem"


def ensure_certs() -> tuple[Path, Path]:
    CERT_DIR.mkdir(parents=True, exist_ok=True)
    if CERT_FILE.exists() and KEY_FILE.exists():
        try:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(str(CERT_FILE), str(KEY_FILE))
            return CERT_FILE, KEY_FILE
        except ssl.SSLError:
            pass

    script = Path(__file__).resolve().parent.parent / "scripts" / "gen_cert.py"
    subprocess.run([sys.executable, str(script)], check=True)
    return CERT_FILE, KEY_FILE


def make_ssl_context() -> ssl.SSLContext:
    cert, key = ensure_certs()
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(certfile=str(cert), keyfile=str(key))
    return ctx
