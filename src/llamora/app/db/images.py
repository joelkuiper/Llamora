"""Image rows: wrapped file keys, encrypted metadata and the entry link.

The files themselves are on disk (``services.images.blob_store``). Linking
images to an entry happens inside the entries repository's own transactions
(:func:`attach_on`, :func:`replace_on`), so an entry and its images are saved
or rejected together.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from llamora.app.db.base import BaseRepository

_COLUMNS = (
    "id, user_id, entry_id, position, key_nonce, key_cipher, "
    "meta_nonce, meta_cipher, alg, attached_at, created_at"
)


class ImageAttachError(ValueError):
    """Images that cannot be linked to an entry; the whole save is rolled back."""


@dataclass(frozen=True, slots=True)
class ImageRow:
    id: str
    user_id: str
    entry_id: str | None
    position: int
    key_nonce: bytes
    key_cipher: bytes
    meta_nonce: bytes
    meta_cipher: bytes
    alg: bytes
    attached_at: str | None
    created_at: str

    @property
    def pending(self) -> bool:
        return self.entry_id is None and self.attached_at is None

    @classmethod
    def from_row(cls, row: Any) -> ImageRow:
        alg = row["alg"]
        return cls(
            id=row["id"],
            user_id=row["user_id"],
            entry_id=row["entry_id"],
            position=int(row["position"]),
            key_nonce=row["key_nonce"],
            key_cipher=row["key_cipher"],
            meta_nonce=row["meta_nonce"],
            meta_cipher=row["meta_cipher"],
            alg=alg.encode("utf-8") if isinstance(alg, str) else alg,
            attached_at=row["attached_at"],
            created_at=row["created_at"],
        )


def _placeholders(values: Sequence[Any]) -> str:
    return ",".join("?" for _ in values)


def _check_request(image_ids: Sequence[str], max_per_entry: int | None) -> list[str]:
    ids = [str(image_id) for image_id in image_ids]
    if len(set(ids)) != len(ids):
        raise ImageAttachError("the same image is listed twice")
    if max_per_entry is not None and len(ids) > max_per_entry:
        raise ImageAttachError(f"an entry can have at most {max_per_entry} images")
    return ids


async def _owned(conn, user_id: str, ids: Sequence[str]) -> dict[str, Any]:
    if not ids:
        return {}
    cursor = await conn.execute(
        f"SELECT id, entry_id, attached_at FROM images "
        f"WHERE user_id = ? AND id IN ({_placeholders(ids)})",
        (user_id, *ids),
    )
    rows = await cursor.fetchall()
    await cursor.close()
    return {row["id"]: row for row in rows}


async def _link(conn, user_id: str, entry_id: str, ids: Sequence[str]) -> None:
    for position, image_id in enumerate(ids):
        await conn.execute(
            """
            UPDATE images
            SET entry_id = ?, position = ?,
                attached_at = COALESCE(attached_at, CURRENT_TIMESTAMP)
            WHERE user_id = ? AND id = ?
            """,
            (entry_id, position, user_id, image_id),
        )


async def attach_on(
    conn,
    *,
    user_id: str,
    entry_id: str,
    image_ids: Sequence[str],
    max_per_entry: int | None = None,
) -> None:
    """Link pending images to a new entry, in order. Runs in the caller's tx."""

    ids = _check_request(image_ids, max_per_entry)
    owned = await _owned(conn, user_id, ids)
    for image_id in ids:
        row = owned.get(image_id)
        if row is None:
            raise ImageAttachError(f"unknown image {image_id}")
        if row["entry_id"] is not None or row["attached_at"] is not None:
            raise ImageAttachError(f"image {image_id} is not pending")
    await _link(conn, user_id, entry_id, ids)


async def replace_on(
    conn,
    *,
    user_id: str,
    entry_id: str,
    image_ids: Sequence[str],
    max_per_entry: int | None = None,
) -> list[str]:
    """Make ``image_ids`` the entry's images, in order. Runs in the caller's tx.

    Images already on the entry may stay; others must be pending. Images
    left out are detached and become orphans for the sweeper. Returns the
    ids that were detached.
    """

    ids = _check_request(image_ids, max_per_entry)
    owned = await _owned(conn, user_id, ids)
    for image_id in ids:
        row = owned.get(image_id)
        if row is None:
            raise ImageAttachError(f"unknown image {image_id}")
        if row["entry_id"] != entry_id and (
            row["entry_id"] is not None or row["attached_at"] is not None
        ):
            raise ImageAttachError(f"image {image_id} is not pending")

    cursor = await conn.execute(
        "SELECT id FROM images WHERE user_id = ? AND entry_id = ?",
        (user_id, entry_id),
    )
    current = [row["id"] for row in await cursor.fetchall()]
    await cursor.close()
    detached = [image_id for image_id in current if image_id not in set(ids)]
    if detached:
        await conn.execute(
            f"UPDATE images SET entry_id = NULL "
            f"WHERE user_id = ? AND id IN ({_placeholders(detached)})",
            (user_id, *detached),
        )
    await _link(conn, user_id, entry_id, ids)
    return detached


class ImagesRepository(BaseRepository):
    async def insert_pending(
        self,
        user_id: str,
        image_id: str,
        *,
        key_nonce: bytes,
        key_cipher: bytes,
        meta_nonce: bytes,
        meta_cipher: bytes,
        alg: bytes,
    ) -> None:
        async with self.pool.connection() as conn:
            await self._run_in_transaction(
                conn,
                conn.execute,
                """
                INSERT INTO images (
                    id, user_id, key_nonce, key_cipher, meta_nonce, meta_cipher, alg
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    image_id,
                    user_id,
                    key_nonce,
                    key_cipher,
                    meta_nonce,
                    meta_cipher,
                    alg.decode("utf-8"),
                ),
            )

    async def get(self, user_id: str, image_id: str) -> ImageRow | None:
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                f"SELECT {_COLUMNS} FROM images WHERE id = ? AND user_id = ?",
                (image_id, user_id),
            )
            row = await cursor.fetchone()
            await cursor.close()
        return ImageRow.from_row(row) if row else None

    async def count_pending(self, user_id: str) -> int:
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                """
                SELECT COUNT(*) FROM images
                WHERE user_id = ? AND entry_id IS NULL AND attached_at IS NULL
                """,
                (user_id,),
            )
            row = await cursor.fetchone()
            await cursor.close()
        return int(row[0]) if row else 0

    async def get_for_entries(
        self, user_id: str, entry_ids: Iterable[str]
    ) -> dict[str, list[ImageRow]]:
        ids = list(dict.fromkeys(entry_ids))
        if not ids:
            return {}
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                f"""
                SELECT {_COLUMNS} FROM images
                WHERE user_id = ? AND entry_id IN ({_placeholders(ids)})
                ORDER BY entry_id, position, id
                """,
                (user_id, *ids),
            )
            rows = await cursor.fetchall()
            await cursor.close()
        grouped: dict[str, list[ImageRow]] = {}
        for row in rows:
            image = ImageRow.from_row(row)
            grouped.setdefault(str(image.entry_id), []).append(image)
        return grouped

    async def count_for_entry(self, user_id: str, entry_id: str) -> int:
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                "SELECT COUNT(*) FROM images WHERE user_id = ? AND entry_id = ?",
                (user_id, entry_id),
            )
            row = await cursor.fetchone()
            await cursor.close()
        return int(row[0]) if row else 0

    async def delete(self, user_id: str, image_id: str) -> ImageRow | None:
        """Delete one image row; returns it (for its entry), or None."""

        async with self.pool.connection() as conn:

            async def _delete():
                cursor = await conn.execute(
                    f"DELETE FROM images WHERE id = ? AND user_id = ? "
                    f"RETURNING {_COLUMNS}",
                    (image_id, user_id),
                )
                row = await cursor.fetchone()
                await cursor.close()
                return row

            row = await self._run_in_transaction(conn, _delete)
        return ImageRow.from_row(row) if row else None

    async def collectable(
        self, *, pending_before: str, limit: int = 500
    ) -> list[tuple[str, str]]:
        """(user_id, image_id) of orphans, and of pending images created
        before ``pending_before`` (``YYYY-MM-DD HH:MM:SS``, UTC)."""

        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                """
                SELECT user_id, id FROM images
                WHERE entry_id IS NULL
                  AND (attached_at IS NOT NULL OR created_at < ?)
                LIMIT ?
                """,
                (pending_before, limit),
            )
            rows = await cursor.fetchall()
            await cursor.close()
        return [(row["user_id"], row["id"]) for row in rows]

    async def delete_detached(self, image_ids: Sequence[str]) -> list[str]:
        """Delete rows that are still detached; returns the ids deleted.

        Re-checks ``entry_id IS NULL`` so an image attached since it was
        listed as collectable is left alone.
        """

        ids = list(image_ids)
        if not ids:
            return []
        async with self.pool.connection() as conn:

            async def _delete():
                cursor = await conn.execute(
                    f"DELETE FROM images WHERE entry_id IS NULL "
                    f"AND id IN ({_placeholders(ids)}) RETURNING id",
                    tuple(ids),
                )
                rows = await cursor.fetchall()
                await cursor.close()
                return [row["id"] for row in rows]

            return await self._run_in_transaction(conn, _delete)

    async def existing_ids(self, image_ids: Iterable[str]) -> set[str]:
        ids = list(dict.fromkeys(image_ids))
        found: set[str] = set()
        async with self.pool.connection() as conn:
            for start in range(0, len(ids), 500):
                batch = ids[start : start + 500]
                cursor = await conn.execute(
                    f"SELECT id FROM images WHERE id IN ({_placeholders(batch)})",
                    tuple(batch),
                )
                found.update(row["id"] for row in await cursor.fetchall())
                await cursor.close()
        return found

    async def rows_not_at_epoch(
        self, user_id: str, epoch: int, *, limit: int = 100
    ) -> list[ImageRow]:
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                f"SELECT {_COLUMNS} FROM images "
                f"WHERE user_id = ? AND alg NOT LIKE ? LIMIT ?",
                (user_id, f"%;e={epoch}", limit),
            )
            rows = await cursor.fetchall()
            await cursor.close()
        return [ImageRow.from_row(row) for row in rows]

    async def update_wraps(
        self,
        user_id: str,
        updates: Sequence[tuple[str, bytes, bytes, bytes, bytes, bytes]],
    ) -> None:
        """Store re-wrapped keys/meta: (id, key_nonce, key_cipher, meta_nonce,
        meta_cipher, alg) per image."""

        if not updates:
            return
        async with self.pool.connection() as conn:

            async def _update():
                for image_id, kn, kc, mn, mc, alg in updates:
                    await conn.execute(
                        """
                        UPDATE images
                        SET key_nonce = ?, key_cipher = ?,
                            meta_nonce = ?, meta_cipher = ?, alg = ?
                        WHERE id = ? AND user_id = ?
                        """,
                        (kn, kc, mn, mc, alg.decode("utf-8"), image_id, user_id),
                    )

            await self._run_in_transaction(conn, _update)
