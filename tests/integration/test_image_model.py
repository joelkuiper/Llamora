"""Stored images prepared for a vision model (ImageService.model_image_uri)."""

from __future__ import annotations

import base64

import pytest

from imaging import decode, encode, halves, jpeg_with_gps
from llamora.app.services.images.service import ImageService
from storage import Person

pytestmark = pytest.mark.anyio


def decode_uri(uri: str):
    prefix = "data:image/jpeg;base64,"
    assert uri.startswith(prefix)
    return decode(base64.b64decode(uri[len(prefix) :]))


async def test_an_image_as_the_model_receives_it(
    images: ImageService, alice: Person
) -> None:
    # The service's display size here is 128 px (see conftest SIZES).
    record = await images.upload(
        alice.ctx(), "p.png", encode(halves((400, 200)), "PNG")
    )

    uri = await images.model_image_uri(alice.ctx(), record.id, max_edge=64)

    assert uri is not None
    image = decode_uri(uri)
    assert image.format == "JPEG" and image.size == (64, 32)


async def test_nothing_personal_survives(images: ImageService, alice: Person) -> None:
    record = await images.upload(alice.ctx(), "gps.jpg", jpeg_with_gps())
    uri = await images.model_image_uri(alice.ctx(), record.id, max_edge=1024)
    assert uri is not None
    image = decode_uri(uri)
    assert not image.getexif() and "icc_profile" not in image.info


async def test_other_users_and_unknown_images_give_nothing(
    images: ImageService, alice: Person, bob: Person
) -> None:
    record = await images.upload(alice.ctx(), "p.png", encode(halves((40, 40)), "PNG"))
    assert await images.model_image_uri(bob.ctx(), record.id, max_edge=64) is None
    assert await images.model_image_uri(alice.ctx(), "01UNKNOWN", max_edge=64) is None


async def test_a_damaged_file_gives_nothing(
    images: ImageService, alice: Person
) -> None:
    record = await images.upload(alice.ctx(), "p.png", encode(halves((40, 40)), "PNG"))
    path = images.store.path_for(alice.id, record.id, "display")
    blob = bytearray(path.read_bytes())
    blob[40] ^= 1
    path.write_bytes(bytes(blob))

    assert await images.model_image_uri(alice.ctx(), record.id, max_edge=64) is None
