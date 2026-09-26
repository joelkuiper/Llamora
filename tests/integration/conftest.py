"""Storage integration tests: a real LocalDB (migrations included) and image
store in a temporary directory, no server."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from llamora.app.services.crypto import generate_dek
from llamora.app.services.images.blob_store import BlobStore
from llamora.app.services.images.service import ImageConfig, ImageService
from llamora.persistence.local_db import LocalDB
from storage import SIZES, MakePerson, Person


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[LocalDB]:
    database = LocalDB(str(tmp_path / "state.sqlite3"))
    await database.init()
    try:
        yield database
    finally:
        await database.close()


@pytest.fixture
def image_config(tmp_path: Path) -> ImageConfig:
    return ImageConfig(
        root=tmp_path / "images",
        max_per_entry=3,
        pending_ttl=3600,
        sizes=SIZES,
    )


@pytest.fixture
def images(db: LocalDB, image_config: ImageConfig) -> ImageService:
    return ImageService(db, BlobStore(image_config.root), image_config)


@pytest.fixture
def make_person(db: LocalDB) -> MakePerson:
    async def make(name: str) -> Person:
        blob = os.urandom(16)
        user_id = await db.users.create_user(
            name, "hash", blob, blob, blob, blob, blob, blob
        )
        return Person(id=user_id, dek=generate_dek())

    return make


@pytest.fixture
async def alice(make_person: MakePerson) -> Person:
    return await make_person("alice")


@pytest.fixture
async def bob(make_person: MakePerson) -> Person:
    return await make_person("bob")
