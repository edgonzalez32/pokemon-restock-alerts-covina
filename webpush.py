"""Minimal Web Push sender (RFC 8291 aes128gcm + RFC 8292 VAPID).

Sends notifications to the installed Restock Radar app on iPhone (or any
browser that subscribed). Only depends on `cryptography`.
"""

from __future__ import annotations

import base64
import json
import os
import struct
import time
import urllib.error
import urllib.parse
import urllib.request

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hmac import HMAC

VAPID_SUBJECT = "https://github.com/edgonzalez32/pokemon-restock-alerts-covina"


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64u(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _hmac(key: bytes, data: bytes) -> bytes:
    h = HMAC(key, hashes.SHA256())
    h.update(data)
    return h.finalize()


def _hkdf(salt: bytes, ikm: bytes, info: bytes, length: int) -> bytes:
    prk = _hmac(salt, ikm)
    return _hmac(prk, info + b"\x01")[:length]


def _public_bytes(key) -> bytes:
    return key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def encrypt(payload: bytes, p256dh: str, auth: str, salt: bytes | None = None,
            server_key=None) -> bytes:
    """Encrypt a payload for one subscription (aes128gcm, single record)."""
    ua_public = unb64u(p256dh)
    auth_secret = unb64u(auth)
    server_key = server_key or ec.generate_private_key(ec.SECP256R1())
    as_public = _public_bytes(server_key)
    ua_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    shared = server_key.exchange(ec.ECDH(), ua_key)
    ikm = _hkdf(auth_secret, shared, b"WebPush: info\x00" + ua_public + as_public, 32)
    salt = salt or os.urandom(16)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    body = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)
    header = salt + struct.pack("!IB", 4096, len(as_public)) + as_public
    return header + body


def vapid_headers(endpoint: str, private_key_b64: str, subject: str = VAPID_SUBJECT) -> dict:
    value = int.from_bytes(unb64u(private_key_b64), "big")
    key = ec.derive_private_key(value, ec.SECP256R1())
    url = urllib.parse.urlparse(endpoint)
    claims = {"aud": f"{url.scheme}://{url.netloc}", "exp": int(time.time()) + 12 * 3600, "sub": subject}
    signing_input = (b64u(json.dumps({"typ": "JWT", "alg": "ES256"}).encode()) + "." +
                     b64u(json.dumps(claims, separators=(",", ":")).encode()))
    r, s = decode_dss_signature(key.sign(signing_input.encode(), ec.ECDSA(hashes.SHA256())))
    jwt = signing_input + "." + b64u(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    return {"Authorization": f"vapid t={jwt}, k={b64u(_public_bytes(key))}"}


def send(subscription: dict, message: dict, private_key_b64: str, ttl: int = 3600) -> int:
    """Send one notification. Returns the push service's HTTP status."""
    body = encrypt(json.dumps(message).encode(), subscription["keys"]["p256dh"],
                   subscription["keys"]["auth"])
    headers = {"Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream",
               "TTL": str(ttl), "Urgency": "high",
               **vapid_headers(subscription["endpoint"], private_key_b64)}
    req = urllib.request.Request(subscription["endpoint"], data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code
