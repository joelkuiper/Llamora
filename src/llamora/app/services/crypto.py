from __future__ import annotations

from dataclasses import dataclass
from logging import getLogger

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

logger = getLogger(__name__)

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


class CryptoContext:
    """Opaque crypto capability for a single user's encryption context."""

    __slots__ = ("user_id", "epoch", "_dek", "_dropped")

    def __init__(self, *, user_id: str, dek: bytes, epoch: int) -> None:
        self.user_id = str(user_id)
        self.epoch = int(epoch)
        self._dek = bytearray(dek)
        self._dropped = False

    def fork(self) -> "CryptoContext":
        """Return a detached copy suitable for background tasks."""

        return CryptoContext(
            user_id=self.user_id,
            dek=self._require_key(),
            epoch=self.epoch,
        )

    def drop(self) -> None:
        """Best-effort zeroize key material."""

        if self._dropped:
            return
        for idx in range(len(self._dek)):
            self._dek[idx] = 0
        self._dropped = True

    def _require_key(self) -> bytes:
        if self._dropped:
            raise ValueError("crypto context has been dropped")
        return bytes(self._dek)

    def require_write(self, *, operation: str) -> None:
        """Validate that this context can be used for encryption writes."""

        _require_epoch(self.epoch, operation=operation)
        _ = self._require_key()

    def encrypt_entry(self, entry_id: str, plaintext: str):
        epoch = _require_epoch(self.epoch, operation="encrypt_entry")
        nonce = utils.random(24)
        aad = f"{self.user_id}|{entry_id}|{ALG.decode()}".encode("utf-8")
        ct = crypto_aead_xchacha20poly1305_ietf_encrypt(
            plaintext.encode("utf-8"), aad, nonce, self._require_key()
        )
        descriptor = CryptoDescriptor(algorithm=ALG.decode(), epoch=epoch)
        return nonce, ct, descriptor.encode_bytes()

    def decrypt_entry(
        self,
        entry_id: str,
        nonce: bytes,
        ct: bytes,
        alg: bytes,
    ) -> str:
        alg_name = CryptoDescriptor.parse(alg).algorithm
        aad = f"{self.user_id}|{entry_id}|{alg_name}".encode("utf-8")
        pt = crypto_aead_xchacha20poly1305_ietf_decrypt(
            ct, aad, nonce, self._require_key()
        )
        return pt.decode("utf-8")

    def entry_digest(self, entry_id: str, role: str, text: str) -> str:
        key = derive_entry_digest_key(self._require_key())
        payload = f"{entry_id}\0{role}\0{text}".encode("utf-8")
        return hmac.new(key, payload, hashlib.sha256).hexdigest()

    def encrypt_vector(self, entry_id: str, vector_id: str, vec: bytes):
        epoch = _require_epoch(self.epoch, operation="encrypt_vector")
        nonce = utils.random(24)
        aad = f"{self.user_id}|{entry_id}|{vector_id}|vector|{ALG.decode()}".encode(
            "utf-8"
        )
        ct = crypto_aead_xchacha20poly1305_ietf_encrypt(
            vec, aad, nonce, self._require_key()
        )
        descriptor = CryptoDescriptor(algorithm=ALG.decode(), epoch=epoch)
        return nonce, ct, descriptor.encode_bytes()

    def decrypt_vector(
        self,
        entry_id: str,
        vector_id: str,
        nonce: bytes,
        ct: bytes,
        alg: bytes,
    ) -> bytes:
        alg_name = CryptoDescriptor.parse(alg).algorithm
        aad = f"{self.user_id}|{entry_id}|{vector_id}|vector|{alg_name}".encode("utf-8")
        return crypto_aead_xchacha20poly1305_ietf_decrypt(
            ct, aad, nonce, self._require_key()
        )

    def encrypt_lockbox(self, namespace: str, key: str, plaintext: bytes) -> bytes:
        nonce = utils.random(24)
        aad = f"{self.user_id}:{namespace}:{key}".encode("utf-8")
        ciphertext = crypto_aead_xchacha20poly1305_ietf_encrypt(
            plaintext,
            aad,
            nonce,
            self._require_key(),
        )
        return nonce + ciphertext

    def decrypt_lockbox(self, namespace: str, key: str, packed: bytes) -> bytes:
        nonce_bytes = 24
        if len(packed) <= nonce_bytes:
            raise ValueError("failed to decrypt lockbox value")
        nonce = packed[:nonce_bytes]
        ciphertext = packed[nonce_bytes:]
        aad = f"{self.user_id}:{namespace}:{key}".encode("utf-8")
        return crypto_aead_xchacha20poly1305_ietf_decrypt(
            ciphertext,
            aad,
            nonce,
            self._require_key(),
        )


def _require_epoch(epoch: int | None, *, operation: str) -> int:
    try:
        value = int(epoch) if epoch is not None else 0
    except (TypeError, ValueError):
        value = 0
    if value <= 0:
        logger.warning("Encryption write missing epoch metadata for %s", operation)
        raise ValueError("missing encryption epoch metadata")
    return value


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


def encrypt_message(ctx: CryptoContext, entry_id: str, plaintext: str):
    return ctx.encrypt_entry(entry_id, plaintext)


def decrypt_message(
    ctx: CryptoContext,
    entry_id: str,
    nonce: bytes,
    ct: bytes,
    alg: bytes,
) -> str:
    return ctx.decrypt_entry(entry_id, nonce, ct, alg)


# ---------------------------------------------------------------------------
# Vector encryption / decryption
# ---------------------------------------------------------------------------


def encrypt_vector(
    ctx: CryptoContext,
    entry_id: str,
    vector_id: str,
    vec: bytes,
):
    """Encrypt a vector embedding for storage."""

    return ctx.encrypt_vector(entry_id, vector_id, vec)


def decrypt_vector(
    ctx: CryptoContext,
    entry_id: str,
    vector_id: str,
    nonce: bytes,
    ct: bytes,
    alg: bytes,
) -> bytes:
    """Decrypt an encrypted vector embedding."""

    return ctx.decrypt_vector(entry_id, vector_id, nonce, ct, alg)
