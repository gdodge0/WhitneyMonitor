from __future__ import annotations

"""aes_token.py – Confidential AES‑GCM utilities
=================================================
A single self‑contained module that provides:

1. **AESCipher** – ergonomic helpers for AES‑GCM encryption / decryption of
   arbitrary secrets, plus convenience helpers that return *web‑safe*,
   Base64‑URL‑encoded blobs for easy transport over HTTP queries, JSON, QR
   codes, etc.
2. **ConfidentialTokenService** – stateless opaque tokens whose JSON payload
   is encrypted (and authenticated) with AES‑GCM. The tokens are already
   Base64‑URL‑encoded and have embedded TTLs.
3. **TokenValidationError** – a dedicated exception type for token problems.

New in v1.1
-----------
* **Deterministic key derivation** – ``AESCipher.derive_key_from_secret()`` lets
  you turn a pass‑phrase (plus optional salt) into a 128/192/256‑bit key using
  PBKDF2‑HMAC‑SHA‑256. This makes it easy to accept a user‑supplied secret and
  reuse it across different instances of your application (as long as the
  same *salt* and *iterations* are used).

Run this file directly (``python aes_token.py``) to see a minimal demo.

Dependencies
------------
``cryptography`` ≥ 41.0 (``pip install cryptography``)
"""

import base64
import json
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, Tuple

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.backends import default_backend

__all__ = [
    "AESCipher",
    "ConfidentialTokenService",
    "TokenValidationError",
]

# ---------------------------------------------------------------------------
# Low‑level constants – keep these small & immutable
# ---------------------------------------------------------------------------
VERSION_BYTE: int = 1  # token format version
NONCE_SIZE: int = 12  # 96‑bit nonce recommended for AES‑GCM
TAG_SIZE: int = 16  # AES‑GCM appends 16‑byte tag
HEADER_SIZE: int = 1 + 1 + NONCE_SIZE  # V (1) + KID (1) + NONCE (12)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------
class TokenValidationError(Exception):
    """Raised when an opaque token is malformed, expired, or fails auth."""


# ---------------------------------------------------------------------------
# Symmetric AES‑GCM helper
# ---------------------------------------------------------------------------
class AESCipher:
    """Convenience wrapper around *cryptography*'s :class:`AESGCM`.

    Extras:
    * Key‑size enforcement.
    * Deterministic key derivation from a pass‑phrase (PBKDF2‑HMAC‑SHA‑256).
    * Built‑in web‑safe Base64 helpers (``encrypt_b64`` / ``decrypt_b64``).
    * Automatic conversion of ``str`` plaintext to UTF‑8 bytes.
    * Optional *AAD* (Additional Authenticated Data).
    """

    # ------------------------------------------------------------------
    # Construction / key helpers
    # ------------------------------------------------------------------
    def __init__(self, key: bytes):
        if len(key) not in (16, 24, 32):
            raise ValueError("Key must be 128, 192, or 256 bits long")
        self._key = key
        self._aead = AESGCM(key)

    @staticmethod
    def generate_key(length: int = 32) -> bytes:
        """Generate a cryptographically‑random key (default 256‑bit)."""
        if length not in (16, 24, 32):
            raise ValueError("length must be 16, 24, or 32 bytes")
        return os.urandom(length)

    @staticmethod
    def derive_key_from_secret(
            secret: str | bytes,
            *,
            length: int = 32,
            salt: bytes = b"aes_token_default_salt",
            iterations: int = 150_000,
    ) -> bytes:
        """Derive a deterministic AES key from *secret* using PBKDF2‑HMAC‑SHA‑256.

        Parameters
        ----------
        secret : str | bytes
            The pass‑phrase / secret value. ``str`` values are UTF‑8 encoded.
        length : int, optional
            Desired key length in **bytes** (16, 24, or 32). Default 32 (256‑bit).
        salt : bytes, optional
            Public salt value. **Must be identical** across all deployments that
            need to generate the same key. Defaults to a hard‑coded constant;
            supplying your own random 16‑byte salt is recommended.
        iterations : int, optional
            PBKDF2 iteration count. Higher = slower & stronger. Default 150k.
        """
        if length not in (16, 24, 32):
            raise ValueError("length must be 16, 24, or 32 bytes")
        if isinstance(secret, str):
            secret = secret.encode()
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=length,
            salt=salt,
            iterations=iterations,
            backend=default_backend(),
        )
        return kdf.derive(secret)

    # ------------------------------------------------------------------
    # Private helpers – Base64 encode/decode without padding
    # ------------------------------------------------------------------
    @staticmethod
    def _b64e(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    @staticmethod
    def _b64d(text: str) -> bytes:
        padded = text + "=" * (-len(text) % 4)
        return base64.urlsafe_b64decode(padded)

    # ------------------------------------------------------------------
    # Core encrypt / decrypt (binary in / binary out)
    # ------------------------------------------------------------------
    def encrypt(self, plaintext: bytes | str, *, aad: bytes | None = None) -> Tuple[bytes, bytes]:
        if isinstance(plaintext, str):
            plaintext = plaintext.encode()
        elif not isinstance(plaintext, (bytes, bytearray)):
            raise TypeError("plaintext must be bytes or str")
        nonce = os.urandom(NONCE_SIZE)
        ct = self._aead.encrypt(nonce, plaintext, aad)
        return nonce, ct

    def decrypt(self, nonce: bytes, ciphertext: bytes, *, aad: bytes | None = None) -> bytes:
        if len(nonce) != NONCE_SIZE:
            raise ValueError("nonce must be exactly 12 bytes for AES‑GCM")
        return self._aead.decrypt(nonce, ciphertext, aad)

    # ------------------------------------------------------------------
    # Web‑safe Base64 convenience wrappers
    # ------------------------------------------------------------------
    def encrypt_b64(self, plaintext: bytes | str, *, aad: bytes | None = None) -> str:
        nonce, ct = self.encrypt(plaintext, aad=aad)
        return self._b64e(nonce + ct)

    def decrypt_b64(self, token: str, *, aad: bytes | None = None) -> bytes:
        blob = self._b64d(token)
        if len(blob) < NONCE_SIZE + TAG_SIZE:
            raise ValueError("token too short to contain nonce and tag")
        nonce, ct = blob[:NONCE_SIZE], blob[NONCE_SIZE:]
        return self.decrypt(nonce, ct, aad=aad)


# ---------------------------------------------------------------------------
# Confidential Token Service (opaque stateless tokens)
# ---------------------------------------------------------------------------
@dataclass
class _KeyRecord:
    key: bytes
    created_at: float  # unix seconds


class ConfidentialTokenService:
    """Issue & verify encrypted JWT‑like tokens (opaque blob + Base64‑URL)."""

    def __init__(self):
        self._keys: Dict[int, _KeyRecord] = {}

    # --------------------------- key management ----------------------
    def add_key(self, kid: int, key: bytes):
        if not 0 <= kid <= 255:
            raise ValueError("kid must fit in one byte 0‑255")
        self._keys[kid] = _KeyRecord(key=key, created_at=time.time())

    def remove_key(self, kid: int):
        self._keys.pop(kid, None)

    # --------------------------- issue token -------------------------
    def issue_token(
            self,
            payload: Dict[str, Any],
            *,
            ttl_seconds: int,
            kid: int,
    ) -> str:
        if kid not in self._keys:
            raise ValueError(f"kid {kid} not registered")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")

        now = int(time.time())
        full_payload = {**payload, "iat": now, "exp": now + ttl_seconds}
        payload_bytes = json.dumps(full_payload, separators=(',', ':'), sort_keys=True).encode()

        cipher = AESCipher(self._keys[kid].key)
        nonce, ct = cipher.encrypt(payload_bytes)
        blob = bytes([VERSION_BYTE, kid]) + nonce + ct
        return AESCipher._b64e(blob)

    # --------------------------- verify token ------------------------
    def verify_token(self, token: str, *, leeway: int = 0) -> Dict[str, Any]:
        try:
            raw = AESCipher._b64d(token)
        except Exception as e:
            raise TokenValidationError("token not valid Base64‑URL") from e

        if len(raw) < HEADER_SIZE + TAG_SIZE:
            raise TokenValidationError("token too short")

        version, kid = raw[0], raw[1]
        if version != VERSION_BYTE:
            raise TokenValidationError(f"unsupported version {version}")
        if kid not in self._keys:
            raise TokenValidationError(f"unknown kid {kid}")

        nonce = raw[2: 2 + NONCE_SIZE]
        ct = raw[2 + NONCE_SIZE:]

        cipher = AESCipher(self._keys[kid].key)
        try:
            payload_bytes = cipher.decrypt(nonce, ct)
        except Exception as e:
            raise TokenValidationError("decryption failed or tag mismatch") from e
        try:
            claims = json.loads(payload_bytes)
        except json.JSONDecodeError as e:
            raise TokenValidationError("payload not valid JSON") from e

        now = int(time.time())
        exp = int(claims.get("exp", 0))
        if now > exp + leeway:
            raise TokenValidationError("token expired")
        return claims


# ---------------------------------------------------------------------------
# Self‑test / demo
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("[demo] --- Key derivation ---")
    secret_passphrase = "correct horse battery staple"
    key1 = AESCipher.derive_key_from_secret(secret_passphrase, length=32)
    key2 = AESCipher.derive_key_from_secret(secret_passphrase, length=32)
    print("Keys identical:", key1 == key2)

    print("\n[demo] --- General encryption with web‑safe blobs ---")
    cipher = AESCipher(key1)
    plaintext = "super secret message"
    blob = cipher.encrypt_b64(plaintext)
    print("encrypted:", blob)
    decrypted = cipher.decrypt_b64(blob).decode()
    print("decrypted:", decrypted)

    print("\n[demo] --- Confidential token ---")
    svc = ConfidentialTokenService()
    kid = 7
    svc.add_key(kid, key1)
    token = svc.issue_token({"sub": "alice", "scope": ["read"]}, ttl_seconds=60, kid=kid)
    print("token:", token)
    claims = svc.verify_token(token)
    print("claims:", claims)
