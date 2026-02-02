from nacl import pwhash, utils
from nacl.bindings import (
    crypto_aead_xchacha20poly1305_ietf_encrypt,
    crypto_aead_xchacha20poly1305_ietf_decrypt,
)
import secrets

ALG = b"xchacha20poly1305_ietf"
OPSLIMIT = pwhash.argon2id.OPSLIMIT_MODERATE
MEMLIMIT = pwhash.argon2id.MEMLIMIT_MODERATE


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


def derive_key(secret: bytes, salt: bytes) -> bytes:
    return pwhash.argon2id.kdf(32, secret, salt, opslimit=OPSLIMIT, memlimit=MEMLIMIT)


def wrap_key(key: bytes, secret: str):
    salt = utils.random(pwhash.argon2id.SALTBYTES)
    k = derive_key(secret.encode("utf-8"), salt)
    nonce = utils.random(24)
    ct = crypto_aead_xchacha20poly1305_ietf_encrypt(key, None, nonce, k)
    return salt, nonce, ct


def unwrap_key(ct: bytes, salt: bytes, nonce: bytes, secret: str) -> bytes:
    k = derive_key(secret.encode("utf-8"), salt)
    return crypto_aead_xchacha20poly1305_ietf_decrypt(ct, None, nonce, k)


def encrypt_message(dek: bytes, user_id: str, entry_id: str, plaintext: str):
    nonce = utils.random(24)
    aad = f"{user_id}|{entry_id}|{ALG.decode()}".encode("utf-8")
    ct = crypto_aead_xchacha20poly1305_ietf_encrypt(
        plaintext.encode("utf-8"), aad, nonce, dek
    )
    return nonce, ct, ALG


def decrypt_message(
    dek: bytes,
    user_id: str,
    entry_id: str,
    nonce: bytes,
    ct: bytes,
    alg: bytes,
) -> str:
    aad = f"{user_id}|{entry_id}|{alg.decode()}".encode("utf-8")
    pt = crypto_aead_xchacha20poly1305_ietf_decrypt(ct, aad, nonce, dek)
    return pt.decode("utf-8")


def encrypt_vector(dek: bytes, user_id: str, entry_id: str, vec: bytes):
    """Encrypt a vector embedding for storage.

    The vector is provided as raw bytes and is encrypted using the same AEAD
    algorithm as messages. Associated data ties the vector to the owning user
    and message identifier.
    """

    nonce = utils.random(24)
    aad = f"{user_id}|{entry_id}|vector|{ALG.decode()}".encode("utf-8")
    ct = crypto_aead_xchacha20poly1305_ietf_encrypt(vec, aad, nonce, dek)
    return nonce, ct, ALG


def decrypt_vector(
    dek: bytes,
    user_id: str,
    entry_id: str,
    nonce: bytes,
    ct: bytes,
    alg: bytes,
) -> bytes:
    """Decrypt an encrypted vector embedding."""

    aad = f"{user_id}|{entry_id}|vector|{alg.decode()}".encode("utf-8")
    return crypto_aead_xchacha20poly1305_ietf_decrypt(ct, aad, nonce, dek)
