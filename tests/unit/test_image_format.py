"""The encrypted image file format (llamora.app.services.images.format)."""

from __future__ import annotations

import os

import pytest

from llamora.app.services.images import ImageDecryptError
from llamora.app.services.images.format import (
    ABYTES,
    CHUNK,
    MAGIC,
    PREFIX_BYTES,
    SEALED_CHUNK,
    Decryptor,
    decrypt,
    encrypt,
    file_aad,
)

KEY = bytes(range(32))
AAD = file_aad("user-1", "01IMAGE", "display")


def sealed(size: int) -> bytes:
    return encrypt(KEY, AAD, os.urandom(size))


@pytest.mark.parametrize(
    "size", [0, 1, CHUNK - 1, CHUNK, CHUNK + 1, 3 * CHUNK, 3 * CHUNK + 5]
)
def test_round_trip(size: int) -> None:
    data = os.urandom(size)
    blob = encrypt(KEY, AAD, data)
    assert blob.startswith(MAGIC)
    assert decrypt(KEY, AAD, blob) == data


def test_ciphertext_hides_the_plaintext() -> None:
    data = b"\xff\xd8\xff\xe0 JFIF plaintext marker " * 100
    blob = encrypt(KEY, AAD, data)
    assert b"JFIF" not in blob and b"plaintext marker" not in blob


def test_every_file_is_different() -> None:
    data = os.urandom(1000)
    assert encrypt(KEY, AAD, data) != encrypt(KEY, AAD, data)


@pytest.mark.parametrize("piece", [1, 7, 1000, SEALED_CHUNK, SEALED_CHUNK + 3])
def test_streaming_in_any_piece_size(piece: int) -> None:
    data = os.urandom(2 * CHUNK + 123)
    blob = encrypt(KEY, AAD, data)
    decryptor = Decryptor(KEY, AAD)
    out: list[bytes] = []
    for start in range(0, len(blob), piece):
        out.extend(decryptor.feed(blob[start : start + piece]))
    out.extend(decryptor.finish())
    assert b"".join(out) == data


def test_first_chunk_is_verified_before_the_rest_arrives() -> None:
    # Lets a response verify a file before sending any headers.
    data = os.urandom(3 * CHUNK)
    blob = encrypt(KEY, AAD, data)
    decryptor = Decryptor(KEY, AAD)
    first = decryptor.feed(blob[: PREFIX_BYTES + SEALED_CHUNK])
    assert first == [data[:CHUNK]]
    assert not decryptor.done


def test_tampered_first_chunk_is_rejected_on_the_first_feed() -> None:
    blob = bytearray(sealed(3 * CHUNK))
    blob[PREFIX_BYTES + 10] ^= 0x01
    with pytest.raises(ImageDecryptError):
        Decryptor(KEY, AAD).feed(bytes(blob[: PREFIX_BYTES + SEALED_CHUNK]))


@pytest.mark.parametrize(
    "offset",
    [
        0,  # magic
        len(MAGIC) + 1,  # stream header
        PREFIX_BYTES,  # first chunk
        PREFIX_BYTES + SEALED_CHUNK + 5,  # a middle chunk
        -1,  # the last chunk
    ],
)
def test_any_flipped_byte_is_rejected(offset: int) -> None:
    blob = bytearray(sealed(2 * CHUNK + 10))
    blob[offset] ^= 0x01
    with pytest.raises(ImageDecryptError):
        decrypt(KEY, AAD, bytes(blob))


def test_unknown_version_is_rejected() -> None:
    blob = bytearray(sealed(10))
    blob[len(MAGIC)] = 2
    with pytest.raises(ImageDecryptError, match="version"):
        decrypt(KEY, AAD, bytes(blob))


@pytest.mark.parametrize(
    "cut",
    [
        lambda blob: blob[: -(10 + ABYTES)],  # drop the (10-byte) last chunk
        lambda blob: blob[:-1],  # partial last chunk
        lambda blob: blob[: PREFIX_BYTES + SEALED_CHUNK],  # at a chunk boundary
        lambda blob: blob[:PREFIX_BYTES],  # header only
        lambda blob: blob[: PREFIX_BYTES - 1],  # not even a header
        lambda blob: b"",
    ],
    ids=[
        "no-last-chunk",
        "partial-chunk",
        "chunk-boundary",
        "header-only",
        "short-header",
        "empty",
    ],
)
def test_truncation_is_detected(cut) -> None:
    blob = sealed(2 * CHUNK + 10)
    with pytest.raises(ImageDecryptError):
        decrypt(KEY, AAD, cut(blob))


def test_truncation_after_a_full_final_chunk_boundary() -> None:
    # A file of exactly two chunks cut after the first: every piece left
    # authenticates, only the missing FINAL tag shows the cut.
    blob = sealed(2 * CHUNK)
    with pytest.raises(ImageDecryptError, match="truncated"):
        decrypt(KEY, AAD, blob[: PREFIX_BYTES + SEALED_CHUNK])


@pytest.mark.parametrize("extra", [b"x", os.urandom(SEALED_CHUNK)])
def test_trailing_bytes_after_the_final_chunk_are_rejected(extra: bytes) -> None:
    with pytest.raises(ImageDecryptError):
        decrypt(KEY, AAD, sealed(CHUNK) + extra)
    with pytest.raises(ImageDecryptError):
        decrypt(KEY, AAD, sealed(10) + extra)


@pytest.mark.parametrize(
    "other",
    [
        file_aad("user-1", "01OTHER", "display"),  # moved to another image
        file_aad("user-1", "01IMAGE", "thumb"),  # moved to another variant
        file_aad("user-2", "01IMAGE", "display"),  # moved to another user
    ],
    ids=["image", "variant", "user"],
)
def test_a_file_cannot_be_moved(other: bytes) -> None:
    with pytest.raises(ImageDecryptError):
        decrypt(KEY, other, sealed(100))


def test_wrong_key_is_rejected() -> None:
    with pytest.raises(ImageDecryptError):
        decrypt(bytes(32), AAD, sealed(100))


def test_keys_must_be_32_bytes() -> None:
    with pytest.raises(ValueError):
        encrypt(b"short", AAD, b"data")
