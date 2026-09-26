"""The encrypted image file format.

::

    magic    "LLIMG"                         5 bytes
    version  0x01                            1 byte
    header   secretstream header            24 bytes
    chunks   secretstream messages: CHUNK bytes of plaintext each (the last
             one may be shorter, even empty), plus ABYTES of overhead; the
             last one is tagged FINAL

Every file has its own key (see ``CryptoContext.wrap_image_key``). The first
chunk carries associated data naming the image and variant, so a file moved
to another image or variant fails to decrypt; secretstream itself rejects
reordered, dropped or altered chunks, and the FINAL tag makes truncation
detectable. Only bytes and keys here: no filesystem or database.
"""

from __future__ import annotations

from collections.abc import Iterator

from nacl.bindings import (
    crypto_secretstream_xchacha20poly1305_ABYTES as ABYTES,
    crypto_secretstream_xchacha20poly1305_HEADERBYTES as HEADERBYTES,
    crypto_secretstream_xchacha20poly1305_KEYBYTES as KEYBYTES,
    crypto_secretstream_xchacha20poly1305_TAG_FINAL as TAG_FINAL,
    crypto_secretstream_xchacha20poly1305_TAG_MESSAGE as TAG_MESSAGE,
    crypto_secretstream_xchacha20poly1305_init_pull,
    crypto_secretstream_xchacha20poly1305_init_push,
    crypto_secretstream_xchacha20poly1305_pull,
    crypto_secretstream_xchacha20poly1305_push,
    crypto_secretstream_xchacha20poly1305_state,
)

from llamora.app.services.images import ImageDecryptError

MAGIC = b"LLIMG"
VERSION = 1
CHUNK = 64 * 1024
PREFIX_BYTES = len(MAGIC) + 1 + HEADERBYTES
SEALED_CHUNK = CHUNK + ABYTES


def file_aad(user_id: str, image_id: str, variant: str) -> bytes:
    """Associated data binding a file to its owner, image and variant."""
    return f"{user_id}|{image_id}|{variant}".encode("utf-8")


def _require_key(key: bytes) -> None:
    if len(key) != KEYBYTES:
        raise ValueError(f"image file keys are {KEYBYTES} bytes")


def encrypt_chunks(key: bytes, aad: bytes, data: bytes) -> Iterator[bytes]:
    """Yield the encrypted file: the prefix, then one sealed chunk at a time."""
    _require_key(key)
    state = crypto_secretstream_xchacha20poly1305_state()
    header = crypto_secretstream_xchacha20poly1305_init_push(state, key)
    yield MAGIC + bytes([VERSION]) + header

    view = memoryview(data)
    starts = range(0, len(view), CHUNK) if len(view) else range(1)
    last = starts[-1]
    for start in starts:
        chunk = bytes(view[start : start + CHUNK])
        yield crypto_secretstream_xchacha20poly1305_push(
            state,
            chunk,
            ad=aad if start == 0 else None,
            tag=TAG_FINAL if start == last else TAG_MESSAGE,
        )


def encrypt(key: bytes, aad: bytes, data: bytes) -> bytes:
    return b"".join(encrypt_chunks(key, aad, data))


class Decryptor:
    """Incremental decryption of one file.

    Feed ciphertext in pieces of any size; each ``feed`` returns the
    plaintext chunks it could verify. Call ``finish`` once the input ends:
    it verifies the last chunk and that the file was complete. Any problem
    raises :class:`ImageDecryptError`, and plaintext is only ever returned
    for chunks that authenticated.
    """

    def __init__(self, key: bytes, aad: bytes) -> None:
        _require_key(key)
        self._key = key
        self._aad = aad
        self._state: object | None = None
        self._buffer = bytearray()
        self._chunks = 0
        self._done = False

    @property
    def done(self) -> bool:
        """Whether the FINAL chunk has been verified."""
        return self._done

    def feed(self, data: bytes) -> list[bytes]:
        if not data:
            return []
        if self._done:
            raise ImageDecryptError("data after the final chunk")
        self._buffer += data
        if self._state is None and not self._start():
            return []
        out: list[bytes] = []
        while len(self._buffer) >= SEALED_CHUNK:
            sealed = bytes(self._buffer[:SEALED_CHUNK])
            del self._buffer[:SEALED_CHUNK]
            out.append(self._pull(sealed))
            if self._done and self._buffer:
                raise ImageDecryptError("data after the final chunk")
        return out

    def finish(self) -> list[bytes]:
        if self._state is None:
            raise ImageDecryptError("file too short")
        out: list[bytes] = []
        if not self._done:
            sealed = bytes(self._buffer)
            self._buffer.clear()
            out.append(self._pull(sealed))
        if not self._done:
            raise ImageDecryptError("file is truncated")
        return out

    def _start(self) -> bool:
        if len(self._buffer) < PREFIX_BYTES:
            return False
        if bytes(self._buffer[: len(MAGIC)]) != MAGIC:
            raise ImageDecryptError("not an encrypted image file")
        version = self._buffer[len(MAGIC)]
        if version != VERSION:
            raise ImageDecryptError(f"unsupported image file version {version}")
        header = bytes(self._buffer[len(MAGIC) + 1 : PREFIX_BYTES])
        del self._buffer[:PREFIX_BYTES]
        state = crypto_secretstream_xchacha20poly1305_state()
        try:
            crypto_secretstream_xchacha20poly1305_init_pull(state, header, self._key)
        except Exception as exc:
            raise ImageDecryptError("invalid stream header") from exc
        self._state = state
        return True

    def _pull(self, sealed: bytes) -> bytes:
        if len(sealed) < ABYTES:
            raise ImageDecryptError("file is truncated")
        try:
            plaintext, tag = crypto_secretstream_xchacha20poly1305_pull(
                self._state,  # type: ignore[arg-type]
                sealed,
                ad=self._aad if self._chunks == 0 else None,
            )
        except Exception as exc:
            raise ImageDecryptError("chunk failed authentication") from exc
        if tag == TAG_FINAL:
            self._done = True
        elif tag != TAG_MESSAGE:
            raise ImageDecryptError(f"unexpected chunk tag {tag}")
        self._chunks += 1
        return plaintext


def decrypt(key: bytes, aad: bytes, blob: bytes) -> bytes:
    decryptor = Decryptor(key, aad)
    return b"".join([*decryptor.feed(blob), *decryptor.finish()])
