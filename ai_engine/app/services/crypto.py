"""AES-256-CBC encrypt/decrypt — wire-compatible with frontend/src/lib/crypto.js.

Both ai_engine and the frontend must produce/consume the same ciphertext format
so encrypted rows in `supabase_projects` (anon_key_enc, service_key_enc,
db_password_enc + their iv columns) are readable from either service.

Format (matches Node's crypto.createCipheriv exactly):
  - Algorithm: AES-256-CBC, PKCS7 padding (the default for Node's CBC)
  - Key:       SHA-256(ENCRYPTION_KEY env var) — gives 32 bytes deterministically
  - IV:        16 random bytes per message
  - Output:    {"encrypted": <hex>, "iv": <hex>}

Pure stdlib + cryptography. No Supabase / network / async.
"""
from __future__ import annotations

import hashlib
import os
import secrets
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

from app.config import settings

_BLOCK_BYTES = 16  # AES block size


@dataclass(frozen=True)
class Encrypted:
    """Hex-encoded ciphertext + IV. Both fields must be persisted; the IV
    is needed for decryption and is not secret."""
    encrypted: str
    iv: str


def _key() -> bytes:
    """Derive the 32-byte AES key. Frontend uses
    crypto.createHash('sha256').update(secret).digest() — same primitive."""
    secret = settings.ENCRYPTION_KEY or os.environ.get("ENCRYPTION_KEY", "")
    if not secret:
        raise RuntimeError(
            "ENCRYPTION_KEY is not set. ai_engine and the frontend must use "
            "the SAME value so encrypted rows are portable between them."
        )
    return hashlib.sha256(secret.encode("utf-8")).digest()


def encrypt(plaintext: str) -> Encrypted:
    """Encrypt a UTF-8 string. Returns hex-encoded ciphertext + IV."""
    if not isinstance(plaintext, str):
        raise TypeError(f"encrypt() expects str, got {type(plaintext).__name__}")
    iv = secrets.token_bytes(_BLOCK_BYTES)
    cipher = Cipher(algorithms.AES(_key()), modes.CBC(iv)).encryptor()
    padder = PKCS7(_BLOCK_BYTES * 8).padder()
    padded = padder.update(plaintext.encode("utf-8")) + padder.finalize()
    ct = cipher.update(padded) + cipher.finalize()
    return Encrypted(encrypted=ct.hex(), iv=iv.hex())


def decrypt(encrypted_hex: str, iv_hex: str) -> str:
    """Reverse encrypt(). Raises ValueError on malformed inputs."""
    try:
        iv = bytes.fromhex(iv_hex)
        ct = bytes.fromhex(encrypted_hex)
    except ValueError as exc:
        raise ValueError(f"crypto: invalid hex input ({exc})") from exc
    if len(iv) != _BLOCK_BYTES:
        raise ValueError(f"crypto: IV must be {_BLOCK_BYTES} bytes, got {len(iv)}")
    cipher = Cipher(algorithms.AES(_key()), modes.CBC(iv)).decryptor()
    padded = cipher.update(ct) + cipher.finalize()
    unpadder = PKCS7(_BLOCK_BYTES * 8).unpadder()
    plain = unpadder.update(padded) + unpadder.finalize()
    return plain.decode("utf-8")
