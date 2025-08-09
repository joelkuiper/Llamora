from nacl import pwhash, utils
from nacl.bindings import (
    crypto_aead_xchacha20poly1305_ietf_encrypt,
    crypto_aead_xchacha20poly1305_ietf_decrypt,
)

ALG = b"xchacha20poly1305_ietf"
OPSLIMIT = pwhash.argon2id.OPSLIMIT_MODERATE
MEMLIMIT = pwhash.argon2id.MEMLIMIT_MODERATE


def generate_dek() -> bytes:
    return utils.random(32)


def derive_key(secret: bytes, salt: bytes) -> bytes:
    return pwhash.argon2id.kdf(
        32, secret, salt, opslimit=OPSLIMIT, memlimit=MEMLIMIT
    )


def wrap_key(key: bytes, secret: str):
    salt = utils.random(pwhash.argon2id.SALTBYTES)
    k = derive_key(secret.encode("utf-8"), salt)
    nonce = utils.random(24)
    ct = crypto_aead_xchacha20poly1305_ietf_encrypt(key, None, nonce, k)
    return salt, nonce, ct


def unwrap_key(ct: bytes, salt: bytes, nonce: bytes, secret: str) -> bytes:
    k = derive_key(secret.encode("utf-8"), salt)
    return crypto_aead_xchacha20poly1305_ietf_decrypt(ct, None, nonce, k)


def encrypt_message(
    dek: bytes, user_id: str, session_id: str, msg_id: str, plaintext: str
):
    nonce = utils.random(24)
    aad = f"{user_id}|{session_id}|{msg_id}|{ALG.decode()}".encode("utf-8")
    ct = crypto_aead_xchacha20poly1305_ietf_encrypt(
        plaintext.encode("utf-8"), aad, nonce, dek
    )
    return nonce, ct, ALG


def decrypt_message(
    dek: bytes, user_id: str, session_id: str, msg_id: str, nonce: bytes, ct: bytes, alg: bytes
) -> str:
    aad = f"{user_id}|{session_id}|{msg_id}|{alg.decode()}".encode("utf-8")
    pt = crypto_aead_xchacha20poly1305_ietf_decrypt(ct, aad, nonce, dek)
    return pt.decode("utf-8")
