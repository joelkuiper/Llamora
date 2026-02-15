from __future__ import annotations

import contextvars
from dataclasses import dataclass

from nacl import pwhash, utils
import hashlib
import hmac
from nacl.bindings import (
    crypto_aead_xchacha20poly1305_ietf_encrypt,
    crypto_aead_xchacha20poly1305_ietf_decrypt,
)
import secrets

ALG = b"xchacha20poly1305_ietf"
OPSLIMIT = pwhash.argon2id.OPSLIMIT_MODERATE
MEMLIMIT = pwhash.argon2id.MEMLIMIT_MODERATE
ENTRY_DIGEST_CONTEXT = b"llamora:entry-digest:v2"

CURRENT_SUITE = "xchacha20poly1305_ietf/argon2id_moderate/hmac_sha256_v2"

# ---------------------------------------------------------------------------
# Crypto epoch context variable
# ---------------------------------------------------------------------------

_crypto_epoch: contextvars.ContextVar[int] = contextvars.ContextVar(
    "crypto_epoch", default=1
)


def get_crypto_epoch() -> int:
    return _crypto_epoch.get()


def set_crypto_epoch(epoch: int) -> contextvars.Token:
    return _crypto_epoch.set(epoch)


# ---------------------------------------------------------------------------
# CryptoDescriptor — structured metadata stored in the `alg` column
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CryptoDescriptor:
    """Structured crypto metadata stored in the ``alg`` column.

    Format: ``<algorithm>[;e=<epoch>]``

    Old values (bare algorithm strings) parse as epoch 1, and epoch-1
    descriptors encode back to the bare algorithm for backward compatibility.
    """

    algorithm: str
    epoch: int = 1

    @classmethod
    def parse(cls, raw: str | bytes) -> CryptoDescriptor:
        s = raw.decode() if isinstance(raw, bytes) else raw
        parts = s.split(";")
        algorithm = parts[0]
        epoch = 1
        for part in parts[1:]:
            if part.startswith("e="):
                epoch = int(part[2:])
        return cls(algorithm=algorithm, epoch=epoch)

    def encode(self) -> str:
        if self.epoch <= 1:
            return self.algorithm
        return f"{self.algorithm};e={self.epoch}"

    def encode_bytes(self) -> bytes:
        return self.encode().encode("utf-8")

    @property
    def algorithm_bytes(self) -> bytes:
        return self.algorithm.encode("utf-8")


# ---------------------------------------------------------------------------
# Key generation & recovery codes
# ---------------------------------------------------------------------------


def generate_dek() -> bytes:
    return utils.random(32)


ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def generate_recovery_code(length: int = 16) -> str:
    """Generate a high-entropy recovery code using Crockford Base32.

    The returned string contains only unambiguous characters. Hyphens are not
    included so the value can be safely stored; use :func:`format_recovery_code`
    for display purposes.
    """

    bits = length * 5
    rand = secrets.randbits(bits)
    chars = [ALPHABET[(rand >> (5 * i)) & 31] for i in range(length)]
    return "".join(reversed(chars))


def format_recovery_code(code: str, group: int = 4) -> str:
    """Format a recovery code into groups for readability."""

    return "-".join(code[i : i + group] for i in range(0, len(code), group))


# ---------------------------------------------------------------------------
# Key derivation & wrapping
# ---------------------------------------------------------------------------


def derive_key(secret: bytes, salt: bytes) -> bytes:
    return pwhash.argon2id.kdf(32, secret, salt, opslimit=OPSLIMIT, memlimit=MEMLIMIT)


def derive_entry_digest_key(dek: bytes) -> bytes:
    return hmac.new(ENTRY_DIGEST_CONTEXT, dek, hashlib.sha256).digest()


def entry_digest(dek: bytes, entry_id: str, role: str, text: str) -> str:
    key = derive_entry_digest_key(dek)
    payload = f"{entry_id}\0{role}\0{text}".encode("utf-8")
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


def wrap_key(key: bytes, secret: str):
    salt = utils.random(pwhash.argon2id.SALTBYTES)
    k = derive_key(secret.encode("utf-8"), salt)
    nonce = utils.random(24)
    ct = crypto_aead_xchacha20poly1305_ietf_encrypt(key, None, nonce, k)
    return salt, nonce, ct


def unwrap_key(ct: bytes, salt: bytes, nonce: bytes, secret: str) -> bytes:
    k = derive_key(secret.encode("utf-8"), salt)
    return crypto_aead_xchacha20poly1305_ietf_decrypt(ct, None, nonce, k)


# ---------------------------------------------------------------------------
# Message encryption / decryption
# ---------------------------------------------------------------------------


def encrypt_message(
    dek: bytes, user_id: str, entry_id: str, plaintext: str, *, epoch: int = 0
):
    if epoch <= 0:
        epoch = get_crypto_epoch()
    nonce = utils.random(24)
    aad = f"{user_id}|{entry_id}|{ALG.decode()}".encode("utf-8")
    ct = crypto_aead_xchacha20poly1305_ietf_encrypt(
        plaintext.encode("utf-8"), aad, nonce, dek
    )
    descriptor = CryptoDescriptor(algorithm=ALG.decode(), epoch=epoch)
    return nonce, ct, descriptor.encode_bytes()


def decrypt_message(
    dek: bytes,
    user_id: str,
    entry_id: str,
    nonce: bytes,
    ct: bytes,
    alg: bytes,
) -> str:
    alg_name = CryptoDescriptor.parse(alg).algorithm
    aad = f"{user_id}|{entry_id}|{alg_name}".encode("utf-8")
    pt = crypto_aead_xchacha20poly1305_ietf_decrypt(ct, aad, nonce, dek)
    return pt.decode("utf-8")


# ---------------------------------------------------------------------------
# Vector encryption / decryption
# ---------------------------------------------------------------------------


def encrypt_vector(
    dek: bytes,
    user_id: str,
    entry_id: str,
    vector_id: str,
    vec: bytes,
    *,
    epoch: int = 0,
):
    """Encrypt a vector embedding for storage.

    The vector is provided as raw bytes and is encrypted using the same AEAD
    algorithm as messages. Associated data ties the vector to the owning user
    and message identifier.
    """

    if epoch <= 0:
        epoch = get_crypto_epoch()
    nonce = utils.random(24)
    aad = f"{user_id}|{entry_id}|{vector_id}|vector|{ALG.decode()}".encode("utf-8")
    ct = crypto_aead_xchacha20poly1305_ietf_encrypt(vec, aad, nonce, dek)
    descriptor = CryptoDescriptor(algorithm=ALG.decode(), epoch=epoch)
    return nonce, ct, descriptor.encode_bytes()


def decrypt_vector(
    dek: bytes,
    user_id: str,
    entry_id: str,
    vector_id: str,
    nonce: bytes,
    ct: bytes,
    alg: bytes,
) -> bytes:
    """Decrypt an encrypted vector embedding."""

    alg_name = CryptoDescriptor.parse(alg).algorithm
    aad = f"{user_id}|{entry_id}|{vector_id}|vector|{alg_name}".encode("utf-8")
    return crypto_aead_xchacha20poly1305_ietf_decrypt(ct, aad, nonce, dek)
