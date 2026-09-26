"""Uploaded files arrive byte-exact, however the request body is chunked.

Werkzeug before 3.1.8 (pallets/werkzeug#3088) prepended "\\r\\n" to a file
when a network chunk ended right after the part's headers, which turned
valid image uploads into "not an image". Quart uses this decoder.
"""

from __future__ import annotations

import pytest
from werkzeug.sansio.multipart import Data, File, MultipartDecoder, NeedData

BOUNDARY = b"----llamora-test-boundary"
PAYLOAD = b"\xff\xd8\xff\xe0\x00\x10JFIF" + bytes(range(256)) * 8


def body() -> bytes:
    return (
        b"--" + BOUNDARY + b"\r\n"
        b'Content-Disposition: form-data; name="image"; filename="p.jpg"\r\n'
        b"Content-Type: image/jpeg\r\n"
        b"\r\n" + PAYLOAD + b"\r\n"
        b"--" + BOUNDARY + b"--\r\n"
    )


def decode(chunks: list[bytes]) -> bytes:
    decoder = MultipartDecoder(BOUNDARY)
    received = bytearray()
    in_file = False
    for chunk in [*chunks, None]:
        decoder.receive_data(chunk)
        while True:
            event = decoder.next_event()
            if isinstance(event, NeedData):
                break
            if isinstance(event, File):
                in_file = True
            elif isinstance(event, Data) and in_file:
                received += event.data
                if not event.more_data:
                    in_file = False
            if decoder.state.name == "COMPLETE":
                break
    return bytes(received)


def split_points() -> list[int]:
    raw = body()
    headers_end = raw.index(b"\r\n\r\n") + 4
    # Around the blank line that ends the headers, and a few elsewhere.
    return sorted({*range(headers_end - 3, headers_end + 3), 10, len(raw) - 10})


@pytest.mark.parametrize("split", split_points())
def test_file_bytes_survive_any_chunk_split(split: int) -> None:
    raw = body()
    assert decode([raw[:split], raw[split:]]) == PAYLOAD


def test_file_bytes_survive_tiny_chunks() -> None:
    raw = body()
    assert decode([raw[i : i + 7] for i in range(0, len(raw), 7)]) == PAYLOAD
