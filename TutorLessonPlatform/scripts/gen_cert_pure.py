#!/usr/bin/env python3
"""Minimal self-signed cert generator (stdlib only)."""

from __future__ import annotations

import datetime as dt
import hashlib
import os
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CERT_DIR = ROOT / "server" / "certs"
CERT_FILE = CERT_DIR / "cert.pem"
KEY_FILE = CERT_DIR / "key.pem"


def _egcd(a: int, b: int) -> tuple[int, int, int]:
    if a == 0:
        return b, 0, 1
    g, y, x = _egcd(b % a, a)
    return g, x - (b // a) * y, y


def _modinv(a: int, m: int) -> int:
    g, x, _ = _egcd(a % m, m)
    if g != 1:
        raise RuntimeError("modular inverse failed")
    return x % m


def _is_probable_prime(n: int, rounds: int = 12) -> bool:
    if n < 2:
        return False
    small = [2, 3, 5, 7, 11, 13, 17, 19, 23, 29]
    for p in small:
        if n == p:
            return True
        if n % p == 0:
            return False
    d, s = n - 1, 0
    while d % 2 == 0:
        d //= 2
        s += 1
    for _ in range(rounds):
        a = int.from_bytes(os.urandom(32), "big") % (n - 3) + 2
        x = pow(a, d, n)
        if x == 1 or x == n - 1:
            continue
        skip = False
        for _ in range(s - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                skip = True
                break
        if skip:
            continue
        return False
    return True


def _gen_prime(bits: int) -> int:
    while True:
        candidate = int.from_bytes(os.urandom(bits // 8), "big") | (1 << (bits - 1)) | 1
        if _is_probable_prime(candidate):
            return candidate


def _asn1_len(n: int) -> bytes:
    if n < 0x80:
        return bytes([n])
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(raw)]) + raw


def _asn1_int(value: int) -> bytes:
    if value == 0:
        body = b"\x00"
    else:
        body = value.to_bytes((value.bit_length() + 7) // 8, "big")
        if body[0] & 0x80:
            body = b"\x00" + body
    return b"\x02" + _asn1_len(len(body)) + body


def _asn1_seq(items: bytes) -> bytes:
    return b"\x30" + _asn1_len(len(items)) + items


def _asn1_octet(data: bytes) -> bytes:
    return b"\x04" + _asn1_len(len(data)) + data


def _asn1_oid(oid: str) -> bytes:
    parts = [int(x) for x in oid.split(".")]
    body = bytes([40 * parts[0] + parts[1]])
    for part in parts[2:]:
        stack = [part & 0x7F]
        part >>= 7
        while part:
            stack.append(0x80 | (part & 0x7F))
            part >>= 7
        body += bytes(reversed(stack))
    return b"\x06" + _asn1_len(len(body)) + body


def _asn1_utf8(text: str) -> bytes:
    data = text.encode("utf-8")
    return b"\x0c" + _asn1_len(len(data)) + data


def _asn1_bitstring(data: bytes) -> bytes:
    body = b"\x00" + data
    return b"\x03" + _asn1_len(len(body)) + body


def _pem(label: str, der: bytes) -> str:
    import base64

    b64 = base64.b64encode(der).decode("ascii")
    lines = [b64[i : i + 64] for i in range(0, len(b64), 64)]
    return f"-----BEGIN {label}-----\n" + "\n".join(lines) + f"\n-----END {label}-----\n"


def generate() -> None:
    CERT_DIR.mkdir(parents=True, exist_ok=True)
    e = 65537
    p = _gen_prime(1024)
    q = _gen_prime(1024)
    while q == p:
        q = _gen_prime(1024)
    n = p * q
    phi = (p - 1) * (q - 1)
    d = _modinv(e, phi)
    dmp1 = d % (p - 1)
    dmq1 = d % (q - 1)
    iqmp = _modinv(q, p)

    private_key = _asn1_seq(
        _asn1_int(0)
        + _asn1_int(n)
        + _asn1_int(e)
        + _asn1_int(d)
        + _asn1_int(p)
        + _asn1_int(q)
        + _asn1_int(dmp1)
        + _asn1_int(dmq1)
        + _asn1_int(iqmp)
    )

    # AlgorithmIdentifier rsaEncryption NULL (for SubjectPublicKeyInfo)
    rsa_alg = _asn1_seq(_asn1_oid("1.2.840.113549.1.1.1") + b"\x05\x00")
    # sha256WithRSAEncryption NULL (for certificate signature)
    sig_alg = _asn1_seq(_asn1_oid("1.2.840.113549.1.1.11") + b"\x05\x00")
    pub = _asn1_seq(_asn1_int(n) + _asn1_int(e))
    spki = _asn1_seq(rsa_alg + _asn1_bitstring(pub))

    cn = _asn1_seq(_asn1_seq(_asn1_oid("2.5.4.3") + _asn1_utf8("localhost")))
    name = _asn1_seq(cn)

    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    # UTCTime YYMMDDHHMMSSZ
    def utc_time(moment: dt.datetime) -> bytes:
        s = moment.strftime("%y%m%d%H%M%SZ").encode("ascii")
        return b"\x17" + _asn1_len(len(s)) + s

    validity = _asn1_seq(utc_time(now - dt.timedelta(minutes=5)) + utc_time(now + dt.timedelta(days=825)))
    serial = _asn1_int(int.from_bytes(os.urandom(8), "big"))
    # X.509 v1 is enough for local trust prompt (no extensions).
    tbs = _asn1_seq(
        serial
        + sig_alg
        + name
        + validity
        + name
        + spki
    )

    # Sign TBS with sha256WithRSAEncryption
    digest = hashlib.sha256(tbs).digest()
    # DigestInfo for sha256
    digest_info = _asn1_seq(
        _asn1_seq(_asn1_oid("2.16.840.1.101.3.4.2.1") + b"\x05\x00") + _asn1_octet(digest)
    )
    # PKCS#1 v1.5 pad
    k = (n.bit_length() + 7) // 8
    if len(digest_info) + 11 > k:
        raise RuntimeError("key too small")
    pad_len = k - len(digest_info) - 3
    em = b"\x00\x01" + (b"\xff" * pad_len) + b"\x00" + digest_info
    sig_int = pow(int.from_bytes(em, "big"), d, n)
    signature = sig_int.to_bytes(k, "big")

    cert = _asn1_seq(tbs + sig_alg + _asn1_bitstring(signature))

    KEY_FILE.write_bytes(_pem("RSA PRIVATE KEY", private_key).encode("ascii"))
    CERT_FILE.write_bytes(_pem("CERTIFICATE", cert).encode("ascii"))
    import ssl

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(CERT_FILE), str(KEY_FILE))
    print("Created and verified TLS certificate pair")


if __name__ == "__main__":
    import ssl

    if CERT_FILE.exists() and KEY_FILE.exists():
        try:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(str(CERT_FILE), str(KEY_FILE))
            print("Existing cert is valid")
        except Exception as exc:
            print(f"Existing cert invalid ({type(exc).__name__}), regenerating…")
            generate()
    else:
        generate()
