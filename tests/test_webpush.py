import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import webpush  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: E402
from cryptography.hazmat.primitives import hashes  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature  # noqa: E402


def decrypt(body, ua_key, auth):
    """Receiver side of RFC 8291, to check our sender."""
    salt, rs, idlen = body[:16], int.from_bytes(body[16:20], "big"), body[20]
    as_public = body[21:21 + idlen]
    ua_public = webpush._public_bytes(ua_key)
    shared = ua_key.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_public))
    ikm = webpush._hkdf(auth, shared, b"WebPush: info\x00" + ua_public + as_public, 32)
    cek = webpush._hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = webpush._hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    plain = AESGCM(cek).decrypt(nonce, body[21 + idlen:], None)
    assert rs == 4096 and plain.endswith(b"\x02")
    return plain[:-1]


class Tests(unittest.TestCase):
    def test_roundtrip(self):
        ua = ec.generate_private_key(ec.SECP256R1())
        auth = os.urandom(16)
        msg = json.dumps({"title": "IN STOCK", "url": "https://www.target.com/p/-/A-1"}).encode()
        body = webpush.encrypt(msg, webpush.b64u(webpush._public_bytes(ua)), webpush.b64u(auth))
        self.assertEqual(decrypt(body, ua, auth), msg)

    def test_rfc8291_vector(self):
        # Appendix A of RFC 8291.
        as_priv = int.from_bytes(webpush.unb64u("yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"), "big")
        server = ec.derive_private_key(as_priv, ec.SECP256R1())
        body = webpush.encrypt(b"When I grow up, I want to be a watermelon",
                               "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4",
                               "BTBZMqHH6r4Tts7J_aSIgg",
                               salt=webpush.unb64u("DGv6ra1nlYgDCS1FRnbzlw"), server_key=server)
        self.assertEqual(webpush.b64u(body),
                         "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN")

    def test_vapid_jwt_verifies(self):
        key = ec.generate_private_key(ec.SECP256R1())
        priv = webpush.b64u(key.private_numbers().private_value.to_bytes(32, "big"))
        h = webpush.vapid_headers("https://web.push.apple.com/abc", priv)["Authorization"]
        jwt = h.split("t=")[1].split(",")[0]
        head, claims, sig = jwt.split(".")
        self.assertEqual(json.loads(webpush.unb64u(claims))["aud"], "https://web.push.apple.com")
        raw = webpush.unb64u(sig)
        der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
        key.public_key().verify(der, f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))


if __name__ == "__main__":
    unittest.main()
