from __future__ import annotations

from typing import Any

from aiosqlitepool import SQLiteConnectionPool
from ulid import ULID

from .base import BaseRepository
from .events import RepositoryEventBus, MESSAGE_TAGS_CHANGED_EVENT
from .utils import cached_tag_name
from llamora.app.util.tags import canonicalize, tag_hash
from llamora.app.util.frecency import DEFAULT_FRECENCY_DECAY, resolve_frecency_lambda


class TagsRepository(BaseRepository):
    """Operations for encrypted tag metadata and associations."""

    def __init__(
        self,
        pool: SQLiteConnectionPool,
        encrypt_message,
        decrypt_message,
        event_bus: RepositoryEventBus | None = None,
    ) -> None:
        super().__init__(pool)
        self._encrypt_message = encrypt_message
        self._decrypt_message = decrypt_message
        self._event_bus = event_bus

    async def resolve_or_create_tag(
        self, user_id: str, tag_name: str, dek: bytes
    ) -> bytes:
        canonical = canonicalize(tag_name)
        digest = tag_hash(user_id, canonical)
        async with self.pool.connection() as conn:

            async def _tx():
                cursor = await conn.execute(
                    """
                    INSERT INTO tags (user_id, tag_hash, name_ct, name_nonce, alg)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(user_id, tag_hash) DO NOTHING
                    """,
                    (user_id, digest, b"", b"", ""),
                )
                if cursor.rowcount:
                    nonce, ct, alg = self._encrypt_message(
                        dek, user_id, digest.hex(), canonical
                    )
                    await conn.execute(
                        """
                        UPDATE tags
                        SET name_ct = ?, name_nonce = ?, alg = ?
                        WHERE user_id = ? AND tag_hash = ?
                        """,
                        (ct, nonce, alg.decode(), user_id, digest),
                    )

            await self._run_in_transaction(conn, _tx)
        return digest

    async def xref_tag_message(
        self, user_id: str, tag_hash: bytes, message_id: str
    ) -> None:
        async with self.pool.connection() as conn:
            changed = False

            async def _tx():
                nonlocal changed
                cursor = await conn.execute(
                    "INSERT OR IGNORE INTO tag_message_xref (user_id, tag_hash, message_id, ulid) VALUES (?, ?, ?, ?)",
                    (user_id, tag_hash, message_id, str(ULID())),
                )
                if cursor.rowcount:
                    changed = True
                    await conn.execute(
                        "UPDATE tags SET seen = seen + 1, last_seen = CURRENT_TIMESTAMP WHERE user_id = ? AND tag_hash = ?",
                        (user_id, tag_hash),
                    )

            await self._run_in_transaction(conn, _tx)

        if changed and self._event_bus:
            await self._event_bus.emit(
                MESSAGE_TAGS_CHANGED_EVENT,
                user_id=user_id,
                message_id=message_id,
            )

    async def unlink_tag_message(
        self, user_id: str, tag_hash: bytes, message_id: str
    ) -> None:
        async with self.pool.connection() as conn:
            changed = False

            async def _tx():
                nonlocal changed
                cursor = await conn.execute(
                    "DELETE FROM tag_message_xref WHERE user_id = ? AND tag_hash = ? AND message_id = ?",
                    (user_id, tag_hash, message_id),
                )
                if cursor.rowcount:
                    changed = True
                    await conn.execute(
                        "UPDATE tags SET seen = CASE WHEN seen > 0 THEN seen - 1 ELSE 0 END WHERE user_id = ? AND tag_hash = ?",
                        (user_id, tag_hash),
                    )

            await self._run_in_transaction(conn, _tx)

        if changed and self._event_bus:
            await self._event_bus.emit(
                MESSAGE_TAGS_CHANGED_EVENT,
                user_id=user_id,
                message_id=message_id,
            )

    async def get_tags_for_message(
        self, user_id: str, message_id: str, dek: bytes
    ) -> list[dict]:
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                """
                SELECT t.tag_hash, t.name_ct, t.name_nonce, t.alg AS tag_alg
                FROM tag_message_xref x
                JOIN tags t ON t.user_id = x.user_id AND t.tag_hash = x.tag_hash
                WHERE x.user_id = ? AND x.message_id = ?
                ORDER BY x.ulid ASC
                """,
                (user_id, message_id),
            )
            rows = await cursor.fetchall()

        tags: list[dict] = []
        for row in rows:
            tag_name = cached_tag_name(
                user_id,
                row["tag_hash"],
                row["name_nonce"],
                row["name_ct"],
                row["tag_alg"].encode(),
                dek,
                self._decrypt_message,
            )
            tags.append({"name": tag_name, "hash": row["tag_hash"].hex()})
        return tags

    async def get_tag_frecency(
        self, user_id: str, limit: int, lambda_: Any, dek: bytes
    ) -> list[dict]:
        decay_constant = resolve_frecency_lambda(
            lambda_, default=DEFAULT_FRECENCY_DECAY
        )
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                """
                SELECT tag_hash, name_ct, name_nonce, alg,
                       seen AS frequency,
                       (julianday('now') - julianday(last_seen)) * 86400 AS recency,
                       seen / exp(? * (julianday('now') - julianday(last_seen)) * 86400) AS frecency
                FROM tags
                WHERE user_id = ?
                ORDER BY frecency DESC
                LIMIT ?
                """,
                (decay_constant, user_id, limit),
            )
            rows = await cursor.fetchall()

        tags: list[dict] = []
        for row in rows:
            tag_name = cached_tag_name(
                user_id,
                row["tag_hash"],
                row["name_nonce"],
                row["name_ct"],
                row["alg"].encode(),
                dek,
                self._decrypt_message,
            )
            tags.append(
                {
                    "name": tag_name,
                    "hash": row["tag_hash"].hex(),
                    "frequency": row["frequency"],
                    "recency": row["recency"],
                    "frecency": row["frecency"],
                }
            )
        return tags

    async def search_tags(
        self,
        user_id: str,
        dek: bytes,
        *,
        limit: int = 15,
        prefix: str | None = None,
        lambda_: Any = DEFAULT_FRECENCY_DECAY,
        exclude_names: set[str] | None = None,
    ) -> list[dict]:
        """Return recent/frequent tags optionally filtered by a prefix."""

        if limit <= 0:
            return []

        normalized_prefix = (prefix or "").strip()
        if normalized_prefix.startswith("#"):
            normalized_prefix = normalized_prefix[1:]
        prefix_lower = normalized_prefix.lower()

        excluded: set[str] = set()
        if exclude_names:
            for name in exclude_names:
                if not name:
                    continue
                trimmed = name.strip().lower()
                if not trimmed:
                    continue
                excluded.add(trimmed)

        batch_size = max(limit * 3, 25) if prefix_lower else max(limit * 2, 25)
        seen: set[str] = set()
        results: list[dict] = []

        decay_constant = resolve_frecency_lambda(
            lambda_, default=DEFAULT_FRECENCY_DECAY
        )

        async with self.pool.connection() as conn:
            offset = 0
            while len(results) < limit:
                cursor = await conn.execute(
                    """
                    SELECT tag_hash, name_ct, name_nonce, alg,
                           seen AS frequency,
                           last_seen,
                           seen / exp(? * (julianday('now') - julianday(last_seen)) * 86400) AS frecency
                    FROM tags
                    WHERE user_id = ?
                    ORDER BY frecency DESC, last_seen DESC
                    LIMIT ? OFFSET ?
                    """,
                    (decay_constant, user_id, batch_size, offset),
                )
                rows = await cursor.fetchall()
                if not rows:
                    break
                offset += batch_size

                for row in rows:
                    tag_name = cached_tag_name(
                        user_id,
                        row["tag_hash"],
                        row["name_nonce"],
                        row["name_ct"],
                        row["alg"].encode(),
                        dek,
                        self._decrypt_message,
                    )
                    tag_name = (tag_name or "").strip()
                    if not tag_name:
                        continue

                    canonical = tag_name
                    normalized = canonical.lower()

                    if normalized in excluded:
                        continue
                    if normalized in seen:
                        continue

                    if prefix_lower:
                        if not normalized.startswith(prefix_lower):
                            continue

                    results.append(
                        {
                            "name": canonical,
                            "hash": row["tag_hash"].hex(),
                            "frequency": row["frequency"],
                            "last_seen": row["last_seen"],
                            "frecency": row["frecency"],
                        }
                    )
                    seen.add(normalized)

                    if len(results) >= limit:
                        break

                if len(rows) < batch_size:
                    break

        return results

    async def get_messages_with_tag_hashes(
        self, user_id: str, tag_hashes: list[bytes], message_ids: list[str]
    ) -> dict[str, set[bytes]]:
        if not tag_hashes or not message_ids:
            return {}
        tag_placeholders = ",".join("?" * len(tag_hashes))
        msg_placeholders = ",".join("?" * len(message_ids))
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                f"""
                SELECT message_id, tag_hash FROM tag_message_xref
                WHERE user_id = ? AND tag_hash IN ({tag_placeholders}) AND message_id IN ({msg_placeholders})
                """,
                (user_id, *tag_hashes, *message_ids),
            )
            rows = await cursor.fetchall()
        mapping: dict[str, set[bytes]] = {}
        for row in rows:
            mapping.setdefault(row["message_id"], set()).add(row["tag_hash"])
        return mapping

    async def get_recent_messages_for_tag_hashes(
        self,
        user_id: str,
        tag_hashes: list[bytes],
        *,
        limit: int | None = None,
    ) -> list[str]:
        """Return recent message IDs associated with any of ``tag_hashes``."""

        if not tag_hashes:
            return []
        if limit is not None and limit <= 0:
            return []

        tag_placeholders = ",".join("?" * len(tag_hashes))
        sql = f"""
            SELECT message_id, MAX(ulid) AS latest_ulid
            FROM tag_message_xref
            WHERE user_id = ? AND tag_hash IN ({tag_placeholders})
            GROUP BY message_id
            ORDER BY latest_ulid DESC
        """
        params: list[object] = [user_id, *tag_hashes]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)

        async with self.pool.connection() as conn:
            cursor = await conn.execute(sql, params)
            rows = await cursor.fetchall()

        return [row["message_id"] for row in rows]
