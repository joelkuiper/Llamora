"""Image file keys and metadata under the user's DEK (CryptoContext)."""

from __future__ import annotations

import os

import pytest
from nacl.exceptions import CryptoError

from llamora.app.services.crypto import CryptoContext, generate_dek

DEK = generate_dek()
FILE_KEY = os.urandom(32)


def ctx(user_id: str = "user-1", *, dek: bytes = DEK, epoch: int = 1):
    return CryptoContext(user_id=user_id, dek=dek, epoch=epoch)


def test_file_key_round_trip() -> None:
    nonce, ct, alg = ctx().wrap_image_key("01IMAGE", FILE_KEY)
    assert FILE_KEY not in ct
    assert ctx().unwrap_image_key("01IMAGE", nonce, ct, alg) == FILE_KEY


@pytest.mark.parametrize(
    ("epoch", "alg"),
    [(1, b"xchacha20poly1305_ietf"), (3, b"xchacha20poly1305_ietf;e=3")],
)
def test_alg_records_the_epoch(epoch: int, alg: bytes) -> None:
    context = ctx(epoch=epoch)
    nonce, ct, recorded = context.wrap_image_key("01IMAGE", FILE_KEY)
    assert recorded == alg
    assert context.unwrap_image_key("01IMAGE", nonce, ct, recorded) == FILE_KEY


@pytest.mark.parametrize(
    ("reader", "image_id"),
    [
        (ctx(), "01OTHER"),  # another image of the same user
        (ctx("user-2"), "01IMAGE"),  # another user with the same key material
        (ctx(dek=generate_dek()), "01IMAGE"),  # another DEK
    ],
    ids=["image", "user", "dek"],
)
def test_file_key_is_bound_to_owner_and_image(reader, image_id: str) -> None:
    nonce, ct, alg = ctx().wrap_image_key("01IMAGE", FILE_KEY)
    with pytest.raises(CryptoError):
        reader.unwrap_image_key(image_id, nonce, ct, alg)


def test_meta_round_trip() -> None:
    meta = b'{"filename": "IMG_0412.jpg"}'
    _, _, alg = ctx().wrap_image_key("01IMAGE", FILE_KEY)
    nonce, ct = ctx().encrypt_image_meta("01IMAGE", meta)
    assert b"IMG_0412" not in ct
    assert ctx().decrypt_image_meta("01IMAGE", nonce, ct, alg) == meta


def test_meta_is_bound_to_owner_and_image() -> None:
    _, _, alg = ctx().wrap_image_key("01IMAGE", FILE_KEY)
    nonce, ct = ctx().encrypt_image_meta("01IMAGE", b"{}")
    with pytest.raises(CryptoError):
        ctx().decrypt_image_meta("01OTHER", nonce, ct, alg)
    with pytest.raises(CryptoError):
        ctx("user-2").decrypt_image_meta("01IMAGE", nonce, ct, alg)


def test_key_and_meta_ciphertexts_are_not_interchangeable() -> None:
    nonce, ct, alg = ctx().wrap_image_key("01IMAGE", FILE_KEY)
    with pytest.raises(CryptoError):
        ctx().decrypt_image_meta("01IMAGE", nonce, ct, alg)
    meta_nonce, meta_ct = ctx().encrypt_image_meta("01IMAGE", os.urandom(32))
    with pytest.raises(CryptoError):
        ctx().unwrap_image_key("01IMAGE", meta_nonce, meta_ct, alg)


def test_writes_need_an_epoch() -> None:
    with pytest.raises(ValueError, match="epoch"):
        ctx(epoch=0).wrap_image_key("01IMAGE", FILE_KEY)
    with pytest.raises(ValueError, match="epoch"):
        ctx(epoch=0).encrypt_image_meta("01IMAGE", b"{}")


def test_file_keys_must_be_32_bytes() -> None:
    with pytest.raises(ValueError):
        ctx().wrap_image_key("01IMAGE", b"short")


def test_dropped_context_cannot_unwrap() -> None:
    context = ctx()
    nonce, ct, alg = context.wrap_image_key("01IMAGE", FILE_KEY)
    context.drop()
    with pytest.raises(ValueError, match="dropped"):
        context.unwrap_image_key("01IMAGE", nonce, ct, alg)
