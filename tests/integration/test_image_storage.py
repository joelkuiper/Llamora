"""Images at rest: rows, files, linking to entries, cleanup and key rotation.

A real LocalDB (with migrations) and image store in a temp directory.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastmigrate import create_db, get_db_version, run_migrations
from nacl.exceptions import CryptoError

from imaging import decode, encode, halves, jpeg_with_gps, noise
from llamora.app.db.images import ImageAttachError
from llamora.app.services.crypto import generate_dek
from llamora.app.services.images import VARIANTS, ImageDecryptError, ImageRejected
from llamora.app.services.images.blob_store import BlobStore, user_dir_name
from llamora.app.services.images.format import CHUNK
from llamora.app.services.images.service import (
    TOO_MANY_PENDING,
    ImageConfig,
    ImageService,
)
from llamora.app.services.key_rotation import reencrypt_images
from llamora.persistence.local_db import LocalDB
from storage import DAY, SIZES, Person

pytestmark = pytest.mark.anyio

REPO = Path(__file__).resolve().parents[2]
PHOTO = encode(halves((400, 200)), "JPEG")


async def upload(
    images: ImageService, person: Person, data: bytes = PHOTO, name="a.jpg"
):
    return await images.upload(person.ctx(), name, data)


async def write(db: LocalDB, person: Person, text: str = "An entry", **kwargs) -> str:
    return await db.entries.append_entry(
        person.ctx(), "user", text, created_date=DAY, **kwargs
    )


async def row(db: LocalDB, person: Person, image_id: str):
    return await db.images.get(person.id, image_id)


def files_of(images: ImageService, person: Person, image_id: str) -> list[Path]:
    return [images.store.path_for(person.id, image_id, v) for v in VARIANTS]


async def entry_count(db: LocalDB, person: Person) -> int:
    assert db.pool is not None
    async with db.pool.connection() as conn:
        cursor = await conn.execute(
            "SELECT COUNT(*) FROM entries WHERE user_id = ?", (person.id,)
        )
        (count,) = await cursor.fetchone()
    return int(count)


def later(seconds: float) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


@pytest.fixture
def roomy(db: LocalDB, tmp_path: Path) -> ImageService:
    """Larger variants, so a noisy full image spans several file chunks."""
    config = ImageConfig(
        root=tmp_path / "roomy", sizes={"thumb": 48, "display": 128, "full": 800}
    )
    return ImageService(db, BlobStore(config.root), config)


NOISY = encode(noise((800, 800)), "PNG")


# -- schema ---------------------------------------------------------------


async def test_migration_creates_the_images_table(db: LocalDB) -> None:
    with sqlite3.connect(db.db_path) as conn:
        columns = {r[1] for r in conn.execute("PRAGMA table_info(images)")}
        fks = {
            (r[2], r[3], r[6]) for r in conn.execute("PRAGMA foreign_key_list(images)")
        }
        indexes = {r[1] for r in conn.execute("PRAGMA index_list(images)")}
    assert {
        "id",
        "user_id",
        "entry_id",
        "position",
        "key_cipher",
        "meta_cipher",
        "alg",
        "attached_at",
        "created_at",
    } <= columns
    assert ("users", "user_id", "CASCADE") in fks
    assert ("entries", "entry_id", "SET NULL") in fks
    assert {"idx_images_entry", "idx_images_detached"} <= indexes


async def test_existing_database_is_upgraded(tmp_path: Path) -> None:
    only_first = tmp_path / "migrations-v1"
    only_first.mkdir()
    (only_first / "0001-init.sql").write_text(
        (REPO / "migrations/0001-init.sql").read_text()
    )
    path = tmp_path / "old.sqlite3"
    create_db(path)
    assert run_migrations(path, only_first, verbose=False)
    assert get_db_version(path) == 1

    database = LocalDB(str(path))
    await database.init()
    await database.close()

    assert get_db_version(path) == 2
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM images").fetchone() == (0,)


# -- upload ---------------------------------------------------------------


async def test_upload_stores_a_pending_image(
    images: ImageService, db: LocalDB, alice: Person
) -> None:
    record = await upload(images, alice, jpeg_with_gps(), name="../../IMG_0412.jpg")

    stored = await row(db, alice, record.id)
    assert stored is not None and stored.pending
    assert record.meta.filename == "IMG_0412.jpg"
    for variant in VARIANTS:
        meta = record.meta.variants[variant]
        assert meta.mime == "image/webp"
        assert max(meta.width, meta.height) <= SIZES[variant]
    # Only what queries need is in plaintext.
    with sqlite3.connect(db.db_path) as conn:
        dump = b"".join(
            bytes(str(v), "utf-8") if not isinstance(v, bytes) else v
            for r in conn.execute("SELECT * FROM images")
            for v in r
        )
    assert b"IMG_0412" not in dump and b"image/webp" not in dump


async def test_files_on_disk_reveal_nothing(
    images: ImageService, alice: Person
) -> None:
    source = jpeg_with_gps()
    record = await upload(images, alice, source, name="holiday-secret.jpg")
    root = images.store.root

    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    user_dir = root / user_dir_name(alice.id)
    assert user_dir.is_dir() and alice.id not in str(user_dir)
    for path in files_of(images, alice, record.id):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
        blob = path.read_bytes()
        assert source not in blob and source[:64] not in blob
        for signature in (b"RIFF", b"WEBP", b"\xff\xd8\xff", b"\x89PNG", b"Exif"):
            assert signature not in blob
        assert b"holiday-secret" not in blob
    names = [p.name for p in root.rglob("*")]
    assert not any("holiday" in name for name in names)


async def test_variants_round_trip(roomy: ImageService, alice: Person) -> None:
    images = roomy
    record = await upload(images, alice, NOISY)
    for variant in VARIANTS:
        opened = await images.open_variant(alice.ctx(), record.id, variant)
        assert opened is not None
        meta, stream = opened
        data = b"".join([chunk async for chunk in stream])
        assert len(data) == meta.bytes
        image = decode(data)
        assert image.format == "WEBP"
        assert (image.width, image.height) == (meta.width, meta.height)
    # The noisy full variant is big enough to span several file chunks.
    assert record.meta.variants["full"].bytes > CHUNK


async def test_rejected_uploads_leave_nothing_behind(
    images: ImageService, db: LocalDB, alice: Person
) -> None:
    with pytest.raises(ImageRejected):
        await upload(images, alice, b"not an image")
    assert await db.images.count_pending(alice.id) == 0
    assert not list(images.store.root.rglob("*.*"))


async def test_failed_insert_removes_the_files(
    images: ImageService, db: LocalDB, alice: Person, monkeypatch
) -> None:
    async def broken(*args, **kwargs):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(db.images, "insert_pending", broken)
    with pytest.raises(sqlite3.OperationalError):
        await upload(images, alice)
    assert not [p for p in images.store.root.rglob("*") if p.is_file()]


async def test_pending_uploads_are_capped(
    images: ImageService, image_config: ImageConfig, alice: Person, bob: Person
) -> None:
    small = encode(halves((20, 10)), "PNG")
    for _ in range(image_config.max_pending):
        await upload(images, alice, small)
    with pytest.raises(ImageRejected) as excinfo:
        await upload(images, alice, small)
    assert excinfo.value.reason == TOO_MANY_PENDING
    await upload(images, bob, small)  # per user


async def test_oversized_upload_is_rejected_before_decoding(
    db: LocalDB, alice: Person, tmp_path: Path
) -> None:
    config = ImageConfig(root=tmp_path / "small", max_upload_bytes=100, sizes=SIZES)
    service = ImageService(db, BlobStore(config.root), config)
    with pytest.raises(ImageRejected) as excinfo:
        await service.upload(alice.ctx(), "big.jpg", PHOTO)
    assert excinfo.value.reason == ImageRejected.TOO_LARGE


# -- reading --------------------------------------------------------------


async def test_other_users_and_unknown_variants_get_nothing(
    images: ImageService, alice: Person, bob: Person
) -> None:
    record = await upload(images, alice)
    assert await images.open_variant(bob.ctx(), record.id, "display") is None
    assert await images.get(bob.ctx(), record.id) is None
    assert await images.open_variant(alice.ctx(), record.id, "original") is None
    assert await images.open_variant(alice.ctx(), "01UNKNOWN", "display") is None


@pytest.mark.parametrize(
    "damage", ["flip-first", "flip-last", "truncate", "missing", "swap"]
)
async def test_damaged_files_are_refused(
    roomy: ImageService, alice: Person, damage: str
) -> None:
    images = roomy
    record = await upload(images, alice, NOISY)
    assert record.meta.variants["full"].bytes > 2 * CHUNK
    path = images.store.path_for(alice.id, record.id, "full")
    blob = bytearray(path.read_bytes())
    if damage == "flip-first":
        blob[40] ^= 1
    elif damage == "flip-last":
        blob[-3] ^= 1
    elif damage == "truncate":
        blob = blob[: len(blob) - 100]
    elif damage == "swap":
        blob = bytearray(
            images.store.path_for(alice.id, record.id, "thumb").read_bytes()
        )
    if damage == "missing":
        path.unlink()
    else:
        path.write_bytes(bytes(blob))

    with pytest.raises(ImageDecryptError):
        opened = await images.open_variant(alice.ctx(), record.id, "full")
        assert opened is not None
        _, stream = opened
        async for _ in stream:
            pass


async def test_damage_in_the_first_chunk_is_caught_before_streaming(
    images: ImageService, alice: Person
) -> None:
    # The image route relies on this to answer 404 instead of a broken 200.
    record = await upload(images, alice)
    path = images.store.path_for(alice.id, record.id, "display")
    blob = bytearray(path.read_bytes())
    blob[40] ^= 1
    path.write_bytes(bytes(blob))
    with pytest.raises(ImageDecryptError):
        await images.open_variant(alice.ctx(), record.id, "display")


async def test_wrong_dek_cannot_read(images: ImageService, alice: Person) -> None:
    record = await upload(images, alice)
    with pytest.raises(ImageDecryptError):
        await images.open_variant(alice.ctx(dek=generate_dek()), record.id, "thumb")


# -- linking to entries ---------------------------------------------------


async def test_images_attach_to_a_new_entry_in_order(
    images: ImageService, db: LocalDB, alice: Person
) -> None:
    first, second = await upload(images, alice), await upload(images, alice)
    entry_id = await write(db, alice, image_ids=[second.id, first.id], max_images=3)

    grouped = await db.images.get_for_entries(alice.id, [entry_id])
    assert [r.id for r in grouped[entry_id]] == [second.id, first.id]
    assert [r.position for r in grouped[entry_id]] == [0, 1]
    assert all(r.attached_at and not r.pending for r in grouped[entry_id])

    refs = await images.refs_for_entries(alice.ctx(), [entry_id])
    assert refs == {
        entry_id: [
            {"id": second.id, "width": 128, "height": 64},
            {"id": first.id, "width": 128, "height": 64},
        ]
    }


async def test_entries_without_images_have_no_refs(
    images: ImageService, db: LocalDB, alice: Person
) -> None:
    entry_id = await write(db, alice)
    assert await images.refs_for_entries(alice.ctx(), [entry_id]) == {}


@pytest.mark.parametrize(
    "problem", ["foreign", "attached", "unknown", "duplicate", "too-many"]
)
async def test_bad_image_lists_save_nothing(
    images: ImageService, db: LocalDB, alice: Person, bob: Person, problem: str
) -> None:
    mine = await upload(images, alice)
    if problem == "foreign":
        ids = [mine.id, (await upload(images, bob)).id]
    elif problem == "attached":
        other = await upload(images, alice)
        await write(db, alice, image_ids=[other.id])
        ids = [mine.id, other.id]
    elif problem == "unknown":
        ids = [mine.id, "01UNKNOWN"]
    elif problem == "duplicate":
        ids = [mine.id, mine.id]
    else:
        ids = [mine.id] + [(await upload(images, alice)).id for _ in range(3)]
    before = await entry_count(db, alice)

    with pytest.raises(ImageAttachError):
        await write(db, alice, image_ids=ids, max_images=3)

    assert await entry_count(db, alice) == before
    stored = await row(db, alice, mine.id)
    assert stored is not None and stored.pending


async def test_only_user_entries_have_images(
    images: ImageService, db: LocalDB, alice: Person
) -> None:
    image = await upload(images, alice)
    with pytest.raises(ImageAttachError):
        await db.entries.append_entry(
            alice.ctx(), "assistant", "A reply", created_date=DAY, image_ids=[image.id]
        )


async def test_update_replaces_the_images_and_their_order(
    images: ImageService, db: LocalDB, alice: Person
) -> None:
    a, b, c = [await upload(images, alice) for _ in range(3)]
    entry_id = await write(db, alice, image_ids=[a.id, b.id])

    await db.entries.update_entry_text(
        alice.ctx(), entry_id, "Edited", image_ids=[c.id, a.id], max_images=3
    )

    grouped = await db.images.get_for_entries(alice.id, [entry_id])
    assert [r.id for r in grouped[entry_id]] == [c.id, a.id]
    dropped = await row(db, alice, b.id)
    assert dropped is not None
    assert dropped.entry_id is None and dropped.attached_at is not None  # orphan


async def test_update_without_image_ids_leaves_them_alone(
    images: ImageService, db: LocalDB, alice: Person
) -> None:
    a = await upload(images, alice)
    entry_id = await write(db, alice, image_ids=[a.id])
    await db.entries.update_entry_text(alice.ctx(), entry_id, "Edited")
    grouped = await db.images.get_for_entries(alice.id, [entry_id])
    assert [r.id for r in grouped[entry_id]] == [a.id]


async def test_update_with_a_bad_list_changes_nothing(
    images: ImageService, db: LocalDB, alice: Person, bob: Person
) -> None:
    a = await upload(images, alice)
    entry_id = await write(db, alice, "Original", image_ids=[a.id])
    foreign = await upload(images, bob)

    with pytest.raises(ImageAttachError):
        await db.entries.update_entry_text(
            alice.ctx(), entry_id, "Edited", image_ids=[foreign.id]
        )

    grouped = await db.images.get_for_entries(alice.id, [entry_id])
    assert [r.id for r in grouped[entry_id]] == [a.id]
    entries = await db.entries.get_entries_by_ids(alice.ctx(), [entry_id])
    assert entries[0]["text"] == "Original"


# -- deleting and sweeping ------------------------------------------------


async def test_deleting_an_image(
    images: ImageService, db: LocalDB, alice: Person
) -> None:
    pending = await upload(images, alice)
    attached = await upload(images, alice)
    entry_id = await write(db, alice, image_ids=[attached.id])

    assert (await images.delete(alice.ctx(), pending.id)).entry_id is None
    removed = await images.delete(alice.ctx(), attached.id)
    assert removed is not None and removed.entry_id == entry_id

    for image in (pending, attached):
        assert await row(db, alice, image.id) is None
        assert not any(p.exists() for p in files_of(images, alice, image.id))


async def test_deleting_someone_elses_image_does_nothing(
    images: ImageService, db: LocalDB, alice: Person, bob: Person
) -> None:
    image = await upload(images, alice)
    assert await images.delete(bob.ctx(), image.id) is None
    assert await row(db, alice, image.id) is not None
    assert all(p.exists() for p in files_of(images, alice, image.id))


async def test_deleted_entry_orphans_its_images_and_the_sweep_removes_them(
    images: ImageService, db: LocalDB, alice: Person
) -> None:
    image = await upload(images, alice)
    entry_id = await write(db, alice, image_ids=[image.id])

    await db.entries.delete_entry(alice.id, entry_id)
    orphan = await row(db, alice, image.id)
    assert orphan is not None and orphan.entry_id is None and orphan.attached_at

    result = await images.sweep()

    assert result.rows == 1
    assert await row(db, alice, image.id) is None
    assert not any(p.exists() for p in files_of(images, alice, image.id))


async def test_pending_uploads_expire_but_attached_images_never_do(
    images: ImageService, db: LocalDB, alice: Person, image_config: ImageConfig
) -> None:
    pending = await upload(images, alice)
    attached = await upload(images, alice)
    await write(db, alice, image_ids=[attached.id])

    assert (await images.sweep()).rows == 0
    assert await row(db, alice, pending.id) is not None

    result = await images.sweep(now=later(image_config.pending_ttl + 60))

    assert result.rows == 1
    assert await row(db, alice, pending.id) is None
    assert not any(p.exists() for p in files_of(images, alice, pending.id))
    assert await row(db, alice, attached.id) is not None
    assert all(p.exists() for p in files_of(images, alice, attached.id))


async def test_sweep_removes_stray_files_once_they_are_old(
    images: ImageService, alice: Person, image_config: ImageConfig
) -> None:
    # A crash between writing files and inserting the row, and a leftover
    # temporary file from an interrupted write.
    key = os.urandom(32)
    await images.store.write(alice.id, "01CRASHED", "thumb", key, b"pixels")
    stray = images.store.path_for(alice.id, "01CRASHED", "thumb")
    tmp = stray.with_name("01HALFWRITTEN.full.tmp")
    tmp.write_bytes(b"partial")
    kept = await upload(images, alice)

    assert (await images.sweep()).stray_files == 0  # too recent: maybe in flight
    assert stray.exists() and tmp.exists()

    result = await images.sweep(now=later(image_config.pending_ttl + 60))

    assert result.stray_files == 2
    assert not stray.exists() and not tmp.exists()
    assert result.rows == 1  # the pending upload expired too
    assert not any(p.exists() for p in files_of(images, alice, kept.id))


async def test_deleting_a_user_removes_rows_and_files(
    images: ImageService, db: LocalDB, alice: Person, bob: Person
) -> None:
    mine = await upload(images, alice)
    await write(db, alice, image_ids=[mine.id])
    theirs = await upload(images, bob)

    await db.users.delete_user(alice.id)
    await images.delete_user_files(alice.id)

    assert await row(db, alice, mine.id) is None
    assert not (images.store.root / user_dir_name(alice.id)).exists()
    assert await row(db, bob, theirs.id) is not None
    assert all(p.exists() for p in files_of(images, bob, theirs.id))


# -- key rotation ---------------------------------------------------------


async def test_key_rotation_rewraps_keys_and_leaves_files_alone(
    images: ImageService, db: LocalDB, alice: Person
) -> None:
    record = await upload(images, alice)
    before = {
        p: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in files_of(images, alice, record.id)
    }
    new_dek = generate_dek()

    count = await reencrypt_images(db, alice.id, alice.dek, new_dek, 2)

    assert count == 1
    stored = await row(db, alice, record.id)
    assert stored is not None and stored.alg.endswith(b";e=2")
    new_ctx = alice.ctx(dek=new_dek, epoch=2)
    assert (await images.get(new_ctx, record.id)).meta == record.meta
    opened = await images.open_variant(new_ctx, record.id, "display")
    assert opened is not None
    assert decode(b"".join([c async for c in opened[1]])).format == "WEBP"
    with pytest.raises(ImageDecryptError):
        await images.open_variant(alice.ctx(), record.id, "display")
    with pytest.raises(CryptoError):
        alice.ctx().unwrap_image_key(
            stored.id, stored.key_nonce, stored.key_cipher, stored.alg
        )
    assert before == {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in before}
    assert await reencrypt_images(db, alice.id, alice.dek, new_dek, 2) == 0


# -- configuration --------------------------------------------------------


def test_config_from_settings(tmp_path: Path, monkeypatch) -> None:
    from llamora.settings import settings

    monkeypatch.chdir(tmp_path)
    config = ImageConfig.from_settings(settings)
    assert config.root == (tmp_path / "images").resolve()
    assert config.max_upload_bytes == 20 * 1024 * 1024  # "20MiB" in settings.toml
    assert dict(config.sizes) == {"thumb": 480, "display": 2048, "full": 4096}
    assert config.max_pending == 4 * config.max_per_entry
