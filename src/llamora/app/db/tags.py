from __future__ import annotations

from typing import Any, Sequence

from aiosqlitepool import SQLiteConnectionPool
from ulid import ULID

from .base import BaseRepository
from .events import RepositoryEventBus, ENTRY_TAGS_CHANGED_EVENT
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

    async def xref_tag_entry(
        self, user_id: str, tag_hash: bytes, entry_id: str
    ) -> None:
        async with self.pool.connection() as conn:
            changed = False

            async def _tx():
                nonlocal changed
                cursor = await conn.execute(
                    "INSERT OR IGNORE INTO tag_entry_xref (user_id, tag_hash, entry_id, ulid) VALUES (?, ?, ?, ?)",
                    (user_id, tag_hash, entry_id, str(ULID())),
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
                ENTRY_TAGS_CHANGED_EVENT,
                user_id=user_id,
                entry_id=entry_id,
                tag_hash=tag_hash,
            )

    async def unlink_tag_entry(
        self, user_id: str, tag_hash: bytes, entry_id: str
    ) -> None:
        async with self.pool.connection() as conn:
            changed = False

            async def _tx():
                nonlocal changed
                cursor = await conn.execute(
                    "DELETE FROM tag_entry_xref WHERE user_id = ? AND tag_hash = ? AND entry_id = ?",
                    (user_id, tag_hash, entry_id),
                )
                if cursor.rowcount:
                    changed = True

            await self._run_in_transaction(conn, _tx)

        if changed and self._event_bus:
            await self._event_bus.emit(
                ENTRY_TAGS_CHANGED_EVENT,
                user_id=user_id,
                entry_id=entry_id,
                tag_hash=tag_hash,
            )

    async def get_tags_for_entry(
        self, user_id: str, entry_id: str, dek: bytes
    ) -> list[dict]:
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                """
                SELECT t.tag_hash, t.name_ct, t.name_nonce, t.alg AS tag_alg
                FROM tag_entry_xref x
                JOIN tags t ON t.user_id = x.user_id AND t.tag_hash = x.tag_hash
                WHERE x.user_id = ? AND x.entry_id = ?
                ORDER BY x.ulid ASC
                """,
                (user_id, entry_id),
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

    async def get_tags_for_entries(
        self,
        user_id: str,
        entry_ids: Sequence[str],
        dek: bytes,
    ) -> dict[str, list[dict]]:
        """Return decrypted tags for each entry in ``entry_ids``."""

        ids = [eid for eid in entry_ids if eid]
        if not ids:
            return {}

        placeholders = ",".join("?" for _ in ids)
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                f"""
                SELECT x.entry_id,
                       t.tag_hash,
                       t.name_ct,
                       t.name_nonce,
                       t.alg AS tag_alg,
                       x.ulid
                FROM tag_entry_xref x
                JOIN tags t
                  ON t.user_id = x.user_id AND t.tag_hash = x.tag_hash
                WHERE x.user_id = ? AND x.entry_id IN ({placeholders})
                ORDER BY x.entry_id ASC, x.ulid ASC
                """,
                (user_id, *ids),
            )
            rows = await cursor.fetchall()

        mapping: dict[str, list[dict]] = {}
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
            entry_id = row["entry_id"]
            mapping.setdefault(entry_id, []).append(
                {"name": tag_name, "hash": row["tag_hash"].hex()}
            )
        return mapping

    async def get_tag_info(
        self, user_id: str, tag_hash: bytes, dek: bytes
    ) -> dict[str, Any] | None:
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                """
                SELECT t.tag_hash,
                       t.name_ct,
                       t.name_nonce,
                       t.alg,
                       (
                           SELECT COUNT(*)
                           FROM tag_entry_xref x
                           JOIN entries e
                             ON e.user_id = x.user_id AND e.id = x.entry_id
                           WHERE x.user_id = t.user_id
                             AND x.tag_hash = t.tag_hash
                       ) AS seen_count,
                       (
                           SELECT MAX(e.created_at)
                           FROM tag_entry_xref x
                           JOIN entries e
                             ON e.user_id = x.user_id AND e.id = x.entry_id
                            WHERE x.user_id = t.user_id
                              AND x.tag_hash = t.tag_hash
                       ) AS last_used
                FROM tags t
                WHERE t.user_id = ? AND t.tag_hash = ?
                """,
                (user_id, tag_hash),
            )
            row = await cursor.fetchone()

        if not row:
            return None

        tag_name = cached_tag_name(
            user_id,
            row["tag_hash"],
            row["name_nonce"],
            row["name_ct"],
            row["alg"].encode(),
            dek,
            self._decrypt_message,
        )

        return {
            "name": tag_name,
            "hash": row["tag_hash"].hex(),
            "count": row["seen_count"],
            "last_used": row["last_used"],
        }

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

    async def get_tag_match_counts(
        self, user_id: str, tag_hashes: Sequence[bytes], entry_ids: Sequence[str]
    ) -> dict[str, int]:
        """Return the number of matching tag hashes for each ``entry_id``."""

        tags = [digest for digest in tag_hashes if digest]
        ids = [eid for eid in entry_ids if eid]
        if not tags or not ids:
            return {}

        tag_placeholders = ",".join("?" * len(tags))
        entry_placeholders = ",".join("?" * len(ids))
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                f"""
                SELECT entry_id, COUNT(*) AS match_count
                FROM tag_entry_xref
                WHERE user_id = ?
                  AND tag_hash IN ({tag_placeholders})
                  AND entry_id IN ({entry_placeholders})
                GROUP BY entry_id
                """,
                (user_id, *tags, *ids),
            )
            rows = await cursor.fetchall()

        return {row["entry_id"]: int(row["match_count"]) for row in rows}

    async def get_recent_entries_for_tag_hashes(
        self,
        user_id: str,
        tag_hashes: list[bytes],
        *,
        limit: int | None = None,
        max_entry_id: str | None = None,
        max_created_at: str | None = None,
    ) -> list[str]:
        """Return recent entry IDs associated with any of ``tag_hashes``.

        Optional cutoff values limit matches to entries at-or-before the entry.
        """

        if not tag_hashes:
            return []
        if limit is not None and limit <= 0:
            return []

        tag_placeholders = ",".join("?" * len(tag_hashes))
        joins = "JOIN entries m ON m.user_id = x.user_id AND m.id = x.entry_id"
        conditions = [f"x.user_id = ? AND x.tag_hash IN ({tag_placeholders})"]
        params: list[object] = [user_id, *tag_hashes]

        if max_entry_id or max_created_at:
            if max_entry_id:
                conditions.append("m.id <= ?")
                params.append(max_entry_id)
            if max_created_at:
                conditions.append("m.created_at <= ?")
                params.append(max_created_at)

        where_clause = " AND ".join(conditions)
        sql = f"""
            SELECT x.entry_id, MAX(x.ulid) AS latest_ulid
            FROM tag_entry_xref x
            {joins}
            WHERE {where_clause}
            GROUP BY x.entry_id
            ORDER BY latest_ulid DESC
        """
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)

        async with self.pool.connection() as conn:
            cursor = await conn.execute(sql, params)
            rows = await cursor.fetchall()

        return [row["entry_id"] for row in rows]

    async def get_recent_entries_page_for_tag_hashes(
        self,
        user_id: str,
        tag_hashes: list[bytes],
        *,
        limit: int,
        before_created_at: str | None = None,
        before_entry_id: str | None = None,
        max_entry_id: str | None = None,
        max_created_at: str | None = None,
    ) -> tuple[list[str], str | None, bool]:
        if not tag_hashes:
            return [], None, False
        if limit <= 0:
            return [], None, False

        tag_placeholders = ",".join("?" * len(tag_hashes))
        params: list[object] = [user_id, *tag_hashes]
        conditions = [
            f"x.user_id = ? AND x.tag_hash IN ({tag_placeholders})",
            "m.created_at IS NOT NULL",
            "m.created_at != ''",
        ]
        if max_entry_id:
            conditions.append("m.id <= ?")
            params.append(max_entry_id)
        if max_created_at:
            conditions.append("m.created_at <= ?")
            params.append(max_created_at)

        where_clause = " AND ".join(conditions)
        params.append(before_created_at)
        params.append(before_created_at)
        params.append(before_created_at)
        params.append(before_entry_id)
        fetch_limit = limit + 1
        params.append(fetch_limit)

        sql = f"""
            WITH ranked AS (
                SELECT x.entry_id, MAX(m.created_at) AS created_at
                FROM tag_entry_xref x
                JOIN entries m
                  ON m.user_id = x.user_id AND m.id = x.entry_id
                WHERE {where_clause}
                GROUP BY x.entry_id
            )
            SELECT entry_id, created_at
            FROM ranked
            WHERE (
                ? IS NULL
                OR created_at < ?
                OR (created_at = ? AND entry_id < ?)
            )
            ORDER BY created_at DESC, entry_id DESC
            LIMIT ?
        """

        async with self.pool.connection() as conn:
            cursor = await conn.execute(sql, params)
            rows = await cursor.fetchall()

        has_more = len(rows) > limit
        page_rows = rows[:limit]
        entry_ids = [row["entry_id"] for row in page_rows]
        next_cursor = (
            f"{page_rows[-1]['created_at']}|{page_rows[-1]['entry_id']}"
            if has_more
            else None
        )
        return entry_ids, next_cursor, has_more
